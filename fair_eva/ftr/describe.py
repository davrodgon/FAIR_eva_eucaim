"""Descriptions of FAIR EVA tests and metrics as FTR resources.

* A **test** (``ftr:Test``) is the pair *plugin + indicator*: the code of a given plugin
  that checks a given RDA indicator on the repository that plugin talks to.
* A **metric** (``ftr:Metric``) is the RDA indicator itself, which is the same for all
  plugins. Every plugin's test for that indicator is an implementation of the metric.
"""

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, RDF

from fair_eva.ftr.identifiers import Identifiers, segment
from fair_eva.ftr.settings import DEFAULT_ENDPOINT_BASE_URL
from fair_eva.ftr.vocab import (
    DCAT,
    DOAP,
    DPV,
    DQV,
    FTR,
    HAS_IMPLEMENTATION,
    IS_IMPLEMENTATION_OF,
    PREFIXES,
    VCARD,
    VIVO,
)

# Specification the RDA indicators come from (cited in docs/index.md of FAIR EVA).
RDA_FAIR_MATURITY_MODEL = "https://doi.org/10.15497/rda00050"

STATUS_ACTIVE = "Active"


def new_graph():
    g = Graph()
    for prefix, namespace in PREFIXES.items():
        g.bind(prefix, namespace)
    return g


def _en(text):
    return Literal(text, lang="en")


def add_agent(g, agent):
    """Add a vcard:Organization / vcard:Individual node and return its IRI."""
    node = URIRef(agent.uri)
    if agent.kind == "individual":
        g.add((node, RDF.type, VCARD.Individual))
        if agent.name:
            g.add((node, VCARD.fn, Literal(agent.name)))
    else:
        g.add((node, RDF.type, VCARD.Organization))
        if agent.name:
            g.add((node, VCARD["organization-name"], Literal(agent.name)))
    if agent.email:
        g.add((node, VCARD.hasEmail, URIRef(agent.email)))
    return node


def _add_responsible_agents(g, subject, settings, with_publisher=True):
    g.add((subject, DCAT.contactPoint, add_agent(g, settings.contact)))
    if settings.creator:
        g.add((subject, DCTERMS.creator, add_agent(g, settings.creator)))
    # NOTE: the shapes check dcat:publisher, which DCAT does not define. The standard
    # dcterms:publisher (also used in the specification examples) is emitted instead.
    if with_publisher and settings.publisher:
        g.add((subject, DCTERMS.publisher, add_agent(g, settings.publisher)))


def test_urls(settings, plugin, indicator_id):
    base = settings.endpoint_base_url or DEFAULT_ENDPOINT_BASE_URL
    p, i = segment(plugin), segment(indicator_id)
    return {
        "endpoint_url": URIRef("%s/assess/test/%s/%s" % (base, p, i)),
        "landing_page": URIRef("%s/tests/%s/%s" % (base, p, i)),
        "endpoint_description": URIRef("%s/openapi.json" % base),
    }


def add_test(g, settings, plugin, indicator):
    """Add the ``ftr:Test`` for ``plugin + indicator`` to ``g`` and return its IRI."""
    ids = Identifiers(settings.base_uri)
    test = ids.test(plugin, indicator.id)
    metric = ids.metric(indicator.id)
    urls = test_urls(settings, plugin, indicator.id)

    g.add((test, RDF.type, FTR.Test))
    g.add((test, DCTERMS.identifier, Literal(str(test))))
    g.add(
        (
            test,
            DCTERMS.title,
            _en("%s: %s (%s plugin)" % (indicator.code, indicator.title, plugin)),
        )
    )
    g.add(
        (
            test,
            DCTERMS.description,
            _en(
                "%s This test checks the indicator with the FAIR EVA '%s' plugin, "
                "using the metadata terms configured for that plugin."
                % (indicator.description, plugin)
            ),
        )
    )
    g.add((test, VIVO.abbreviation, Literal("%s [%s]" % (indicator.code, plugin))))
    for keyword in ("FAIR", "RDA", indicator.code, indicator.dimension_name, plugin):
        g.add((test, DCAT.keyword, _en(keyword)))
    g.add((test, DCAT.endpointURL, urls["endpoint_url"]))
    g.add((test, DCAT.endpointDescription, urls["endpoint_description"]))
    g.add((test, DCAT.landingPage, urls["landing_page"]))
    g.add((test, DOAP.repository, URIRef(settings.code_repository)))
    g.add((test, DCTERMS.license, URIRef(settings.test_license)))
    g.add((test, DCAT.version, Literal(settings.version)))
    g.add((test, FTR.status, _en(STATUS_ACTIVE)))
    g.add((test, IS_IMPLEMENTATION_OF, metric))
    g.add((test, FTR.supportedBy, URIRef(settings.tool_uri)))
    for object_type in settings.applicable_for:
        g.add((test, DPV.isApplicableFor, URIRef(object_type)))
    _add_responsible_agents(g, test, settings)
    return test


def add_metric(g, settings, indicator, test_uris=()):
    """Add the ``ftr:Metric`` of an indicator to ``g`` and return its IRI."""
    ids = Identifiers(settings.base_uri)
    metric = ids.metric(indicator.id)

    g.add((metric, RDF.type, FTR.Metric))
    g.add((metric, DCTERMS.identifier, Literal(str(metric))))
    g.add((metric, DCTERMS.title, _en("%s: %s" % (indicator.code, indicator.title))))
    g.add((metric, DCTERMS.description, _en(indicator.description)))
    g.add((metric, VIVO.abbreviation, Literal(indicator.code)))
    for keyword in ("FAIR", "RDA", indicator.code, indicator.dimension_name):
        g.add((metric, DCAT.keyword, _en(keyword)))
    g.add((metric, DCAT.landingPage, URIRef(RDA_FAIR_MATURITY_MODEL)))
    g.add((metric, DCAT.version, Literal(settings.version)))
    g.add((metric, FTR.status, _en(STATUS_ACTIVE)))
    g.add((metric, DQV.inDimension, URIRef(indicator.principle_uri)))
    g.add((metric, FTR.supportedBy, URIRef(settings.tool_uri)))
    _add_responsible_agents(g, metric, settings)
    for test in test_uris:
        g.add((metric, HAS_IMPLEMENTATION, test))
    return metric


def tests_graph(settings_by_plugin, indicators):
    """Graph with the tests of every plugin in ``settings_by_plugin`` for ``indicators``."""
    g = new_graph()
    for plugin, settings in settings_by_plugin.items():
        for indicator in indicators:
            add_test(g, settings, plugin, indicator)
    return g


def metrics_graph(settings, plugins, indicators):
    """Graph with the metrics for ``indicators`` and their implementations in ``plugins``."""
    g = new_graph()
    ids = Identifiers(settings.base_uri)
    for indicator in indicators:
        tests = [ids.test(plugin, indicator.id) for plugin in plugins]
        add_metric(g, settings, indicator, tests)
    return g
