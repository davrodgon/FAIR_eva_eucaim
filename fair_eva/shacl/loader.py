"""Loading of shapes documents (files, URLs, inline) with their provenance.

The SHA-256 of a document is computed over the **raw bytes exactly as they were
handed to the RDF parser**: the file content, the HTTP response body of this
execution, or the inline content (``str`` encoded as UTF-8).  Nothing is
decoded, re-serialised or canonicalised first, so the value can be reproduced
with ``sha256sum`` on the same file.

A *set* (``sources`` or a glob pattern) merges several documents into one
shapes graph.  Its SHA-256 is computed over the SHA-256 of its documents, sorted
and one per line (each followed by ``\n``); it does not depend on the order or
the names of the files and can be reproduced with::

    sha256sum <files> | cut -d' ' -f1 | LC_ALL=C sort | sha256sum
"""

from __future__ import annotations

import copy
import glob
import hashlib
import logging
import os
import pathlib
import re
import threading
from collections import OrderedDict
from typing import Any, Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import urlparse

import requests
from rdflib import RDF, BNode, Graph, Literal
from rdflib.namespace import DCTERMS, OWL, SH
from rdflib.util import guess_format

from fair_eva.shacl.errors import ShapesConfigError, ShapesLoadError
from fair_eva.shacl.models import (
    HASH_ALGORITHM,
    HASH_INPUT,
    HASH_INPUT_SET,
    ROLE_SHAPES,
    SOURCE_TYPE_SET,
    UNSPECIFIED,
    LoadedShapes,
    ShapesProvenance,
    ShapesSpec,
)

logger = logging.getLogger("api.shacl")

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_GLOB_RE = re.compile(r"[*?[]")

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


def has_glob(source: str) -> bool:
    """True for local paths with glob characters (URLs are never expanded)."""
    return bool(_GLOB_RE.search(source)) and not _SCHEME_RE.match(source)


def is_set(spec: ShapesSpec) -> bool:
    """True when ``spec`` describes a set of documents (``sources`` or a glob)."""
    return spec.sources is not None or (
        spec.source is not None and has_glob(spec.source)
    )


def documents_sha256(digests: List[str]) -> str:
    """SHA-256 of a set: over the sorted SHA-256 of its documents, one per line."""
    manifest = "".join("%s\n" % digest for digest in sorted(digests))
    return hashlib.sha256(manifest.encode("ascii")).hexdigest()


def default_id(spec: ShapesSpec, index: int = 0, role: str = ROLE_SHAPES) -> str:
    """Identifier of a spec: its ``id``, the file name, its name or ``<role>-<n>``."""
    if spec.id:
        return spec.id
    if spec.source and not has_glob(spec.source):
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


def _read_file(source: str, base_dir: Optional[str]) -> Tuple[bytes, str]:
    path = _resolve_local_path(source, base_dir)
    try:
        with open(path, "rb") as handle:
            return handle.read(), path
    except FileNotFoundError:
        raise ShapesLoadError("Shapes file not found: %s" % path) from None
    except OSError as exc:
        raise ShapesLoadError("Cannot read shapes file %s: %s" % (path, exc)) from exc


def _download(source: str, timeout: float) -> Tuple[bytes, str, Optional[str]]:
    try:
        response = requests.get(source, timeout=timeout, headers={"Accept": _ACCEPT})
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ShapesLoadError(
            "Could not download shapes from %s: %s" % (source, exc)
        ) from exc
    final_url = getattr(response, "url", None) or source
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
#: Number of parsed specs kept in memory (a document, or a whole set: its merged
#: graph and the metadata of its documents take a single entry).  The cache is
#: keyed by the SHA-256 of the bytes (plus format and base IRI), which are read --
#: and hashed -- again on every load, so provenance always describes the content
#: of that run; only the expensive parsing is skipped when the content did not
#: change.  Graphs handed out by :func:`load_shapes` are shared: treat them as
#: read-only.
GRAPH_CACHE_SIZE = 16
_graph_cache: "OrderedDict[Tuple, _Parsed]" = OrderedDict()
_graph_cache_lock = threading.Lock()


class _Parsed(NamedTuple):
    graph: Graph
    #: (triples, declared metadata, notes) of each document
    documents: List[Tuple[int, Dict[str, Any], List[str]]]


def clear_graph_cache() -> None:
    """Forget every cached parsed document."""
    with _graph_cache_lock:
        _graph_cache.clear()


def _cache_get(key: Tuple) -> Optional[_Parsed]:
    with _graph_cache_lock:
        cached = _graph_cache.get(key)
        if cached is not None:
            _graph_cache.move_to_end(key)
        return cached


def _cache_put(key: Tuple, value: _Parsed) -> None:
    with _graph_cache_lock:
        _graph_cache[key] = value
        while len(_graph_cache) > GRAPH_CACHE_SIZE:
            _graph_cache.popitem(last=False)


# --------------------------------------------------------------------------- #
# Reading the documents of a spec
# --------------------------------------------------------------------------- #
class _Document(NamedTuple):
    raw: bytes
    digest: str
    source: str
    source_type: str
    location: str
    final_url: Optional[str]
    fmt: str
    base: Optional[str]

    def summary(
        self, triples: int, declared: Dict[str, Any], notes: List[str]
    ) -> Dict[str, Any]:
        return {
            "source": self.source,
            "source_type": self.source_type,
            "final_url": self.final_url,
            "format": self.fmt,
            "sha256": self.digest,
            "size_bytes": len(self.raw),
            "triples": triples,
            "declared_in_file": declared,
            "notes": notes,
        }


def _expand_sources(spec: ShapesSpec) -> List[str]:
    """Paths / URLs of a set, glob patterns expanded (sorted) and duplicates removed."""
    patterns = list(spec.sources) if spec.sources is not None else [spec.source]
    expanded: List[str] = []
    seen = set()
    for pattern in patterns:
        if has_glob(pattern):
            full = _resolve_local_path(pattern, spec.base_dir)
            candidates = sorted(
                path for path in glob.glob(full, recursive=True) if os.path.isfile(path)
            )
            if not candidates:
                raise ShapesLoadError("No shapes file matches %s" % full)
        else:
            candidates = [pattern]
        for candidate in candidates:
            key = (
                candidate
                if is_url(candidate)
                else _resolve_local_path(candidate, spec.base_dir)
            )
            if key not in seen:
                seen.add(key)
                expanded.append(candidate)
    return expanded


def _read_document(spec: ShapesSpec, source: Optional[str], set_id: str) -> _Document:
    """Read (without parsing) one document: inline ``content`` or ``source``."""
    final_url = None
    content_type = None
    if source is None:
        raw = (
            spec.content.encode("utf-8")
            if isinstance(spec.content, str)
            else bytes(spec.content)
        )
        source, source_type = "<inline>", "inline"
        location = "<inline:%s>" % set_id
    elif is_url(source):
        raw, final_url, content_type = _download(source, spec.timeout)
        source_type, location = "url", source
    else:
        raw, source = _read_file(source, spec.base_dir)
        source_type, location = "file", source
    fmt = _detect_format(spec, location, content_type)
    base = pathlib.Path(source).as_uri() if source_type == "file" else final_url
    return _Document(
        raw=raw,
        digest=hashlib.sha256(raw).hexdigest(),
        source=source,
        source_type=source_type,
        location=location,
        final_url=final_url,
        fmt=fmt,
        base=base,
    )


def _parse_document(document: _Document, label: str) -> Graph:
    graph = Graph()
    try:
        graph.parse(data=document.raw, format=document.fmt, publicID=document.base)
    except Exception as exc:
        raise ShapesLoadError(
            "Could not parse %s as %s: %s" % (label, document.fmt, exc)
        ) from exc
    if len(graph) == 0:
        raise ShapesLoadError("The %s contains no RDF triples" % label)
    return graph


def _parse_all(documents: List[_Document], role: str, set_id: str) -> _Parsed:
    """Parse the documents of a spec and merge them (cached as a whole)."""
    key = tuple((d.digest, d.fmt, d.base) for d in documents)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    graphs = [
        _parse_document(d, "%s '%s' (%s)" % (role, set_id, d.source)) for d in documents
    ]
    if len(graphs) == 1:
        graph = graphs[0]
    else:
        graph = Graph()
        for part in graphs:
            for prefix, namespace in part.namespaces():
                graph.bind(prefix, namespace, override=False)
            graph += part
    parsed = _Parsed(
        graph=graph,
        documents=[(len(g),) + declared_metadata(g) for g in graphs],
    )
    _cache_put(key, parsed)
    return parsed


def _declared_by_set(
    declared: List[Dict[str, Any]], notes: List[str]
) -> Dict[str, Any]:
    """Title / version declared by a set: only when its documents agree."""
    versions = sorted({d["version"] for d in declared if d["version"]})
    titles = sorted({d["title"] for d in declared if d["title"]})
    meta: Dict[str, Any] = {
        "title": titles[0] if len(titles) == 1 else None,
        "version": None,
        "version_property": None,
        "imports": sorted({i for d in declared for i in d["imports"]}),
    }
    if len(versions) == 1:
        meta["version"] = versions[0]
        properties = {d["version_property"] for d in declared if d["version"]}
        meta["version_property"] = properties.pop() if len(properties) == 1 else None
        declaring = sum(1 for d in declared if d["version"])
        if declaring < len(declared):
            notes.append(
                "Version %s declared by %d of %d documents"
                % (versions[0], declaring, len(declared))
            )
    elif len(versions) > 1:
        notes.append(
            "The documents declare different versions (%s); ignored as ambiguous"
            % ", ".join(versions)
        )
    return meta


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def load_shapes(spec: Any, index: int = 0, role: str = ROLE_SHAPES) -> LoadedShapes:
    """Load one shapes document (or a set of them) and build its provenance.

    Parameters
    ----------
    spec : ShapesSpec, mapping or str
        See :class:`~fair_eva.shacl.models.ShapesSpec`.  ``sources`` or a glob
        ``source`` load a set: its documents are merged into one graph.
    index : int
        Position used to build a default identifier.
    role : str
        ``"shapes"`` or ``"ontology"`` (recorded in the provenance).

    Raises
    ------
    ShapesConfigError
        Invalid specification (unknown format, unsupported URL scheme...).
    ShapesLoadError
        Missing file, pattern matching no file, download error, parse error,
        empty document or ``expected_sha256`` mismatch.
    """
    spec = ShapesSpec.coerce(spec)
    set_id = default_id(spec, index, role)
    multiple = is_set(spec)

    sources = _expand_sources(spec) if multiple else [spec.source]
    documents = [_read_document(spec, source, set_id) for source in sources]
    if multiple:
        digest = documents_sha256([d.digest for d in documents])
        source = ", ".join(spec.sources if spec.sources is not None else [spec.source])
    else:
        digest, source = documents[0].digest, documents[0].source
    if spec.expected_sha256 and spec.expected_sha256 != digest:
        raise ShapesLoadError(
            "SHA-256 mismatch for %s '%s' (%s): expected %s, got %s"
            % (role, set_id, source, spec.expected_sha256, digest)
        )

    parsed = _parse_all(documents, role, set_id)
    graph = parsed.graph
    summaries = [
        # copies: the parsed metadata is shared through the cache
        document.summary(triples, copy.deepcopy(declared), list(doc_notes))
        for document, (triples, declared, doc_notes) in zip(documents, parsed.documents)
    ]
    declared_list = [summary["declared_in_file"] for summary in summaries]
    notes: List[str] = []
    if multiple:
        declared = _declared_by_set(declared_list, notes)
    else:
        declared = declared_list[0]
        notes.extend(summaries[0]["notes"])

    discrepancies: List[Dict[str, Optional[str]]] = []

    # Version: configuration wins; the file only fills the gap.  A version is
    # never invented.
    if spec.version:
        version, version_source = spec.version, "config"
        for document, doc_declared in zip(documents, declared_list):
            if doc_declared["version"] and doc_declared["version"] != spec.version:
                discrepancy = {
                    "field": "version",
                    "config": spec.version,
                    "file": doc_declared["version"],
                }
                if multiple:
                    discrepancy["document"] = document.source
                discrepancies.append(discrepancy)
        if discrepancies:
            logger.warning(
                "Version of %s '%s' differs between configuration (%s) and %d "
                "document(s) (%s)",
                role,
                set_id,
                spec.version,
                len(discrepancies),
                ", ".join(sorted({d["file"] for d in discrepancies})),
            )
    elif declared["version"]:
        version, version_source = declared["version"], "file"
    else:
        version, version_source = UNSPECIFIED, UNSPECIFIED

    if spec.name:
        name, name_source = spec.name, "config"
        # The documents of a set are usually modules with their own titles:
        # only a single document is compared with the configured name.
        if (
            not multiple
            and declared["title"]
            and not _same_text(declared["title"], spec.name)
        ):
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

    formats = sorted({d.fmt for d in documents})
    provenance = ShapesProvenance(
        role=role,
        id=set_id,
        name=name,
        name_source=name_source,
        version=version,
        version_source=version_source,
        sha256=digest,
        source=source,
        source_type=SOURCE_TYPE_SET if multiple else documents[0].source_type,
        format=formats[0] if len(formats) == 1 else "mixed",
        size_bytes=sum(len(d.raw) for d in documents),
        triples=len(graph),
        final_url=None if multiple else documents[0].final_url,
        hash_algorithm=HASH_ALGORITHM,
        hash_input=HASH_INPUT_SET if multiple else HASH_INPUT,
        declared_in_file={
            "title": declared["title"],
            "version": declared["version"],
            "version_property": declared["version_property"],
            "imports": declared["imports"],
        },
        discrepancies=discrepancies,
        notes=notes,
        documents=summaries,
    )
    logger.debug("Loaded %s", provenance.describe())
    return LoadedShapes(graph=graph, provenance=provenance)
