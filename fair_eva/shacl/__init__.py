"""Generic SHACL validation with traceability of the shapes used.

The core validates RDF against SHACL shapes supplied by configuration (local
path or URL, any rdflib serialisation); it ships no shapes of its own.  Every
:class:`ShaclReport` records, for each shapes document, its name, version and
SHA-256, plus the time of the validation and the library versions.

Quick start::

    from fair_eva.shacl import validate

    report = validate(graph, {"source": "shapes/basic.ttl", "version": "1.0"})
    report.conforms, report.violations
    report.shapes_provenance[0].sha256

See ``docs/shacl_validation.md``.
"""

from fair_eva.shacl.config import (
    available_set_ids,
    load_core_config,
    ontologies_from_config,
    options_from_config,
    selected_ids,
    shapes_from_config,
)
from fair_eva.shacl.errors import (
    DataGraphError,
    ShaclError,
    ShaclRunError,
    ShapesConfigError,
    ShapesLoadError,
)
from fair_eva.shacl.loader import clear_graph_cache, load_shapes
from fair_eva.shacl.models import (
    UNSPECIFIED,
    LoadedShapes,
    ShaclReport,
    ShaclResult,
    ShapesProvenance,
    ShapesSpec,
    ValidationOptions,
    ValidationRun,
)
from fair_eva.shacl.plugin import validate_for_plugin
from fair_eva.shacl.validator import validate

__all__ = [
    "UNSPECIFIED",
    "DataGraphError",
    "LoadedShapes",
    "ShaclError",
    "ShaclReport",
    "ShaclResult",
    "ShaclRunError",
    "ShapesConfigError",
    "ShapesLoadError",
    "ShapesProvenance",
    "ShapesSpec",
    "ValidationOptions",
    "ValidationRun",
    "available_set_ids",
    "clear_graph_cache",
    "load_core_config",
    "load_shapes",
    "ontologies_from_config",
    "options_from_config",
    "selected_ids",
    "shapes_from_config",
    "validate",
    "validate_for_plugin",
]
