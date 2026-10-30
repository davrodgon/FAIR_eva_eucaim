"""Namespaces and constants of the FTR vocabulary and of the vocabularies it reuses."""

from rdflib import Namespace
from rdflib.namespace import DCTERMS, PROV, RDFS, XSD

FTR = Namespace("https://w3id.org/ftr#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
DQV = Namespace("http://www.w3.org/ns/dqv#")
DOAP = Namespace("http://usefulinc.com/ns/doap#")
VIVO = Namespace("http://vivoweb.org/ontology/core#")
VCARD = Namespace("http://www.w3.org/2006/vcard/ns#")
DPV = Namespace("https://w3id.org/dpv#")
# NOTE: the release 1.3.0 SHACL shapes (and the Turtle examples of the
# specification) use the *http* SIO namespace, whereas the official JSON-LD
# context (ftr_context.jsonld) maps "sio" to the *https* one. The shapes are the
# validation source of truth, so the http namespace is used. See docs/ftr_ostrails.md.
SIO = Namespace("http://semanticscience.org/resource/")

# Property that links a test with the metric it implements ("is implementation of")
# and its inverse ("has implementation").
IS_IMPLEMENTATION_OF = SIO["SIO_000233"]
HAS_IMPLEMENTATION = SIO["SIO_000234"]

# Values of prov:value for a test result.
PASS = "pass"
FAIL = "fail"
INDETERMINATE = "indeterminate"

# Prefixes bound in the serialised graphs.
PREFIXES = {
    "ftr": FTR,
    "dcterms": DCTERMS,
    "dcat": DCAT,
    "dqv": DQV,
    "doap": DOAP,
    "vivo": VIVO,
    "vcard": VCARD,
    "dpv": DPV,
    "sio": SIO,
    "prov": PROV,
    "rdfs": RDFS,
    "xsd": XSD,
}
