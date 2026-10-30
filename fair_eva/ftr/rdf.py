"""Content negotiation and RDF serialisation (Turtle and JSON-LD) of FTR graphs."""

from werkzeug.datastructures import MIMEAccept
from werkzeug.http import parse_accept_header

JSONLD = "application/ld+json"
TURTLE = "text/turtle"
JSON = "application/json"

# Media types the FTR endpoints can produce, in order of preference. ``application/json``
# is served as JSON-LD for clients that just ask for "JSON".
SUPPORTED = (JSONLD, TURTLE, JSON)

FORMAT_PARAMETERS = {
    "jsonld": JSONLD,
    "json-ld": JSONLD,
    "ld+json": JSONLD,
    "json": JSON,
    "turtle": TURTLE,
    "ttl": TURTLE,
}

# Inline JSON-LD context. It follows ``ftr_context.jsonld`` of the FTR release 1.3.0 (so
# the terms are the same) with two differences: ``sio`` is the *http* namespace, as in
# the SHACL shapes (the official context says https, which does not match them), and the
# dates are typed so that they are compacted to plain strings.
CONTEXT = {
    "dcat": "http://www.w3.org/ns/dcat#",
    "dcterms": "http://purl.org/dc/terms/",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "prov": "http://www.w3.org/ns/prov#",
    "ftr": "https://w3id.org/ftr#",
    "dqv": "http://www.w3.org/ns/dqv#",
    "vivo": "http://vivoweb.org/ontology/core#",
    "vcard": "http://www.w3.org/2006/vcard/ns#",
    "dpv": "https://w3id.org/dpv#",
    "sio": "http://semanticscience.org/resource/",
    "doap": "http://usefulinc.com/ns/doap#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "assessmentTarget": {"@id": "ftr:assessmentTarget"},
    "contactPoint": {"@id": "dcat:contactPoint"},
    "creator": {"@id": "dcterms:creator"},
    "publisher": {"@id": "dcterms:publisher"},
    "endpointDescription": {"@id": "dcat:endpointDescription"},
    "endpointURL": {"@id": "dcat:endpointURL"},
    "hadMember": {"@id": "prov:hadMember"},
    "hasImplementation": {"@id": "sio:SIO_000234"},
    "inDimension": {"@id": "dqv:inDimension"},
    "isApplicableFor": {"@id": "dpv:isApplicableFor"},
    "isImplementationOf": {"@id": "sio:SIO_000233"},
    "landingPage": {"@id": "dcat:landingPage"},
    "license": {"@id": "dcterms:license"},
    "outputFromTest": {"@id": "ftr:outputFromTest"},
    "repository": {"@id": "doap:repository"},
    "suggestion": {"@id": "ftr:suggestion"},
    "supportedBy": {"@id": "ftr:supportedBy"},
    "used": {"@id": "prov:used"},
    "wasAssociatedWith": {"@id": "prov:wasAssociatedWith"},
    "wasGeneratedBy": {"@id": "prov:wasGeneratedBy"},
    "abbreviation": {"@id": "vivo:abbreviation"},
    "completion": {"@id": "ftr:completion"},
    "description": {"@id": "dcterms:description"},
    "endedAtTime": {"@id": "prov:endedAtTime", "@type": "xsd:dateTime"},
    "startedAtTime": {"@id": "prov:startedAtTime", "@type": "xsd:dateTime"},
    "generatedAtTime": {"@id": "prov:generatedAtTime", "@type": "xsd:dateTime"},
    "identifier": {"@id": "dcterms:identifier"},
    "keyword": {"@id": "dcat:keyword"},
    "log": {"@id": "ftr:log"},
    "status": {"@id": "ftr:status"},
    "title": {"@id": "dcterms:title"},
    "value": {"@id": "prov:value"},
    "version": {"@id": "dcat:version"},
}


class FormatError(Exception):
    """The requested representation cannot be produced."""

    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def negotiate(accept=None, format_parameter=None):
    """Pick the media type of the response.

    The ``format`` query parameter, when given, takes precedence over the ``Accept``
    header. Without either, JSON-LD (the format of the FTR OpenAPI template) is used.
    """
    if format_parameter:
        try:
            return FORMAT_PARAMETERS[format_parameter.strip().lower()]
        except KeyError:
            raise FormatError(
                "Unsupported format %r, use one of: %s"
                % (format_parameter, ", ".join(sorted(FORMAT_PARAMETERS))),
                400,
            )
    if not accept or not accept.strip():
        return JSONLD
    best = parse_accept_header(accept, MIMEAccept).best_match(SUPPORTED)
    if best is None:
        raise FormatError(
            "None of the requested media types is available. Use one of: %s"
            % ", ".join(SUPPORTED),
            406,
        )
    return best


def serialize(graph, media_type):
    """Serialise ``graph`` as Turtle or (compact) JSON-LD."""
    if media_type == TURTLE:
        return graph.serialize(format="turtle")
    return graph.serialize(
        format="json-ld", context=CONTEXT, auto_compact=True, indent=2
    )
