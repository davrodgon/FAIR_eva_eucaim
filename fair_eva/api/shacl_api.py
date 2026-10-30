"""REST endpoint for the generic SHACL validation (``POST /shacl/validate``).

The shapes are never taken from the request: the caller can only choose among
the shape sets configured on the server (``[shacl:<id>]`` sections), so the
endpoint cannot be used to make the server fetch arbitrary URLs.
"""

import logging
from importlib import resources

from fair_eva.api import rda
from fair_eva.api.evaluator import EvaluatorBase
from fair_eva.shacl import (
    DataGraphError,
    ShaclError,
    ShapesConfigError,
    load_core_config,
    ontologies_from_config,
    options_from_config,
    shapes_from_config,
    validate,
)
from fair_eva.shacl.config import combine_from_config

logger = logging.getLogger("api.shacl")


def _error(code, message):
    logger.error(message)
    return {"code": code, "message": str(message)}, code


def _load_config(plugin_name):
    """Core configuration plus, if given, the one of the plugin; and its directory."""
    if not plugin_name:
        return load_core_config(), None
    package = "%s.%s" % (rda.PLUGIN_PATH, plugin_name)
    config = EvaluatorBase.load_config(package)
    try:
        base_dir = str(resources.files(package))
    except (ModuleNotFoundError, TypeError):  # pragma: no cover
        base_dir = None
    return config, base_dir


def validate_endpoint(body):
    """Validate the RDF in ``body["data"]`` against the configured shape sets."""
    data = body.get("data")
    if not data:
        return _error(400, "Missing 'data': RDF text to validate")
    plugin_name = body.get("repo")
    if plugin_name and plugin_name not in rda.collect_plugins():
        return _error(400, "Could not find plugin module <%s>" % plugin_name)

    try:
        config, base_dir = _load_config(plugin_name)
    except (OSError, ImportError) as exc:
        return _error(500, "Could not load the configuration: %s" % exc)

    try:
        specs = shapes_from_config(
            config, plugin_name, body.get("shape_sets"), base_dir
        )
        if not specs:
            raise ShapesConfigError(
                "No SHACL shape sets are configured (nor selected in 'shape_sets')"
            )
        report = validate(
            data,
            specs,
            ontologies=ontologies_from_config(
                config, plugin_name, body.get("ontologies"), base_dir
            ),
            data_format=body.get("format") or "turtle",
            combine=body.get("combine") or combine_from_config(config),
            options=options_from_config(config),
        )
    except (ShapesConfigError, DataGraphError) as exc:
        return _error(400, exc)
    except ShaclError as exc:  # shapes that cannot be loaded, engine failures
        return _error(500, exc)
    return report.to_dict(), 200
