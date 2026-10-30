"""Generic SHACL validation of an RDF graph against configurable shapes."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from importlib import metadata
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import rdflib
from rdflib import RDF, BNode, Graph, URIRef
from rdflib.collection import Collection
from rdflib.namespace import SH

from fair_eva.shacl.errors import DataGraphError, ShaclRunError, ShapesConfigError
from fair_eva.shacl.loader import load_shapes, normalize_format
from fair_eva.shacl.models import (
    COMBINE_MERGED,
    COMBINE_MODES,
    COMBINE_SEPARATE,
    ROLE_ONTOLOGY,
    ROLE_SHAPES,
    SEVERITY_INFO,
    SEVERITY_VIOLATION,
    SEVERITY_WARNING,
    LoadedShapes,
    ShaclReport,
    ShaclResult,
    ShapesSpec,
    ValidationOptions,
    ValidationRun,
)

logger = logging.getLogger("api.shacl")

_SEVERITY_NAMES = {
    SH.Violation: SEVERITY_VIOLATION,
    SH.Warning: SEVERITY_WARNING,
    SH.Info: SEVERITY_INFO,
}
_SEVERITY_ORDER = {SEVERITY_VIOLATION: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}

ShapesInput = Union[ShapesSpec, LoadedShapes, Mapping, str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _package_version(dist: str, module: Any = None) -> str:
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return str(getattr(module, "__version__", "unknown"))


def software_versions() -> Dict[str, str]:
    """Versions of the libraries that determine the validation outcome."""
    try:
        import pyshacl

        pyshacl_version = _package_version("pyshacl", pyshacl)
    except ImportError:
        pyshacl_version = "not installed"
    return {
        "pyshacl": pyshacl_version,
        "rdflib": _package_version("rdflib", rdflib),
        "fair_eva": _package_version("fair_eva"),
        "python": "%d.%d.%d" % sys.version_info[:3],
    }


# --------------------------------------------------------------------------- #
# Input coercion
# --------------------------------------------------------------------------- #
def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping, ShapesSpec, LoadedShapes)):
        return [value]
    if isinstance(value, Iterable):
        return list(value)
    return [value]


def _load_all(items: Any, role: str) -> List[LoadedShapes]:
    loaded = []
    for index, item in enumerate(_as_list(items)):
        if isinstance(item, LoadedShapes):
            loaded.append(item)
        else:
            loaded.append(load_shapes(item, index=index, role=role))
    ids = [item.provenance.id for item in loaded]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        raise ShapesConfigError(
            "Duplicate %s identifier(s): %s (set a distinct 'id')"
            % (role, ", ".join(duplicated))
        )
    return loaded


def _coerce_data_graph(
    data_graph: Any, data_format: Optional[str], options: ValidationOptions
) -> Graph:
    if isinstance(data_graph, Graph):
        graph = data_graph
    elif isinstance(data_graph, (str, bytes)):
        fmt = normalize_format(data_format or "turtle")
        try:
            graph = Graph().parse(data=data_graph, format=fmt)
        except Exception as exc:
            raise DataGraphError(
                "Could not parse the data graph as %s: %s" % (fmt, exc)
            ) from exc
    else:
        raise DataGraphError(
            "The data graph must be an rdflib.Graph or RDF text, got %s"
            % type(data_graph).__name__
        )
    if len(graph) == 0 and not options.allow_empty_data:
        raise DataGraphError(
            "The data graph is empty (an empty graph would conform vacuously); "
            "set allow_empty_data to validate it anyway"
        )
    return graph


# --------------------------------------------------------------------------- #
# Result extraction
# --------------------------------------------------------------------------- #
def _term_str(term: Any) -> str:
    if isinstance(term, BNode):
        return "_:%s" % term
    return str(term)


def _path_str(graph: Graph, node: Any, nested: bool = False, depth: int = 0) -> str:
    """Render a ``sh:resultPath`` (IRI or SHACL property path) as text."""
    if isinstance(node, URIRef):
        return "<%s>" % node if nested else str(node)
    if not isinstance(node, BNode) or depth > 10:
        return _term_str(node)
    unary = (
        (SH.inversePath, "^%s"),
        (SH.zeroOrMorePath, "%s*"),
        (SH.oneOrMorePath, "%s+"),
        (SH.zeroOrOnePath, "%s?"),
    )
    for predicate, template in unary:
        inner = graph.value(node, predicate)
        if inner is not None:
            return template % _path_str(graph, inner, True, depth + 1)
    alternatives = graph.value(node, SH.alternativePath)
    try:
        if alternatives is not None:
            items = list(Collection(graph, alternatives))
            return "(%s)" % "|".join(
                _path_str(graph, i, True, depth + 1) for i in items
            )
        if (node, RDF.first, None) in graph:
            items = list(Collection(graph, node))
            return "(%s)" % "/".join(
                _path_str(graph, i, True, depth + 1) for i in items
            )
    except Exception:  # malformed RDF list: fall back to the blank node label
        pass
    return _term_str(node)


def _extract_results(
    results_graph: Graph, shapes_id: Optional[str]
) -> List[ShaclResult]:
    results = []
    for node in results_graph.subjects(RDF.type, SH.ValidationResult):
        severity_term = results_graph.value(node, SH.resultSeverity)
        severity = _SEVERITY_NAMES.get(severity_term, _term_str(severity_term))
        messages = sorted(str(m) for m in results_graph.objects(node, SH.resultMessage))
        component = results_graph.value(node, SH.sourceConstraintComponent)
        path = results_graph.value(node, SH.resultPath)
        value = results_graph.value(node, SH.value)
        source_shape = results_graph.value(node, SH.sourceShape)
        focus = results_graph.value(node, SH.focusNode)
        if messages:
            message = messages[0]
        else:
            message = "Constraint %s not satisfied" % (
                str(component).rsplit("#", 1)[-1] if component else "unknown"
            )
        results.append(
            ShaclResult(
                severity=severity,
                focus_node=_term_str(focus) if focus is not None else "",
                message=message,
                path=_path_str(results_graph, path) if path is not None else None,
                value=_term_str(value) if value is not None else None,
                source_shape=(
                    _term_str(source_shape) if source_shape is not None else None
                ),
                constraint_component=str(component) if component else None,
                shapes_id=shapes_id,
                messages=messages,
            )
        )
    return results


def _sort_key(result: ShaclResult) -> Tuple:
    return (
        result.shapes_id or "",
        _SEVERITY_ORDER.get(result.severity, 99),
        result.focus_node,
        result.path or "",
        result.constraint_component or "",
        result.message,
        result.source_shape or "",
        result.value or "",
    )


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
def _copy_graph(graph: Graph) -> Graph:
    copy = Graph()
    for prefix, namespace in graph.namespaces():
        copy.bind(prefix, namespace, override=False)
    copy += graph
    return copy


def _release_engine_caches() -> None:
    """Empty pyshacl's module-level memo of rendered blank nodes.

    pyshacl memoises them in a dict keyed by ``id(graph)`` that it never clears,
    so a long-running process that validates against freshly built graphs grows
    without bound (a few KiB per validation).  The memo is a pure optimisation,
    so emptying it after each run is always safe.
    """
    try:
        from pyshacl.rdfutil.stringify import stringify_blank_node

        stringify_blank_node.dict_cache.clear()
    except Exception:  # internals changed: nothing to release
        logger.debug("Could not release pyshacl caches", exc_info=True)


def _run_engine(
    data: Graph,
    shapes_graph: Graph,
    ontology_graph: Optional[Graph],
    options: ValidationOptions,
    label: str,
) -> Tuple[bool, Graph]:
    try:
        import pyshacl
    except ImportError as exc:  # pragma: no cover - pyshacl is a hard dependency
        raise ShaclRunError(
            "pyshacl is required for SHACL validation but is not installed"
        ) from exc
    try:
        conforms, results_graph, _text = pyshacl.validate(
            data,
            # pyshacl adds triples to the shapes graph it receives: give it a copy
            # so loaded shapes stay pristine (and reusable).
            shacl_graph=_copy_graph(shapes_graph),
            ont_graph=ontology_graph,
            inference=None if options.inference == "none" else options.inference,
            advanced=options.advanced,
            abort_on_first=options.abort_on_first,
            allow_infos=options.allow_infos,
            allow_warnings=options.allow_warnings,
            meta_shacl=options.meta_shacl,
        )
    except Exception as exc:
        raise ShaclRunError(
            "SHACL validation against %s failed: %s" % (label, exc)
        ) from exc
    finally:
        _release_engine_caches()
    return bool(conforms), results_graph


def validate(
    data_graph: Union[Graph, str, bytes],
    shapes_spec: Union[ShapesInput, Sequence[ShapesInput]],
    *,
    ontologies: Optional[Union[ShapesInput, Sequence[ShapesInput]]] = None,
    data_format: Optional[str] = None,
    combine: str = COMBINE_SEPARATE,
    options: Any = None,
) -> ShaclReport:
    """Validate ``data_graph`` against one or several SHACL shape sets.

    Parameters
    ----------
    data_graph : rdflib.Graph or str or bytes
        Graph to validate, or RDF text (parsed as ``data_format``, Turtle by
        default).  It is not modified.
    shapes_spec : ShapesSpec, LoadedShapes, mapping, str or a list of them
        The shape set(s).  A string is a local path or an ``http(s)`` URL; a
        mapping accepts the fields of :class:`~fair_eva.shacl.models.ShapesSpec`.
    ontologies : same as ``shapes_spec``, optional
        Extra RDF documents handed to the engine as ontology graph (e.g. class
        hierarchies for ``sh:class`` with ``inference``).  Their provenance is
        recorded too.
    data_format : str, optional
        Serialisation of ``data_graph`` when it is text.
    combine : ``"separate"`` or ``"merged"``
        ``separate`` (default) validates each set independently and attributes
        every result to its set.  ``merged`` merges all sets into one shapes
        graph (needed when sets reference each other's shapes); results are
        then not attributed to a set (``shapes_id`` is ``None``).
    options : ValidationOptions or mapping, optional
        Engine options (inference, advanced features, allowed severities...).

    Returns
    -------
    ShaclReport
        Conformance, flattened results and the provenance (name, version,
        SHA-256...) of every shapes / ontology document used.

    Raises
    ------
    ShaclError
        (or a subclass) if the specification is invalid, a document cannot be
        loaded, the data cannot be parsed or the engine fails.
    """
    opts = ValidationOptions.coerce(options)
    if combine not in COMBINE_MODES:
        raise ShapesConfigError(
            "combine must be one of %s, got %r" % (", ".join(COMBINE_MODES), combine)
        )
    shape_sets = _load_all(shapes_spec, ROLE_SHAPES)
    if not shape_sets:
        raise ShapesConfigError("No SHACL shapes were specified")
    ontology_docs = _load_all(ontologies, ROLE_ONTOLOGY)
    data = _coerce_data_graph(data_graph, data_format, opts)

    ontology_graph = None
    if ontology_docs:
        ontology_graph = Graph()
        for doc in ontology_docs:
            ontology_graph += doc.graph

    notes: List[str] = []
    runs: List[ValidationRun] = []
    results: List[ShaclResult] = []

    if combine == COMBINE_MERGED:
        groups = [(None, shape_sets)]
        notes.append(
            "Shape sets were merged into a single shapes graph; results are not "
            "attributed to an individual set"
        )
    else:
        groups = [(item.provenance.id, [item]) for item in shape_sets]

    for shapes_id, members in groups:
        if len(members) == 1:
            shapes_graph = members[0].graph
        else:
            shapes_graph = Graph()
            for member in members:
                shapes_graph += member.graph
        ids = [m.provenance.id for m in members]
        conforms, results_graph = _run_engine(
            data, shapes_graph, ontology_graph, opts, ", ".join(ids)
        )
        run_results = _extract_results(results_graph, shapes_id)
        runs.append(
            ValidationRun(
                shapes=ids,
                conforms=conforms,
                violations=sum(r.severity == SEVERITY_VIOLATION for r in run_results),
                warnings=sum(r.severity == SEVERITY_WARNING for r in run_results),
                infos=sum(r.severity == SEVERITY_INFO for r in run_results),
                results_graph=results_graph,
                shapes_graph=shapes_graph,
            )
        )
        results.extend(run_results)

    results.sort(key=_sort_key)
    return ShaclReport(
        conforms=all(run.conforms for run in runs),
        results=results,
        shapes_provenance=[item.provenance for item in shape_sets],
        ontology_provenance=[item.provenance for item in ontology_docs],
        runs=runs,
        validated_at=_now().isoformat(timespec="seconds"),
        mode=combine,
        options=opts.to_dict(),
        software=software_versions(),
        data_triples=len(data),
        notes=notes,
    )
