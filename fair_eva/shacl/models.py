"""Data model of the SHACL validation: specifications, provenance and reports."""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from rdflib import Graph

from fair_eva.shacl.errors import ShapesConfigError

#: Value used when no version is declared (neither in the configuration nor in
#: the shapes document).  A version is never invented.
UNSPECIFIED = "unspecified"

#: Hash algorithm and what it is computed over (see ``docs/shacl_validation.md``).
HASH_ALGORITHM = "sha256"
HASH_INPUT = "raw-bytes"

ROLE_SHAPES = "shapes"
ROLE_ONTOLOGY = "ontology"

SEVERITY_VIOLATION = "Violation"
SEVERITY_WARNING = "Warning"
SEVERITY_INFO = "Info"

COMBINE_SEPARATE = "separate"
COMBINE_MERGED = "merged"
COMBINE_MODES = (COMBINE_SEPARATE, COMBINE_MERGED)

INFERENCE_MODES = ("none", "rdfs", "owlrl", "both")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def normalize_sha256(value: str) -> str:
    """Return ``value`` as 64 lowercase hex digits (optional ``sha256:`` prefix)."""
    text = str(value).strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:") :]
    if not _SHA256_RE.match(text):
        raise ShapesConfigError(
            "expected_sha256 must be 64 hexadecimal digits (optionally prefixed "
            "with 'sha256:'), got %r" % value
        )
    return text


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


# --------------------------------------------------------------------------- #
# Input specification
# --------------------------------------------------------------------------- #
@dataclass
class ShapesSpec:
    """Where a SHACL shapes document (or an ontology) comes from.

    Exactly one of ``source`` and ``content`` must be given.

    Attributes
    ----------
    source : str
        Local path or ``http(s)://`` URL.
    content : str or bytes
        The document itself (inline).  Its SHA-256 is computed over these bytes
        (``str`` is encoded as UTF-8).
    format : str
        RDF serialisation (``turtle``, ``json-ld``, ``xml``, ``n3``, ``nt`` and
        common aliases).  Guessed from the extension / ``Content-Type`` when
        omitted for ``source``; defaults to ``turtle`` for inline ``content``.
    id : str
        Identifier used to select the set and to attribute results.  Defaults
        to the file name (without extension), the name or ``shapes-<n>``.
    name, version : str
        Human metadata.  When omitted, they are read from the document
        (``dcterms:title`` / ``owl:versionInfo`` / ``dcterms:hasVersion`` on an
        ``owl:Ontology``).  If no version is available it is ``"unspecified"``.
    expected_sha256 : str
        Optional pin.  Loading fails if the content has a different SHA-256.
    timeout : float
        Download timeout in seconds (URLs only).
    base_dir : str
        Directory against which a relative local ``source`` is resolved
        (default: the current working directory).
    """

    source: Optional[str] = None
    content: Optional[Union[str, bytes]] = None
    format: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    version: Optional[str] = None
    expected_sha256: Optional[str] = None
    timeout: float = 30.0
    base_dir: Optional[str] = None

    def __post_init__(self):
        if (self.source is None) == (self.content is None):
            raise ShapesConfigError(
                "A shapes specification needs exactly one of 'source' or 'content'"
            )
        if self.source is not None:
            self.source = _clean(self.source)
            if not self.source:
                raise ShapesConfigError(
                    "The 'source' of a shapes specification is empty"
                )
        elif not isinstance(self.content, (str, bytes)):
            raise ShapesConfigError("'content' must be str or bytes")
        self.format = _clean(self.format)
        self.id = _clean(self.id)
        self.name = _clean(self.name)
        self.version = _clean(self.version)
        if self.expected_sha256 is not None:
            self.expected_sha256 = normalize_sha256(self.expected_sha256)
        try:
            self.timeout = float(self.timeout)
        except (TypeError, ValueError):
            raise ShapesConfigError("'timeout' must be a number") from None
        if self.timeout <= 0:
            raise ShapesConfigError("'timeout' must be greater than zero")

    @classmethod
    def coerce(cls, value: Any) -> "ShapesSpec":
        """Build a spec from a ``ShapesSpec``, a path/URL string or a mapping."""
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            return cls(source=value)
        if isinstance(value, Mapping):
            known = {f.name for f in dataclasses.fields(cls)}
            unknown = sorted(set(value) - known)
            if unknown:
                raise ShapesConfigError(
                    "Unknown key(s) in shapes specification: %s (allowed: %s)"
                    % (", ".join(unknown), ", ".join(sorted(known)))
                )
            return cls(**value)
        raise ShapesConfigError(
            "Cannot interpret %s as a shapes specification" % type(value).__name__
        )


@dataclass
class ValidationOptions:
    """Options passed to the SHACL engine (all off / neutral by default)."""

    inference: str = "none"
    advanced: bool = False
    allow_infos: bool = False
    allow_warnings: bool = False
    abort_on_first: bool = False
    meta_shacl: bool = False
    allow_empty_data: bool = False

    def __post_init__(self):
        self.inference = str(self.inference).strip().lower()
        if self.inference not in INFERENCE_MODES:
            raise ShapesConfigError(
                "inference must be one of %s, got %r"
                % (", ".join(INFERENCE_MODES), self.inference)
            )

    @classmethod
    def coerce(cls, value: Any) -> "ValidationOptions":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            known = {f.name for f in dataclasses.fields(cls)}
            unknown = sorted(set(value) - known)
            if unknown:
                raise ShapesConfigError(
                    "Unknown validation option(s): %s (allowed: %s)"
                    % (", ".join(unknown), ", ".join(sorted(known)))
                )
            return cls(**value)
        raise ShapesConfigError(
            "Cannot interpret %s as validation options" % type(value).__name__
        )

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# --------------------------------------------------------------------------- #
# Provenance of the loaded documents
# --------------------------------------------------------------------------- #
@dataclass
class ShapesProvenance:
    """Traceability record of one loaded shapes document (or ontology).

    ``sha256`` is the SHA-256 of the exact bytes that were handed to the RDF
    parser (``hash_input == "raw-bytes"``): no decoding, newline normalisation
    or RDF canonicalisation.  For URLs it is the hash of the content downloaded
    in this execution.
    """

    role: str
    id: str
    name: str
    name_source: str  # "config" | "file" | "id"
    version: str
    version_source: str  # "config" | "file" | "unspecified"
    sha256: str
    source: str
    source_type: str  # "file" | "url" | "inline"
    format: str
    size_bytes: int
    triples: int
    final_url: Optional[str] = None
    hash_algorithm: str = HASH_ALGORITHM
    hash_input: str = HASH_INPUT
    declared_in_file: Dict[str, Any] = field(default_factory=dict)
    discrepancies: List[Dict[str, Optional[str]]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def has_discrepancies(self) -> bool:
        return bool(self.discrepancies)

    def describe(self) -> str:
        """One-line human readable description (used in indicator messages)."""
        text = "%s '%s' (id=%s) version %s, %s %s, source: %s" % (
            self.role,
            self.name,
            self.id,
            self.version,
            self.hash_algorithm,
            self.sha256,
            self.source,
        )
        if self.discrepancies:
            text += (
                " [%d metadata discrepancy(ies) between configuration and file]"
                % len(self.discrepancies)
            )
        return text

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class LoadedShapes:
    """A parsed shapes document together with its provenance.

    The provenance describes the content at load time.  A ``LoadedShapes`` can
    be passed to :func:`fair_eva.shacl.validate` to reuse it across several
    validations without downloading / parsing it again.
    """

    graph: Graph = field(repr=False)
    provenance: ShapesProvenance


# --------------------------------------------------------------------------- #
# Results and report
# --------------------------------------------------------------------------- #
@dataclass
class ShaclResult:
    """One ``sh:ValidationResult`` flattened to plain strings."""

    severity: str
    focus_node: str
    message: str
    path: Optional[str] = None
    value: Optional[str] = None
    source_shape: Optional[str] = None
    constraint_component: Optional[str] = None
    shapes_id: Optional[str] = None
    messages: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def describe(self) -> str:
        where = "focus node %s" % self.focus_node
        if self.path:
            where += ", path %s" % self.path
        prefix = "[%s]" % self.severity
        if self.shapes_id:
            prefix += " (%s)" % self.shapes_id
        return "%s %s: %s" % (prefix, where, self.message)


@dataclass
class ValidationRun:
    """Outcome of one engine invocation.

    In ``separate`` mode there is one run per shape set; in ``merged`` mode a
    single run covers all sets.  ``results_graph`` and ``shapes_graph`` are the
    raw rdflib graphs (not serialised) for plugins that need custom scoring,
    e.g. walking ``sh:sourceShape`` back into the shapes graph.
    """

    shapes: List[str]
    conforms: bool
    violations: int = 0
    warnings: int = 0
    infos: int = 0
    results_graph: Optional[Graph] = field(default=None, repr=False, compare=False)
    shapes_graph: Optional[Graph] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shapes": list(self.shapes),
            "conforms": self.conforms,
            "violations": self.violations,
            "warnings": self.warnings,
            "infos": self.infos,
        }


_GROUP_KEYS = (
    "focus_node",
    "path",
    "severity",
    "shapes_id",
    "source_shape",
    "constraint_component",
)


@dataclass
class ShaclReport:
    """Result of a SHACL validation, with the provenance of the shapes used."""

    conforms: bool
    results: List[ShaclResult]
    shapes_provenance: List[ShapesProvenance]
    ontology_provenance: List[ShapesProvenance]
    runs: List[ValidationRun]
    validated_at: str
    mode: str
    options: Dict[str, Any]
    software: Dict[str, str]
    data_triples: int
    notes: List[str] = field(default_factory=list)

    # ---- counters -------------------------------------------------------- #
    @property
    def result_count(self) -> int:
        return len(self.results)

    def _count(self, severity: str) -> int:
        return sum(1 for r in self.results if r.severity == severity)

    @property
    def violations(self) -> int:
        return self._count(SEVERITY_VIOLATION)

    @property
    def warnings(self) -> int:
        return self._count(SEVERITY_WARNING)

    @property
    def infos(self) -> int:
        return self._count(SEVERITY_INFO)

    # ---- queries --------------------------------------------------------- #
    def filter(
        self,
        severity: Optional[str] = None,
        focus_node: Optional[str] = None,
        path: Optional[str] = None,
        shapes_id: Optional[str] = None,
    ) -> List[ShaclResult]:
        """Results matching all the given (exact) criteria."""
        wanted = {
            "severity": severity,
            "focus_node": focus_node,
            "path": path,
            "shapes_id": shapes_id,
        }
        return [
            r
            for r in self.results
            if all(v is None or getattr(r, k) == v for k, v in wanted.items())
        ]

    def group_by(self, key: str) -> Dict[str, List[ShaclResult]]:
        """Group results by ``focus_node``, ``path``, ``severity``, ``shapes_id``..."""
        if key not in _GROUP_KEYS:
            raise ValueError("Cannot group by %r (use one of %s)" % (key, _GROUP_KEYS))
        groups: Dict[str, List[ShaclResult]] = {}
        for result in self.results:
            groups.setdefault(str(getattr(result, key)), []).append(result)
        return groups

    def messages(self, severity: Optional[str] = None) -> List[str]:
        """Human readable line per result (optionally of one severity)."""
        return [r.describe() for r in self.filter(severity=severity)]

    # ---- serialisation --------------------------------------------------- #
    def to_dict(self) -> Dict[str, Any]:
        return {
            "conforms": self.conforms,
            "summary": {
                "results": self.result_count,
                "violations": self.violations,
                "warnings": self.warnings,
                "infos": self.infos,
            },
            "validated_at": self.validated_at,
            "mode": self.mode,
            "software": dict(self.software),
            "options": dict(self.options),
            "data": {"triples": self.data_triples},
            "shapes_provenance": [p.to_dict() for p in self.shapes_provenance],
            "ontology_provenance": [p.to_dict() for p in self.ontology_provenance],
            "runs": [r.to_dict() for r in self.runs],
            "results": [r.to_dict() for r in self.results],
            "notes": list(self.notes),
        }

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    # ---- indicators ------------------------------------------------------ #
    def as_indicator_result(
        self,
        points_if_conforms: float = 100,
        points_otherwise: float = 0,
        max_results: Optional[int] = None,
    ) -> Tuple[float, List[Dict[str, Any]]]:
        """Convert the report to the ``(points, msg_list)`` indicator convention.

        The first message carries the points and the verdict; then one message
        per shapes document used (name, version, SHA-256) and one per result
        (0 points each).  ``max_results`` truncates the per-result messages.
        """
        points = points_if_conforms if self.conforms else points_otherwise
        verdict = "conforms" if self.conforms else "does NOT conform"
        summary = (
            "SHACL validation: the data graph %s to the shapes "
            "(%d violation(s), %d warning(s), %d info(s))"
            % (verdict, self.violations, self.warnings, self.infos)
        )
        msgs: List[Dict[str, Any]] = [{"message": summary, "points": points}]
        for prov in self.shapes_provenance + self.ontology_provenance:
            msgs.append({"message": prov.describe(), "points": 0})
        shown = self.results if max_results is None else self.results[:max_results]
        for result in shown:
            msgs.append({"message": result.describe(), "points": 0})
        omitted = len(self.results) - len(shown)
        if omitted > 0:
            msgs.append(
                {"message": "... %d more result(s) omitted" % omitted, "points": 0}
            )
        return points, msgs
