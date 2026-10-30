"""Handlers of the FTR API (``ftr/ftr-api.yaml``), served under ``/v1.0/ftr``.

They follow the OpenAPI template of the FTR vocabulary (``/tests``, ``/metrics`` and
``/assess/test/...``) and answer in JSON-LD (default) or Turtle, chosen with the
``Accept`` header or the ``format`` query parameter. The existing ``/rda/*`` endpoints
are not touched and keep returning the FAIR EVA JSON.
"""

import dataclasses
import json
import logging
from functools import wraps
from importlib import resources
from urllib.parse import unquote

from flask import Response, request

from fair_eva.api import rda
from fair_eva.ftr import describe, rdf, results
from fair_eva.ftr.execution import FtrRequestError, run_tests
from fair_eva.ftr.identifiers import Identifiers
from fair_eva.ftr.indicators import all_indicators, get_indicator
from fair_eva.ftr.settings import load_settings

logger = logging.getLogger("api.ftr")

# Path under which ftr-api.yaml is mounted (its ``servers`` url).
BASE_PATH = "/v1.0/ftr"


def _error(message, status):
    # An explicit Response: the operations define several content types (RDF formats),
    # so connexion cannot tell which one a plain dictionary should be serialised as.
    body = json.dumps({"code": status, "message": message})
    return Response(body, status=status, mimetype="application/json")


def _plugins():
    """Installed plugins. ``collect_plugins`` also returns files and caches of the namespace."""
    plugins = []
    for name in rda.collect_plugins():
        if name.startswith(("_", ".")):
            continue
        try:
            if resources.files(rda.PLUGIN_PATH).joinpath(name, "plugin.py").is_file():
                plugins.append(name)
        except (ModuleNotFoundError, OSError):  # pragma: no cover - defensive
            continue
    return sorted(set(plugins))


def _settings(plugin=None):
    try:
        settings = load_settings(plugin, rda.PLUGIN_PATH)
    except ValueError as e:
        raise FtrRequestError("Invalid [ftr] configuration: %s" % e, 500)
    if not settings.endpoint_base_url:
        settings = dataclasses.replace(
            settings, endpoint_base_url=request.url_root.rstrip("/") + BASE_PATH
        )
    return settings


def _media_type():
    """Representation requested (checked before doing any work, e.g. running a test)."""
    return rdf.negotiate(request.headers.get("Accept"), request.args.get("format"))


def _respond(graph, media_type):
    return Response(
        rdf.serialize(graph, media_type),
        status=200,
        mimetype=media_type,
        headers={"Vary": "Accept"},
    )


def _guard(handler):
    """Turn the errors of the FTR layer into the JSON error of the API."""

    @wraps(handler)
    def wrapper(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except rdf.FormatError as e:
            return _error(e.message, e.status)
        except FtrRequestError as e:
            return _error(e.message, e.status)

    return wrapper


def _split_test_id(testid, base_uri):
    """``plugin/indicator`` or the full IRI of a test -> ``(plugin, indicator)``."""
    prefix = Identifiers(base_uri).base + "/test/"
    if testid.startswith(prefix):
        testid = testid[len(prefix) :]
    parts = [unquote(p) for p in testid.strip("/").split("/")]
    if len(parts) != 2 or not all(parts):
        raise FtrRequestError(
            "Test identifiers have the form <plugin>/<indicator>, e.g. 'dummy/rda_f1_01m'",
            400,
        )
    return parts[0], parts[1]


def _known_test(plugin, indicator_id):
    if plugin not in _plugins():
        raise FtrRequestError("Unknown plugin <%s>" % plugin, 404)
    indicator = get_indicator(indicator_id)
    if indicator is None:
        raise FtrRequestError("Unknown indicator <%s>" % indicator_id, 404)
    return indicator


@_guard
def list_tests(plugin=None, testid=None):
    """Describe the tests (``plugin + indicator``), all of them or those of one plugin."""
    media_type = _media_type()
    if testid:
        plugin, indicator_id = _split_test_id(testid, _settings().base_uri)
        return get_test(plugin, indicator_id)
    plugins = _plugins()
    if plugin:
        if plugin not in plugins:
            raise FtrRequestError("Unknown plugin <%s>" % plugin, 404)
        plugins = [plugin]
    graph = describe.tests_graph({p: _settings(p) for p in plugins}, all_indicators())
    return _respond(graph, media_type)


@_guard
def get_test(plugin, indicator):
    """Describe one test."""
    media_type = _media_type()
    indicator = _known_test(plugin, indicator)
    graph = describe.new_graph()
    describe.add_test(graph, _settings(plugin), plugin, indicator)
    return _respond(graph, media_type)


@_guard
def list_metrics(metricid=None):
    """Describe the metrics (the RDA indicators) and the tests that implement them."""
    media_type = _media_type()
    if metricid:
        # Either the indicator id or the IRI of the metric (.../metric/<indicator>)
        return get_metric(unquote(metricid.rstrip("/").split("/")[-1]))
    graph = describe.metrics_graph(_settings(), _plugins(), all_indicators())
    return _respond(graph, media_type)


@_guard
def get_metric(indicator):
    """Describe one metric."""
    media_type = _media_type()
    found = get_indicator(indicator)
    if found is None:
        raise FtrRequestError("Unknown indicator <%s>" % indicator, 404)
    graph = describe.metrics_graph(_settings(), _plugins(), [found])
    return _respond(graph, media_type)


@_guard
def assess_test(plugin, indicator, body):
    """Run one test on a resource and return the FTR test result."""
    media_type = _media_type()
    indicator = _known_test(plugin, indicator)
    outcomes = run_tests(
        plugin,
        [indicator],
        body.get("resource_identifier"),
        api_endpoint=body.get("api_endpoint"),
        lang=body.get("lang", "en"),
    )
    return _respond(results.result_graph(_settings(plugin), outcomes[0]), media_type)


@_guard
def assess_plugin(plugin, body):
    """Run several tests (all by default) of a plugin and return an FTR test result set."""
    media_type = _media_type()
    if plugin not in _plugins():
        raise FtrRequestError("Unknown plugin <%s>" % plugin, 404)
    wanted = body.get("tests") or [i.id for i in all_indicators()]
    indicators = []
    for indicator_id in wanted:
        indicator = get_indicator(indicator_id)
        if indicator is None:
            raise FtrRequestError("Unknown indicator <%s>" % indicator_id, 400)
        indicators.append(indicator)
    outcomes = run_tests(
        plugin,
        indicators,
        body.get("resource_identifier"),
        api_endpoint=body.get("api_endpoint"),
        lang=body.get("lang", "en"),
    )
    return _respond(results.result_set_graph(_settings(plugin), outcomes), media_type)
