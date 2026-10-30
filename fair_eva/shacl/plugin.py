"""Glue between :mod:`fair_eva.shacl` and plugins (``EvaluatorBase``)."""

from __future__ import annotations

import inspect
import os
from typing import Any, Mapping, Optional, Sequence

from rdflib import Graph

from fair_eva.shacl import config as shacl_config
from fair_eva.shacl.errors import DataGraphError, ShapesConfigError
from fair_eva.shacl.models import ShaclReport, ValidationOptions
from fair_eva.shacl.validator import validate


def plugin_directory(evaluator: Any) -> Optional[str]:
    """Directory of the module defining the plugin class (or ``None``)."""
    try:
        return os.path.dirname(os.path.abspath(inspect.getfile(type(evaluator))))
    except (TypeError, OSError):  # pragma: no cover - built-in / dynamic classes
        return None


def validate_for_plugin(
    evaluator: Any,
    data_graph: Optional[Graph] = None,
    shape_sets: Optional[Sequence[str]] = None,
    ontologies: Optional[Sequence[str]] = None,
    data_format: Optional[str] = None,
    combine: Optional[str] = None,
    options: Any = None,
) -> ShaclReport:
    """Validate the RDF of a plugin against the shape sets selected in its config.

    ``evaluator.config`` is the merged core + plugin ``ConfigParser`` and
    ``evaluator.name`` the plugin name (the section holding ``shacl_sets``).

    Parameters
    ----------
    data_graph : rdflib.Graph, optional
        Graph to validate; defaults to ``evaluator.get_rdf_graph()``.
    shape_sets, ontologies : sequence of ids, optional
        Override the configured selection (ids of ``[shacl:<id>]`` sections).
    combine : str, optional
        Override ``[shacl] combine`` (``separate`` / ``merged``).
    options : ValidationOptions or mapping, optional
        A mapping overrides individual options read from ``[shacl]``.

    Relative ``source`` paths are resolved against the directory of the module
    that defines the plugin class.
    """
    config = evaluator.config
    if config is None:
        raise ShapesConfigError(
            "The plugin has no configuration to read SHACL sets from"
        )
    name = getattr(evaluator, "name", None)
    base_dir = plugin_directory(evaluator)

    specs = shacl_config.shapes_from_config(config, name, shape_sets, base_dir)
    if not specs:
        raise ShapesConfigError(
            "No SHACL shape sets are configured for plugin '%s' (set 'shacl_sets' "
            "in its section or 'default_sets' in [shacl])" % name
        )
    ontology_specs = shacl_config.ontologies_from_config(
        config, name, ontologies, base_dir
    )

    if data_graph is None:
        data_graph = evaluator.get_rdf_graph()
    if data_graph is None:
        raise DataGraphError(
            "No RDF graph to validate: pass data_graph or override get_rdf_graph() "
            "in the plugin"
        )

    base_options = shacl_config.options_from_config(config)
    if isinstance(options, ValidationOptions):
        merged_options = options
    elif isinstance(options, Mapping):
        merged_options = ValidationOptions.coerce({**base_options.to_dict(), **options})
    else:
        merged_options = base_options

    return validate(
        data_graph,
        specs,
        ontologies=ontology_specs,
        data_format=data_format,
        combine=combine or shacl_config.combine_from_config(config),
        options=merged_options,
    )
