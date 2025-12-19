# SPDX-FileCopyrightText: 2025 ProFACE developers
#
# SPDX-License-Identifier: MIT

__all__ = ["DIM", "VOIGT_NOTATION", "PreprocessorError", "__version__"]


try:
    from ._version import __version__
except ImportError:
    __version__ = "0.0.0"


class PreprocessorError(Exception):
    """Base from which all preprocessor errors should be derived"""


# number of vector dimensions
DIM = 3

# order of 2-tensors components (zero based) in Voigt notation
VOIGT_NOTATION = ((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))

# of course these are compatible
assert len(VOIGT_NOTATION) == (DIM + 1) * DIM / 2
