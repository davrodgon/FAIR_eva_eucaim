from importlib import resources

import yaml
from rdflib import Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, RDF

import fair_eva
from fair_eva.api.evaluator import EvaluatorBase
from fair_eva.ftr import describe
from fair_eva.ftr.identifiers import Identifiers
from fair_eva.ftr.indicators import all_indicators, get_indicator
from fair_eva.ftr.settings import DEFAULT_ENDPOINT_BASE_URL, settings_from_section
from fair_eva.ftr.vocab import (
    DCAT,
    DOAP,
    DPV,
    DQV,
    FTR,
    HAS_IMPLEMENTATION,
    IS_IMPLEMENTATION_OF,
    VCARD,
)

F1_01M = get_indicator("rda_f1_01m")


def test_indicator_registry_matches_the_api_definition():
    api = resources.files(fair_eva).joinpath("fair-api.yaml").read_text()
    paths = yaml.safe_load(api)["paths"]
    in_api = {
        p.split("/")[-1] for p, v in paths.items() if v.get("x-indicator") is True
    }
    assert {i.id for i in all_indicators()} == in_api
    for indicator in all_indicators():
        assert hasattr(EvaluatorBase, indicator.id)
        assert (
            indicator.code.replace("-", "_").replace(".", "_").lower() == indicator.id
        )
        assert indicator.code.startswith("RDA-" + indicator.principle + "-")
        assert indicator.title and indicator.description


def test_unknown_indicator():
    assert get_indicator("rda_x9_99z") is None


def test_test_is_the_pair_plugin_and_indicator(settings):
    g = describe.new_graph()
    test = describe.add_test(g, settings, "dummy", F1_01M)
    other = describe.add_test(g, settings, "other-plugin", F1_01M)
    assert str(test) == settings.base_uri + "/test/dummy/rda_f1_01m"
    assert str(other) == settings.base_uri + "/test/other-plugin/rda_f1_01m"
    # ... and both implement the same metric
    metric = Identifiers(settings.base_uri).metric("rda_f1_01m")
    assert set(g.objects(test, IS_IMPLEMENTATION_OF)) == {metric}
    assert set(g.objects(other, IS_IMPLEMENTATION_OF)) == {metric}
    assert "dummy" in str(g.value(test, DCTERMS.title))
    assert "other-plugin" in str(g.value(other, DCTERMS.title))


def test_test_properties(settings):
    g = describe.new_graph()
    test = describe.add_test(g, settings, "dummy", F1_01M)
    assert (test, RDF.type, FTR.Test) in g
    assert g.value(test, DCTERMS.identifier) == Literal(str(test))
    assert g.value(test, DCAT.version) == Literal(settings.version)
    assert g.value(test, DCTERMS.license) == URIRef(settings.test_license)
    assert g.value(test, DOAP.repository) == URIRef(settings.code_repository)
    assert g.value(test, FTR.supportedBy) == URIRef(settings.tool_uri)
    assert g.value(test, DCAT.contactPoint) == URIRef(settings.contact.uri)
    assert (URIRef(settings.contact.uri), RDF.type, VCARD.Organization) in g
    assert g.value(test, DCAT.endpointURL) == URIRef(
        DEFAULT_ENDPOINT_BASE_URL + "/assess/test/dummy/rda_f1_01m"
    )
    assert g.value(test, DCAT.endpointDescription) == URIRef(
        DEFAULT_ENDPOINT_BASE_URL + "/openapi.json"
    )
    assert g.value(test, DCAT.landingPage) == URIRef(
        DEFAULT_ENDPOINT_BASE_URL + "/tests/dummy/rda_f1_01m"
    )
    # Optional things are only there when the deployer configures them
    assert g.value(test, DCTERMS.publisher) is None
    assert g.value(test, DCTERMS.creator) is None
    assert g.value(test, DPV.isApplicableFor) is None


def test_optional_properties_follow_the_configuration():
    s = settings_from_section(
        {
            "endpoint_base_url": "https://api.ex.org/ftr",
            "contact_type": "individual",
            "contact_name": "Ada",
            "contact_email": "ada@ex.org",
            "publisher_name": "Pub",
            "creator_name": "Cre",
            "applicable_for": "https://schema.org/Dataset",
        }
    )
    g = describe.new_graph()
    test = describe.add_test(g, s, "dummy", F1_01M)
    contact = g.value(test, DCAT.contactPoint)
    assert (contact, RDF.type, VCARD.Individual) in g
    assert g.value(contact, VCARD.fn) == Literal("Ada")
    assert g.value(contact, VCARD.hasEmail) == URIRef("mailto:ada@ex.org")
    assert (g.value(test, DCTERMS.publisher), RDF.type, VCARD.Organization) in g
    assert g.value(test, DCTERMS.creator) is not None
    assert g.value(test, DPV.isApplicableFor) == URIRef("https://schema.org/Dataset")
    assert str(g.value(test, DCAT.endpointURL)).startswith(
        "https://api.ex.org/ftr/assess/"
    )


def test_everything_hangs_from_the_configured_base_uri():
    s = settings_from_section({"base_uri": "https://ids.example.net/fe"})
    g = describe.tests_graph({"dummy": s}, [F1_01M])
    g += describe.metrics_graph(s, ["dummy"], [F1_01M])
    for node in set(g.subjects(RDF.type, FTR.Test)) | set(
        g.subjects(RDF.type, FTR.Metric)
    ):
        assert str(node).startswith("https://ids.example.net/fe/")


def test_descriptions_are_deterministic(settings):
    first = describe.tests_graph({"dummy": settings}, all_indicators())
    second = describe.tests_graph({"dummy": settings}, all_indicators())
    assert isomorphic(first, second)


def test_metric_lists_the_tests_that_implement_it(settings):
    g = describe.metrics_graph(settings, ["dummy", "other"], [F1_01M])
    metric = Identifiers(settings.base_uri).metric("rda_f1_01m")
    assert (metric, RDF.type, FTR.Metric) in g
    assert {str(t) for t in g.objects(metric, HAS_IMPLEMENTATION)} == {
        settings.base_uri + "/test/dummy/rda_f1_01m",
        settings.base_uri + "/test/other/rda_f1_01m",
    }
    assert g.value(metric, DQV.inDimension) == URIRef(
        "https://w3id.org/fair/principles/terms/F1"
    )
    assert g.value(metric, DCAT.landingPage) == URIRef(describe.RDA_FAIR_MATURITY_MODEL)


def test_sub_principles_use_dotted_dimension():
    assert get_indicator("rda_a1_1_01m").principle_uri.endswith("/A1.1")
    assert get_indicator("rda_r1_3_02d").dimension_name == "Reusable"
