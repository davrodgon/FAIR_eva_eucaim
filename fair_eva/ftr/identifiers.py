"""Deterministic construction of the IRIs used in the FTR output.

Every IRI minted here hangs from the configurable ``[ftr] base_uri``. Choosing that base
(and registering it, e.g. at w3id.org) is a decision of the deployer: FAIR EVA never
invents persistent identifiers of its own.

====================  =====================================================
Resource              IRI
====================  =====================================================
Test                  ``{base}/test/{plugin}/{indicator}``
Metric                ``{base}/metric/{indicator}``
Test result           ``{base}/result/{uuid5}``
Test result set       ``{base}/resultset/{uuid5}``
Execution activity    ``{base}/execution/{uuid5}``
Suggestion            ``{result}/suggestion/{n}``
Target (no PID)       ``{base}/resource/{plugin}/{identifier}``
====================  =====================================================

The ``uuid5`` values are derived from the test, the assessed resource and the end time of
the execution, so the same execution always gets the same IRIs.
"""

import re
import uuid
from urllib.parse import quote

import idutils
from rdflib import URIRef

# Identifier schemes whose resolver URL is a safe IRI for the assessed resource. Other
# schemes detected by idutils (e.g. pmid for a purely numeric id) are too ambiguous for
# the internal identifiers that repositories use.
RESOLVABLE_SCHEMES = ("doi", "handle", "ark", "purl", "url")

_HTTP = re.compile(r"^https?://", re.IGNORECASE)
# idutils accepts almost any "something/else" as a handle; a real handle has a numeric
# prefix ("10.1234/abc", "20.500.12345/abc"), which is what is required here.
_HANDLE = re.compile(r"^(?:hdl:)?\d+(?:\.\d+)*/\S+$", re.IGNORECASE)


def segment(value):
    """Percent-encode a value so it is a single, safe IRI path segment."""
    return quote(str(value), safe="")


def _escape_iri(value):
    return quote(value, safe=":/?#[]@!$&'()*+,;=%-._~")


class Identifiers:
    def __init__(self, base_uri):
        self.base = base_uri.rstrip("/")

    def test(self, plugin, indicator):
        return URIRef(
            "%s/test/%s/%s" % (self.base, segment(plugin), segment(indicator))
        )

    def metric(self, indicator):
        return URIRef("%s/metric/%s" % (self.base, segment(indicator)))

    def _uuid(self, *parts):
        return uuid.uuid5(uuid.NAMESPACE_URL, "|".join(str(p) for p in parts))

    def result(self, test_uri, target_uri, ended_at):
        key = self._uuid(test_uri, target_uri, ended_at.isoformat())
        return URIRef("%s/result/%s" % (self.base, key))

    def execution(self, test_uri, target_uri, ended_at):
        key = self._uuid("execution", test_uri, target_uri, ended_at.isoformat())
        return URIRef("%s/execution/%s" % (self.base, key))

    def result_set(self, test_uris, target_uri, ended_at):
        key = self._uuid("set", *sorted(test_uris), target_uri, ended_at.isoformat())
        return URIRef("%s/resultset/%s" % (self.base, key))

    def set_execution(self, test_uris, target_uri, ended_at):
        key = self._uuid(
            "set-execution", *sorted(test_uris), target_uri, ended_at.isoformat()
        )
        return URIRef("%s/execution/%s" % (self.base, key))

    @staticmethod
    def suggestion(result_uri, number):
        return URIRef("%s/suggestion/%d" % (result_uri, number))

    def target(self, plugin, identifier):
        """IRI of the assessed resource (``ftr:assessmentTarget``).

        The shapes require an IRI. URLs are used as they are, DOIs, handles, etc. are
        turned into their resolver URL and anything else (an identifier that is only
        meaningful inside a repository) gets an IRI under the base URI. In every case
        the original identifier is kept in ``dcterms:identifier`` of the target.
        """
        value = str(identifier).strip()
        if _HTTP.match(value):
            return URIRef(_escape_iri(value))
        if value.lower().startswith("urn:"):
            return URIRef(_escape_iri(value))
        try:
            schemes = idutils.detect_identifier_schemes(value)
        except Exception:  # pragma: no cover - defensive, idutils is permissive
            schemes = []
        for scheme in schemes:
            if scheme not in RESOLVABLE_SCHEMES:
                continue
            if scheme == "handle" and not _HANDLE.match(value):
                continue
            try:
                url = idutils.to_url(value, scheme, url_scheme="https")
            except Exception:  # pragma: no cover - defensive
                continue
            if url and _HTTP.match(url):
                return URIRef(_escape_iri(url))
        return URIRef(
            "%s/resource/%s/%s" % (self.base, segment(plugin), segment(value))
        )
