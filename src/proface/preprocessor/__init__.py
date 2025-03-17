# SPDX-FileCopyrightText: 2025 ProFACE developers
#
# SPDX-License-Identifier: MIT

__all__ = ["PreprocessorError", "__version__"]


from ._version import __version__


class PreprocessorError(Exception):
    """Base from which all preprocessor errors should be derived"""
