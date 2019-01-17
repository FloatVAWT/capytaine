#!/usr/bin/env python
# coding: utf-8

import logging
from itertools import accumulate
from functools import wraps

import numpy as np

from capytaine.mesh.meshes_collection import CollectionOfMeshes
from capytaine.mesh.symmetries import ReflectionSymmetry, TranslationalSymmetry, AxialSymmetry
from capytaine.matrices.block_matrices import BlockMatrix
from capytaine.matrices.low_rank_blocks import LowRankMatrix
from capytaine.matrices.block_toeplitz_matrices import (
    BlockToeplitzMatrix, BlockSymmetricToeplitzMatrix,
    BlockCirculantMatrix, EvenBlockSymmetricCirculantMatrix, OddBlockSymmetricCirculantMatrix
)

LOG = logging.getLogger(__name__)


def cut_matrix(full_matrix, x_shapes, y_shapes, check=False):
    """Transform a numpy array into a block matrix of numpy arrays.

    Parameters
    ----------
    full_matrix: numpy array
        The matrix to split into blocks.
    x_shapes: sequence of int
        The columns at which to split the blocks.
    y_shapes: sequence of int
        The lines at which to split the blocks.
    check: bool, optional
        Check to dimensions and type of the matrix after creation (default: False).

    Return
    ------
    BlockMatrix
        The same matrix as the input one but in block form.
    """
    new_block_matrix = []
    for i, di in zip(accumulate([0] + x_shapes[:-1]), x_shapes):
        line = []
        for j, dj in zip(accumulate([0] + x_shapes[:-1]), y_shapes):
            line.append(full_matrix[i:i+di, j:j+dj])
        new_block_matrix.append(line)
    return BlockMatrix(new_block_matrix, check=check)


def random_block_matrix(x_shapes, y_shapes):
    """A random block matrix."""
    return cut_matrix(np.random.rand(sum(x_shapes), sum(y_shapes)), x_shapes, y_shapes)


def full_like(A, value, dtype=np.float64):
    """A matrix of the same kind and shape as A but filled with a single value."""
    if isinstance(A, BlockMatrix):
        new_matrix = []
        for i in range(A._stored_nb_blocks[0]):
            line = []
            for j in range(A._stored_nb_blocks[1]):
                line.append(full_like(A._stored_blocks[i, j], value, dtype=dtype))
            new_matrix.append(line)
        return A.__class__(new_matrix)
    elif isinstance(A, LowRankMatrix):
        return LowRankMatrix(np.ones((A.shape[0], 1)), np.full((1, A.shape[1]), value))
    elif isinstance(A, np.ndarray):
        return np.full_like(A, value, dtype=dtype)


def zeros_like(A, dtype=np.float64):
    """A matrix of the same kind and shape as A but filled with zeros."""
    return full_like(A, 0.0, dtype=dtype)


def ones_like(A, dtype=np.float64):
    """A matrix of the same kind and shape as A but filled with ones."""
    return full_like(A, 1.0, dtype=dtype)


def identity_like(A, dtype=np.float64):
    """A identity matrix of the same kind and shape as A."""
    if isinstance(A, BlockMatrix):
        I = []
        for i in range(A._stored_nb_blocks[0]):
            line = []
            for j in range(A._stored_nb_blocks[1]):
                if i == j:
                    line.append(identity_like(A._stored_blocks[i, j], dtype=dtype))
                else:
                    line.append(zeros_like(A._stored_blocks[i, j], dtype=dtype))
            I.append(line)
        return A.__class__(I)
    elif isinstance(A, np.ndarray):
        return np.eye(A.shape[0], A.shape[1], dtype=dtype)


def build_with_symmetries(build_matrices):
    """Decorator for the matrix building functions.

    Parameters
    ----------
    build_matrices: function
        Function that takes as argument two meshes and several other parameters and returns an
        influence matrix.

    Returns
    -------
    function
        A similar function that returns a block matrix based on the symmetries of the meshes.
    """

    @wraps(build_matrices)  # May not be necessary?
    def build_matrices_with_symmetries(mesh1, mesh2, *args, _rec_depth=1, **kwargs):
        """Assemble the influence matrices using symmetries of the body.⎈

        The method is basically an ugly multiple dispatch on the kind of bodies.
        For symmetric structures, the method is called recursively on all of the sub-bodies.

        Parameters
        ----------
        mesh1: Mesh or CollectionOfMeshes
            mesh of the receiving body (where the potential is measured)
        mesh2: Mesh or CollectionOfMeshes
            mesh of the source body (over which the source distribution is integrated)
        *args
            Passed to the actual evaluation of the coefficients
        _rec_depth: int, optional
            internal parameter: recursion accumulator for pretty log printing

        Returns
        -------
        matrix-like
            influence matrix (integral of the Green function)
        """

        if logging.getLogger().isEnabledFor(logging.DEBUG):
            # Hackish read of the original function docstring to get prettier log
            function_description_for_logging = build_matrices.__doc__.splitlines()[0]\
                .replace("mesh1", "{mesh1}").replace("mesh2", "{mesh2}")
        else:
            function_description_for_logging = ""  # irrelevant

        if (isinstance(mesh1, ReflectionSymmetry)
                and isinstance(mesh2, ReflectionSymmetry)
                and mesh1.plane == mesh2.plane):

            LOG.debug("\t" * (_rec_depth+1) +
                      function_description_for_logging.format(
                          mesh1=mesh1.name, mesh2='itself' if mesh2 is mesh1 else mesh2.name
                      ) + " using mirror symmetry.")

            S_a, V_a = build_matrices_with_symmetries(
                mesh1[0], mesh2[0], *args, **kwargs,
                _rec_depth=_rec_depth+1)
            S_b, V_b = build_matrices_with_symmetries(
                mesh1[0], mesh2[1], *args, **kwargs,
                _rec_depth=_rec_depth+1)

            return BlockSymmetricToeplitzMatrix([[S_a, S_b]]), BlockSymmetricToeplitzMatrix([[V_a, V_b]])

        elif (isinstance(mesh1, TranslationalSymmetry)
              and isinstance(mesh2, TranslationalSymmetry)
              and np.allclose(mesh1.translation, mesh2.translation)
              and mesh1.nb_submeshes == mesh2.nb_submeshes):

            LOG.debug("\t" * (_rec_depth+1) +
                      function_description_for_logging.format(
                          mesh1=mesh1.name, mesh2='itself' if mesh2 is mesh1 else mesh2.name
                      ) + " using translational symmetry.")

            S_list, V_list = [], []
            for submesh in mesh2:
                S, V = build_matrices_with_symmetries(
                    mesh1[0], submesh, *args, **kwargs,
                    _rec_depth=_rec_depth+1)
                S_list.append(S)
                V_list.append(V)
            for submesh in mesh1[1:][::-1]:
                S, V = build_matrices_with_symmetries(
                    submesh, mesh2[0], *args, **kwargs,
                    _rec_depth=_rec_depth+1)
                S_list.append(S)
                V_list.append(V)

            return BlockToeplitzMatrix([S_list]), BlockToeplitzMatrix([V_list])

        elif (isinstance(mesh1, AxialSymmetry)
              and mesh1.axis == mesh2.axis
              and mesh1.nb_submeshes == mesh2.nb_submeshes):

            LOG.debug("\t" * (_rec_depth+1) +
                      function_description_for_logging.format(
                          mesh1=mesh1.name, mesh2='itself' if mesh2 is mesh1 else mesh2.name
                      ) + " using rotation symmetry.")

            S_line, V_line = [], []
            for submesh in mesh2[:mesh2.nb_submeshes]:
                S, V = build_matrices_with_symmetries(
                    mesh1[0], submesh, *args, **kwargs,
                    _rec_depth=_rec_depth+1)
                S_line.append(S)
                V_line.append(V)

            return BlockCirculantMatrix([S_line]), BlockCirculantMatrix([V_line])

        elif (isinstance(mesh1, CollectionOfMeshes)
              and isinstance(mesh2, CollectionOfMeshes)):

            LOG.debug("\t" * (_rec_depth+1) +
                      function_description_for_logging.format(
                          mesh1=mesh1.name, mesh2='itself' if mesh2 is mesh1 else mesh2.name
                      ) + " using block matrix structure.")

            S_matrix, V_matrix = [], []
            for submesh1 in mesh1:
                S_line, V_line = [], []
                for submesh2 in mesh2:
                    S, V = build_matrices_with_symmetries(
                        submesh1, submesh2, *args, **kwargs,
                        _rec_depth=_rec_depth+1)

                    S_line.append(S)
                    V_line.append(V)
                S_matrix.append(S_line)
                V_matrix.append(V_line)

            return BlockMatrix(S_matrix), BlockMatrix(V_matrix)

        else:
            LOG.debug("\t" * (_rec_depth+1) +
                      function_description_for_logging.format(
                          mesh1=mesh1.name, mesh2='itself' if mesh2 is mesh1 else mesh2.name
                      ))

            distance = np.linalg.norm(mesh1.center_of_mass_of_nodes - mesh2.center_of_mass_of_nodes)
            if distance < 10*mesh1.diameter_of_nodes or distance < 10*mesh2.diameter_of_nodes:
                # Actual evaluation of coefficients using the Green function.
                return build_matrices(mesh1, mesh2, *args, **kwargs)
            else:
                # Low-rank matrix.
                def get_row_func(i):
                    s, v = build_matrices(mesh1.extract_faces([i]), mesh2, *args, **kwargs)
                    return s.flatten(), v.flatten()

                def get_col_func(j):
                    s, v = build_matrices(mesh1, mesh2.extract_faces([j]), *args, **kwargs)
                    return s.flatten(), v.flatten()

                # TODO: Optimize
                dtype = build_matrices(mesh1.extract_faces([0]), mesh2.extract_faces([0]), *args, **kwargs)[0].dtype

                return LowRankMatrix.from_rows_and_cols_functions_with_2_in_1_ACA(
                    get_row_func, get_col_func, mesh1.nb_faces, mesh2.nb_faces,
                    tol=1e-2, dtype=dtype)

    return build_matrices_with_symmetries
