import json

import pytest
from rdflib import Graph
from rdflib.compare import isomorphic

from fair_eva.ftr import describe, rdf, results
from fair_eva.ftr.execution import run_tests
from fair_eva.ftr.indicators import all_indicators, get_indicator
from fair_eva.tests.ftr.support import API_ENDPOINT, PID, PLUGIN


@pytest.mark.parametrize(
    "accept,expected",
    [
        (None, rdf.JSONLD),
        ("", rdf.JSONLD),
        ("*/*", rdf.JSONLD),
        ("text/turtle", rdf.TURTLE),
        ("application/ld+json", rdf.JSONLD),
        ("application/json", rdf.JSON),
        ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", rdf.JSONLD),
        ("text/turtle;q=0.9, application/ld+json;q=0.5", rdf.TURTLE),
        ("application/ld+json;q=0.1, text/turtle", rdf.TURTLE),
    ],
)
def test_accept_header(accept, expected):
    assert rdf.negotiate(accept) == expected


def test_unsupported_accept_is_not_acceptable():
    with pytest.raises(rdf.FormatError) as error:
        rdf.negotiate("text/html")
    assert error.value.status == 406


@pytest.mark.parametrize(
    "value,expected",
    [
        ("turtle", rdf.TURTLE),
        ("TTL", rdf.TURTLE),
        ("jsonld", rdf.JSONLD),
        ("json", rdf.JSON),
    ],
)
def test_format_parameter_wins_over_accept(value, expected):
    assert (
        rdf.negotiate(
            "text/turtle" if expected != rdf.TURTLE else "application/ld+json", value
        )
        == expected
    )


def test_unknown_format_parameter_is_a_bad_request():
    with pytest.raises(rdf.FormatError) as error:
        rdf.negotiate(None, "rdfxml")
    assert error.value.status == 400


@pytest.fixture
def graph(settings):
    outcomes = run_tests(
        PLUGIN,
        [get_indicator("rda_f1_01m")],
        PID,
        api_endpoint=API_ENDPOINT,
    )
    g = results.result_graph(settings, outcomes[0])
    g += describe.tests_graph({PLUGIN: settings}, all_indicators()[:3])
    return g


def test_turtle_round_trip(graph):
    text = rdf.serialize(graph, rdf.TURTLE)
    assert text.startswith("@prefix")
    assert isomorphic(Graph().parse(data=text, format="turtle"), graph)


@pytest.mark.parametrize("media_type", [rdf.JSONLD, rdf.JSON])
def test_json_ld_round_trip(graph, media_type):
    text = rdf.serialize(graph, media_type)
    document = json.loads(text)
    assert "@graph" in document and "@context" in document
    assert isomorphic(Graph().parse(data=text, format="json-ld"), graph)


def test_json_ld_uses_the_terms_of_the_ftr_context(graph):
    document = json.loads(rdf.serialize(graph, rdf.JSONLD))
    nodes = {n["@id"]: n for n in document["@graph"]}
    (result,) = [n for n in nodes.values() if n.get("@type") == "ftr:TestResult"]
    for term in (
        "value",
        "log",
        "completion",
        "outputFromTest",
        "assessmentTarget",
        "license",
    ):
        assert term in result
    assert result["completion"] == 100
    assert document["@context"]["sio"].startswith("http://")
