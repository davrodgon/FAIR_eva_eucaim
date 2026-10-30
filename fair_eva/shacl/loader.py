"""Loading of shapes documents (files, URLs, inline) with their provenance.

The SHA-256 of a document is computed over the **raw bytes exactly as they were
handed to the RDF parser**: the file content, the HTTP response body of this
execution, or the inline content (``str`` encoded as UTF-8).  Nothing is
decoded, re-serialised or canonicalised first, so the value can be reproduced
with ``sha256sum`` on the same file.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pathlib
import re
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from rdflib import RDF, BNode, Graph, Literal
from rdflib.namespace import DCTERMS, OWL, SH
from rdflib.util import guess_format

from fair_eva.shacl.errors import ShapesConfigError, ShapesLoadError
from fair_eva.shacl.models import (
    HASH_ALGORITHM,
    HASH_INPUT,
    ROLE_SHAPES,
    UNSPECIFIED,
    LoadedShapes,
    ShapesProvenance,
    ShapesSpec,
)

logger = logging.getLogger("api.shacl")

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

#: Accepted spellings of the supported RDF serialisations -> rdflib parser name.
FORMAT_ALIASES = {
    "turtle": "turtle",
    "ttl": "turtle",
    "json-ld": "json-ld",
    "jsonld": "json-ld",
    "xml": "xml",
    "rdf": "xml",
    "rdf/xml": "xml",
    "rdfxml": "xml",
    "n3": "n3",
    "nt": "nt",
    "ntriples": "nt",
    "n-triples": "nt",
}

_MEDIA_TYPES = {
    "text/turtle": "turtle",
    "application/x-turtle": "turtle",
    "application/ld+json": "json-ld",
    "application/rdf+xml": "xml",
    "application/n-triples": "nt",
    "text/n3": "n3",
}

_ACCEPT = (
    "text/turtle, application/ld+json;q=0.9, application/rdf+xml;q=0.8, "
    "application/n-triples;q=0.7, text/n3;q=0.6, */*;q=0.1"
)


def normalize_format(fmt: str) -> str:
    """Map a format name or alias to the rdflib parser name."""
    key = str(fmt).strip().lower()
    try:
        return FORMAT_ALIASES[key]
    except KeyError:
        raise ShapesConfigError(
            "Unsupported RDF format %r (supported: %s)"
            % (fmt, ", ".join(sorted(set(FORMAT_ALIASES.values()))))
        ) from None


def is_url(source: str) -> bool:
    """True for ``http(s)://`` sources; other URL schemes are rejected."""
    if not _SCHEME_RE.match(source):
        return False
    if urlparse(source).scheme.lower() not in ("http", "https"):
        raise ShapesConfigError(
            "Unsupported URL scheme in %r (only http and https are allowed)" % source
        )
    return True


def default_id(spec: ShapesSpec, index: int = 0, role: str = ROLE_SHAPES) -> str:
    """Identifier of a spec: its ``id``, the file name, its name or ``<role>-<n>``."""
    if spec.id:
        return spec.id
    if spec.source:
        path = urlparse(spec.source).path if is_url(spec.source) else spec.source
        stem = os.path.splitext(os.path.basename(path.rstrip("/\\")))[0]
        if stem:
            return stem
    if spec.name:
        return spec.name
    return "%s-%d" % (role, index + 1)


def _resolve_local_path(source: str, base_dir: Optional[str]) -> str:
    path = os.path.expanduser(source)
    if not os.path.isabs(path):
        path = os.path.join(base_dir or os.getcwd(), path)
    return os.path.abspath(path)


def _read_file(spec: ShapesSpec) -> Tuple[bytes, str]:
    path = _resolve_local_path(spec.source, spec.base_dir)
    try:
        with open(path, "rb") as handle:
            return handle.read(), path
    except FileNotFoundError:
        raise ShapesLoadError("Shapes file not found: %s" % path) from None
    except OSError as exc:
        raise ShapesLoadError("Cannot read shapes file %s: %s" % (path, exc)) from exc


def _download(spec: ShapesSpec) -> Tuple[bytes, str, Optional[str]]:
    try:
        response = requests.get(
            spec.source, timeout=spec.timeout, headers={"Accept": _ACCEPT}
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ShapesLoadError(
            "Could not download shapes from %s: %s" % (spec.source, exc)
        ) from exc
    final_url = getattr(response, "url", None) or spec.source
    content_type = (response.headers or {}).get("Content-Type")
    return response.content, final_url, content_type


def _detect_format(spec: ShapesSpec, location: str, content_type: Optional[str]) -> str:
    if spec.format:
        return normalize_format(spec.format)
    if spec.content is not None:
        return "turtle"
    if content_type:
        media = content_type.split(";")[0].strip().lower()
        if media in _MEDIA_TYPES:
            return _MEDIA_TYPES[media]
    guessed = guess_format(urlparse(location).path if is_url(location) else location)
    if guessed in set(FORMAT_ALIASES.values()):
        return guessed
    raise ShapesLoadError(
        "Cannot determine the RDF format of %s; set 'format' explicitly" % location
    )


# --------------------------------------------------------------------------- #
# Metadata declared in the document itself
# --------------------------------------------------------------------------- #
def _distinct_literals(graph: Graph, subject, predicate) -> List[Literal]:
    seen = {}
    for obj in graph.objects(subject, predicate):
        if isinstance(obj, Literal) and str(obj).strip():
            seen.setdefault(str(obj).strip(), obj)
    return [seen[key] for key in sorted(seen)]


def _pick_title(values: List[Literal]) -> Optional[str]:
    """Prefer a language-less title, then English, then the first one."""
    if not values:
        return None
    for wanted in (None, "en"):
        for value in values:
            lang = (value.language or "").split("-")[0].lower() or None
            if lang == wanted:
                return str(value).strip()
    return str(values[0]).strip()


def declared_metadata(graph: Graph) -> Tuple[Dict[str, Any], List[str]]:
    """Title / version / imports declared by the ``owl:Ontology`` of a document.

    Only resources typed ``owl:Ontology`` are considered (shapes themselves may
    carry unrelated titles).  Returns ``(metadata, notes)``; ambiguous
    declarations are ignored and reported in ``notes``.
    """
    notes: List[str] = []
    meta: Dict[str, Any] = {
        "title": None,
        "version": None,
        "version_property": None,
        "imports": [],
    }
    candidates = sorted(set(graph.subjects(RDF.type, OWL.Ontology)), key=str)
    if not candidates:
        return meta, notes
    props = (OWL.versionInfo, DCTERMS.hasVersion, DCTERMS.title)
    if len(candidates) > 1:
        with_meta = [s for s in candidates if any((s, p, None) in graph for p in props)]
        if len(with_meta) != 1:
            notes.append(
                "%d owl:Ontology resources found; title/version declared in the "
                "file ignored as ambiguous" % len(candidates)
            )
            return meta, notes
        candidates = with_meta
    ontology = candidates[0]

    meta["imports"] = sorted(str(o) for o in graph.objects(ontology, OWL.imports))
    meta["title"] = _pick_title(_distinct_literals(graph, ontology, DCTERMS.title))

    for prop, curie in (
        (OWL.versionInfo, "owl:versionInfo"),
        (DCTERMS.hasVersion, "dcterms:hasVersion"),
    ):
        values = _distinct_literals(graph, ontology, prop)
        if len(values) == 1:
            if meta["version"] is None:
                meta["version"] = str(values[0]).strip()
                meta["version_property"] = curie
            elif meta["version"] != str(values[0]).strip():
                notes.append(
                    "%s (%s) differs from the version taken from %s (%s); using the latter"
                    % (curie, values[0], meta["version_property"], meta["version"])
                )
        elif len(values) > 1:
            notes.append(
                "%s has several distinct values (%s); ignored as ambiguous"
                % (curie, ", ".join(str(v) for v in values))
            )
    return meta, notes


def _same_text(a: Optional[str], b: Optional[str]) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


# --------------------------------------------------------------------------- #
# Parsed-graph cache
# --------------------------------------------------------------------------- #
#: Number of parsed documents kept in memory.  The cache is keyed by the SHA-256
#: of the bytes (plus format and base IRI), which are read -- and hashed -- again
#: on every load, so provenance always describes the content of that run; only
#: the expensive parsing is skipped when the content did not change.  Graphs
#: handed out by :func:`load_shapes` are shared: treat them as read-only.
GRAPH_CACHE_SIZE = 16
_graph_cache: "OrderedDict[Tuple[str, str, Optional[str]], Graph]" = OrderedDict()
_graph_cache_lock = threading.Lock()


def clear_graph_cache() -> None:
    """Forget every cached parsed document."""
    with _graph_cache_lock:
        _graph_cache.clear()


def _parse_cached(
    raw: bytes, fmt: str, base: Optional[str], digest: str, label: str
) -> Graph:
    key = (digest, fmt, base)
    with _graph_cache_lock:
        cached = _graph_cache.get(key)
        if cached is not None:
            _graph_cache.move_to_end(key)
            return cached
    graph = Graph()
    try:
        graph.parse(data=raw, format=fmt, publicID=base)
    except Exception as exc:
        raise ShapesLoadError(
            "Could not parse %s as %s: %s" % (label, fmt, exc)
        ) from exc
    if len(graph) == 0:
        raise ShapesLoadError("The %s contains no RDF triples" % label)
    with _graph_cache_lock:
        _graph_cache[key] = graph
        while len(_graph_cache) > GRAPH_CACHE_SIZE:
            _graph_cache.popitem(last=False)
    return graph


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def load_shapes(spec: Any, index: int = 0, role: str = ROLE_SHAPES) -> LoadedShapes:
    """Load one shapes document and build its provenance record.

    Parameters
    ----------
    spec : ShapesSpec, mapping or str
        See :class:`~fair_eva.shacl.models.ShapesSpec`.
    index : int
        Position used to build a default identifier.
    role : str
        ``"shapes"`` or ``"ontology"`` (recorded in the provenance).

    Raises
    ------
    ShapesConfigError
        Invalid specification (unknown format, unsupported URL scheme...).
    ShapesLoadError
        Missing file, download error, parse error, empty document or
        ``expected_sha256`` mismatch.
    """
    spec = ShapesSpec.coerce(spec)
    set_id = default_id(spec, index, role)

    final_url = None
    content_type = None
    if spec.content is not None:
        raw = (
            spec.content.encode("utf-8")
            if isinstance(spec.content, str)
            else bytes(spec.content)
        )
        source, source_type = "<inline>", "inline"
        location = "<inline:%s>" % set_id
    elif is_url(spec.source):
        raw, final_url, content_type = _download(spec)
        source, source_type, location = spec.source, "url", spec.source
    else:
        raw, source = _read_file(spec)
        source_type, location = "file", source

    fmt = _detect_format(spec, location, content_type)
    digest = hashlib.sha256(raw).hexdigest()
    if spec.expected_sha256 and spec.expected_sha256 != digest:
        raise ShapesLoadError(
            "SHA-256 mismatch for %s '%s' (%s): expected %s, got %s"
            % (role, set_id, source, spec.expected_sha256, digest)
        )

    base = final_url
    if source_type == "file":
        base = pathlib.Path(source).as_uri()
    graph = _parse_cached(
        raw, fmt, base, digest, "%s '%s' (%s)" % (role, set_id, source)
    )

    declared, notes = declared_metadata(graph)
    discrepancies: List[Dict[str, Optional[str]]] = []

    # Version: configuration wins; the file only fills the gap.  A version is
    # never invented.
    if spec.version:
        version, version_source = spec.version, "config"
        if declared["version"] and declared["version"] != spec.version:
            discrepancies.append(
                {
                    "field": "version",
                    "config": spec.version,
                    "file": declared["version"],
                }
            )
            logger.warning(
                "Version of %s '%s' differs between configuration (%s) and file (%s)",
                role,
                set_id,
                spec.version,
                declared["version"],
            )
    elif declared["version"]:
        version, version_source = declared["version"], "file"
    else:
        version, version_source = UNSPECIFIED, UNSPECIFIED

    if spec.name:
        name, name_source = spec.name, "config"
        if declared["title"] and not _same_text(declared["title"], spec.name):
            discrepancies.append(
                {"field": "name", "config": spec.name, "file": declared["title"]}
            )
    elif declared["title"]:
        name, name_source = declared["title"], "file"
    else:
        name, name_source = set_id, "id"

    if role == ROLE_SHAPES and not any(
        str(p).startswith(str(SH)) for p in graph.predicates()
    ):
        notes.append(
            "No SHACL (sh:) properties found: validating against this document "
            "is vacuous"
        )

    provenance = ShapesProvenance(
        role=role,
        id=set_id,
        name=name,
        name_source=name_source,
        version=version,
        version_source=version_source,
        sha256=digest,
        source=source,
        source_type=source_type,
        format=fmt,
        size_bytes=len(raw),
        triples=len(graph),
        final_url=final_url,
        hash_algorithm=HASH_ALGORITHM,
        hash_input=HASH_INPUT,
        declared_in_file={
            "title": declared["title"],
            "version": declared["version"],
            "version_property": declared["version_property"],
            "imports": declared["imports"],
        },
        discrepancies=discrepancies,
        notes=notes,
    )
    logger.debug("Loaded %s", provenance.describe())
    return LoadedShapes(graph=graph, provenance=provenance)
