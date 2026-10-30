"""Acceptance tests: the FTR output of FAIR EVA validates with pySHACL against the shapes
of the FTR vocabulary, release 1.3.0 (vendored in ``fair_eva/tests/data/ftr_shacl``).

The dummy plugin is used so that everything runs offline.
"""

import pytest
from pyshacl import validate
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, PROV, RDF, XSD

from fair_eva import app
from fair_eva.ftr import describe, rdf, results
from fair_eva.ftr.execution import run_tests
from fair_eva.ftr.indicators import all_indicators, get_indicator
from fair_eva.ftr.settings import settings_from_section
from fair_eva.ftr.vocab import DCAT, FTR
from fair_eva.tests.ftr.support import (
    API_ENDPOINT,
    PID,
    PLUGIN,
    SHACL_DIR,
    shacl_report,
)

# Every shape file that applies to what FAIR EVA emits for a test result
RESULT_SHAPES = ["test.shacl", "testResult.shacl", "testResultSet.shacl"]

# (resource, indicator): a pass, a fail and an execution error (network is disabled)
SCENARIOS = {
    "pass": (PID, "rda_f1_01m"),
    "fail": ("internal", "rda_f1_01m"),
    "error": (PID, "rda_a1_02m"),
}


def outcome(scenario, clock):
    resource, indicator = SCENARIOS[scenario]
    return run_tests(
        PLUGIN,
        [get_indicator(indicator)],
        resource,
        api_endpoint=API_ENDPOINT,
        clock=clock,
    )[0]


def assert_conforms(graph, shapes):
    conforms, report = shacl_report(graph, shapes)
    assert conforms, "%s\n%s" % (shapes, report)


def test_vendored_shapes_are_present():
    shapes = {p.name for p in SHACL_DIR.glob("*.shacl")}
    assert shapes == {
        "test.shacl",
        "testResult.shacl",
        "testResultSet.shacl",
        "metric.shacl",
    }


# ---- tests (ftr:Test) ----------------------------------------------------------------


def test_dummy_test_description_conforms(settings):
    g = describe.new_graph()
    describe.add_test(g, settings, PLUGIN, get_indicator("rda_f1_01m"))
    assert_conforms(g, "test.shacl")


def test_all_dummy_tests_conform(settings):
    g = describe.tests_graph({PLUGIN: settings}, all_indicators())
    assert len(list(g.subjects(RDF.type, FTR.Test))) == len(all_indicators())
    assert_conforms(g, "test.shacl")


def test_test_with_every_optional_property_conforms():
    full = settings_from_section(
        {
            "base_uri": "https://example.org/x/",
            "contact_type": "individual",
            "contact_name": "Some One",
            "contact_email": "some.one@example.org",
            "publisher_name": "Some Publisher",
            "creator_type": "individual",
            "creator_name": "Some Creator",
            "applicable_for": "https://schema.org/Dataset https://schema.org/SoftwareSourceCode",
        }
    )
    g = describe.tests_graph({PLUGIN: full}, all_indicators())
    assert_conforms(g, "test.shacl")


# ---- test results and result sets ------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("shapes", RESULT_SHAPES)
def test_result_conforms(settings, clock, scenario, shapes):
    g = results.result_graph(settings, outcome(scenario, clock))
    assert_conforms(g, shapes)


@pytest.mark.parametrize("shapes", RESULT_SHAPES)
def test_result_set_conforms(settings, clock, shapes):
    outcomes = run_tests(
        PLUGIN,
        [
            get_indicator(i)
            for i in ("rda_f1_01m", "rda_f1_02m", "rda_a1_02m", "rda_r1_1_01m")
        ],
        "internal",
        api_endpoint=API_ENDPOINT,
        clock=clock,
    )
    g = results.result_set_graph(settings, outcomes)
    assert len(list(g.subjects(RDF.type, FTR.TestResult))) == 4
    assert_conforms(g, shapes)


@pytest.mark.parametrize("media_type", [rdf.TURTLE, rdf.JSONLD])
@pytest.mark.parametrize("shapes", RESULT_SHAPES)
def test_serialised_result_conforms_after_parsing(settings, clock, media_type, shapes):
    """What is sent over the wire, not just the in-memory graph, is what validates."""
    g = results.result_graph(settings, outcome("pass", clock))
    text = rdf.serialize(g, media_type)
    parsed = Graph().parse(
        data=text, format="turtle" if media_type == rdf.TURTLE else "json-ld"
    )
    assert len(parsed) == len(g)
    assert_conforms(parsed, shapes)


# ---- through the HTTP API --------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    return app.test_client()


@pytest.mark.parametrize(
    "accept,fmt",
    [
        ("text/turtle", "turtle"),
        ("application/ld+json", "json-ld"),
    ],
)
def test_http_test_result_conforms(client, accept, fmt):
    response = client.post(
        "/v1.0/ftr/assess/test/dummy/rda_f1_01m",
        json={"resource_identifier": PID, "api_endpoint": API_ENDPOINT},
        headers={"Accept": accept},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(accept)
    g = Graph().parse(data=response.text, format=fmt)
    for shapes in RESULT_SHAPES:
        assert_conforms(g, shapes)
    (result,) = g.subjects(RDF.type, FTR.TestResult)
    assert str(g.value(result, PROV.value)) == "pass"


@pytest.mark.parametrize(
    "path", ["/v1.0/ftr/tests", "/v1.0/ftr/tests/dummy/rda_f1_01m"]
)
@pytest.mark.parametrize(
    "accept,fmt", [("text/turtle", "turtle"), ("application/ld+json", "json-ld")]
)
def test_http_test_descriptions_conform(client, path, accept, fmt):
    response = client.get(path, headers={"Accept": accept})
    assert response.status_code == 200
    assert_conforms(Graph().parse(data=response.text, format=fmt), "test.shacl")


def test_http_result_set_conforms(client):
    response = client.post(
        "/v1.0/ftr/assess/plugin/dummy",
        json={
            "resource_identifier": "internal",
            "api_endpoint": API_ENDPOINT,
            "tests": ["rda_f1_01m", "rda_f3_01m"],
        },
        headers={"Accept": "text/turtle"},
    )
    assert response.status_code == 200
    g = Graph().parse(data=response.text, format="turtle")
    for shapes in RESULT_SHAPES:
        assert_conforms(g, shapes)
    assert len(list(g.subjects(RDF.type, FTR.TestResultSet))) == 1


# ---- metrics ---------------------------------------------------------------------------


def patched_metric_shapes():
    """metric.shacl with its typo fixed the way the other shape files write it.

    ``sh:nodeKind xsd:string`` is not a valid node kind; test.shacl, benchmark.shacl,...
    use ``sh:or ([sh:datatype xsd:string] [sh:datatype rdf:langString])`` instead.
    """
    text = (SHACL_DIR / "metric.shacl").read_text()
    assert "sh:nodeKind xsd:string" in text
    string_or_langstring = (
        "sh:or ( [ sh:datatype xsd:string ] [ sh:datatype rdf:langString ] )"
    )
    return Graph().parse(
        data=text.replace("sh:nodeKind xsd:string", string_or_langstring),
        format="turtle",
    )


def test_metrics_conform_to_metric_shapes_without_the_upstream_typo(settings):
    g = describe.metrics_graph(settings, [PLUGIN], all_indicators())
    conforms, _, report = validate(
        data_graph=g, shacl_graph=patched_metric_shapes(), inference="none"
    )
    assert conforms, report


@pytest.mark.xfail(
    strict=True,
    reason=(
        "metric.shacl 1.3.0 declares 'sh:nodeKind xsd:string' for dcterms:title, "
        "dcterms:description and dcat:version (all mandatory), which no value can "
        "satisfy; see docs/ftr_ostrails.md. If this starts to pass the shapes were "
        "fixed upstream: drop patched_metric_shapes()."
    ),
)
def test_metrics_conform_to_upstream_metric_shapes(settings):
    g = describe.metrics_graph(settings, [PLUGIN], all_indicators())
    assert_conforms(g, "metric.shacl")


# ---- negative controls: the validation is not vacuous ------------------------------------


def break_graph(settings, clock, change):
    g = results.result_graph(settings, outcome("pass", clock))
    (result,) = g.subjects(RDF.type, FTR.TestResult)
    (test,) = g.subjects(RDF.type, FTR.Test)
    change(g, result, test)
    return g


@pytest.mark.parametrize(
    "name,change,shapes",
    [
        (
            "no suggestion",
            lambda g, r, t: g.remove((r, FTR.suggestion, None)),
            "testResult.shacl",
        ),
        (
            "no license",
            lambda g, r, t: g.remove((r, DCTERMS.license, None)),
            "testResult.shacl",
        ),
        (
            "completion is not an integer",
            lambda g, r, t: (
                g.remove((r, FTR.completion, None)),
                g.add((r, FTR.completion, Literal("1.0", datatype=XSD.decimal))),
            ),
            "testResult.shacl",
        ),
        (
            "identifier is an IRI",
            lambda g, r, t: (
                g.remove((r, DCTERMS.identifier, None)),
                g.add((r, DCTERMS.identifier, r)),
            ),
            "testResult.shacl",
        ),
        (
            "result is a blank node",
            lambda g, r, t: g.add((BNode(), RDF.type, FTR.TestResult)),
            "testResult.shacl",
        ),
        (
            "test without contact point",
            lambda g, r, t: g.remove((t, DCAT.contactPoint, None)),
            "test.shacl",
        ),
        (
            "test without license",
            lambda g, r, t: g.remove((t, DCTERMS.license, None)),
            "test.shacl",
        ),
        (
            "test with two versions",
            lambda g, r, t: g.add((t, DCAT.version, Literal("another"))),
            "test.shacl",
        ),
        (
            "test without metric",
            lambda g, r, t: g.remove(
                (t, URIRef("http://semanticscience.org/resource/SIO_000233"), None)
            ),
            "test.shacl",
        ),
    ],
)
def test_broken_output_is_rejected(settings, clock, name, change, shapes):
    g = break_graph(settings, clock, change)
    conforms, _ = shacl_report(g, shapes)
    assert not conforms, name


def test_the_sio_namespace_of_the_official_json_ld_context_would_not_validate(
    settings, clock
):
    """The official context maps ``sio`` to https; the shapes require http."""
    g = describe.new_graph()
    describe.add_test(g, settings, PLUGIN, get_indicator("rda_f1_01m"))
    (test,) = g.subjects(RDF.type, FTR.Test)
    (metric,) = g.objects(
        test, URIRef("http://semanticscience.org/resource/SIO_000233")
    )
    g.remove((test, URIRef("http://semanticscience.org/resource/SIO_000233"), metric))
    g.add((test, URIRef("https://semanticscience.org/resource/SIO_000233"), metric))
    conforms, _ = shacl_report(g, "test.shacl")
    assert not conforms
