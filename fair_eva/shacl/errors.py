"""Exceptions raised by the SHACL validation package.

All of them derive from :class:`ShaclError`, so callers (typically plugins
computing an indicator) can catch a single exception type.
"""


class ShaclError(Exception):
    """Base class of every error raised by :mod:`fair_eva.shacl`."""


class ShapesConfigError(ShaclError):
    """The shapes specification or the configuration is invalid.

    Examples: unknown option, unknown shape set identifier, no shapes selected.
    """


class ShapesLoadError(ShaclError):
    """A shapes (or ontology) document could not be read, downloaded or parsed.

    It is also raised when the loaded content does not match a pinned SHA-256.
    """


class DataGraphError(ShaclError):
    """The data graph to validate is missing, empty or cannot be parsed."""


class ShaclRunError(ShaclError):
    """The SHACL engine failed while validating (e.g. invalid shapes graph)."""
