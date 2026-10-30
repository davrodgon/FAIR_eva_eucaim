"""Core SHACL validation: reports, provenance, hashing and error handling.

Everything here is neutral and hermetic: tiny shapes about a ``Person`` that
must have a name, local files under ``tmp_path`` and mocked HTTP.
"""

import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from rdflib import Graph

from fair_eva.shacl import (
    UNSPECIFIED,
    DataGraphError,
    ShaclError,
    ShaclRunError,
    ShapesConfigError,
    ShapesLoadError,
    ShapesSpec,
    ValidationOptions,
    clear_graph_cache,
    load_shapes,
)
from fair_eva.shacl import loader as loader_module
from fair_eva.shacl import validate
from fair_eva.shacl import validator as validator_module

PREFIXES = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <http://example.org/> .
"""

NAME_SHAPES = (
    PREFIXES
    + """
ex:PersonShape a sh:NodeShape ;
    sh:targetClass ex:Person ;
    sh:property [ sh:path ex:name ; sh:minCount 1 ; sh:datatype xsd:string ] .
"""
)

EMAIL_SHAPES = (
    PREFIXES
    + """
ex:ContactShape a sh:NodeShape ;
    sh:targetClass ex:Person ;
    sh:property [ sh:path ex:email ; sh:minCount 1 ] .
"""
)

WARNING_SHAPES = (
    PREFIXES
    + """
ex:NickShape a sh:NodeShape ;
    sh:targetClass ex:Person ;
    sh:property [ sh:path ex:nick ; sh:minCount 1 ; sh:severity sh:Warning ] .
"""
)

DECLARED_HEADER = """
<http://example.org/shapes> a owl:Ontology ;
    dcterms:title "Person shapes"@en ;
    owl:versionInfo "2.1.0" .
"""

GOOD_DATA = (
    PREFIXES + 'ex:alice a ex:Person ; ex:name "Alice" ; ex:email "a@example.org" .'
)
BAD_DATA = PREFIXES + "ex:bob a ex:Person ."


@pytest.fixture(autouse=True)
def fresh_graph_cache():
    clear_graph_cache()
    yield
    clear_graph_cache()


@pytest.fixture
def shapes_file(tmp_path):
    path = tmp_path / "person.ttl"
    path.write_text(NAME_SHAPES)
    return path


def graph(text):
    return Graph().parse(data=text, format="turtle")


# --------------------------------------------------------------------------- #
# Conformance and results
# --------------------------------------------------------------------------- #
def test_conforming_data(shapes_file):
    report = validate(graph(GOOD_DATA), str(shapes_file))
    assert report.conforms is True
    assert report.result_count == 0
    assert report.violations == report.warnings == report.infos == 0
    assert report.mode == "separate"
    assert [run.conforms for run in report.runs] == [True]
    assert report.data_triples == 3


def test_non_conforming_data_reports_violation_details(shapes_file):
    report = validate(graph(BAD_DATA), str(shapes_file))
    assert report.conforms is False
    assert report.violations == 1
    result = report.results[0]
    assert result.severity == "Violation"
    assert result.focus_node == "http://example.org/bob"
    assert result.path == "http://example.org/name"
    assert result.constraint_component.endswith("MinCountConstraintComponent")
    assert result.shapes_id == "person"
    assert result.message and result.messages == [result.message]
    assert result.source_shape is not None


def test_custom_message_is_kept(tmp_path):
    shapes = NAME_SHAPES.replace(
        "sh:minCount 1 ;", 'sh:minCount 1 ; sh:message "A person needs a name" ;'
    )
    report = validate(graph(BAD_DATA), {"content": shapes, "id": "msg"})
    assert report.results[0].message == "A person needs a name"


def test_value_of_violation_is_reported():
    shapes = NAME_SHAPES
    data = PREFIXES + "ex:carol a ex:Person ; ex:name 42 ."
    report = validate(graph(data), {"content": shapes})
    assert report.results[0].value == "42"
    assert report.results[0].constraint_component.endswith(
        "DatatypeConstraintComponent"
    )


def test_warning_severity_and_allow_warnings():
    spec = {"content": WARNING_SHAPES, "id": "w"}
    report = validate(graph(GOOD_DATA), spec)
    assert report.conforms is False
    assert (report.violations, report.warnings, report.infos) == (0, 1, 0)
    assert report.results[0].severity == "Warning"

    relaxed = validate(graph(GOOD_DATA), spec, options={"allow_warnings": True})
    assert relaxed.conforms is True
    assert relaxed.warnings == 1  # still reported, just not blocking
    assert relaxed.options["allow_warnings"] is True


def test_filter_group_by_and_messages(shapes_file):
    data = PREFIXES + "ex:bob a ex:Person . ex:eve a ex:Person ; ex:name 7 ."
    report = validate(graph(data), str(shapes_file))
    assert report.violations == 2
    assert len(report.filter(severity="Violation")) == 2
    assert report.filter(severity="Warning") == []
    assert len(report.filter(focus_node="http://example.org/bob")) == 1
    assert len(report.filter(path="http://example.org/name", shapes_id="person")) == 2
    by_focus = report.group_by("focus_node")
    assert sorted(by_focus) == ["http://example.org/bob", "http://example.org/eve"]
    assert list(report.group_by("severity")) == ["Violation"]
    assert len(report.messages()) == 2
    assert report.messages(severity="Warning") == []
    with pytest.raises(ValueError):
        report.group_by("nonsense")


def test_results_are_sorted_deterministically(shapes_file):
    data = PREFIXES + "ex:b a ex:Person . ex:a a ex:Person . ex:c a ex:Person ."
    first = validate(graph(data), str(shapes_file))
    second = validate(graph(data), str(shapes_file))
    assert [r.focus_node for r in first.results] == [
        "http://example.org/a",
        "http://example.org/b",
        "http://example.org/c",
    ]
    assert [r.focus_node for r in first.results] == [
        r.focus_node for r in second.results
    ]


@pytest.mark.parametrize(
    "path_ttl, expected",
    [
        ("[ sh:inversePath ex:knows ]", "^<http://example.org/knows>"),
        ("( ex:a ex:b )", "(<http://example.org/a>/<http://example.org/b>)"),
        (
            "[ sh:alternativePath ( ex:a ex:b ) ]",
            "(<http://example.org/a>|<http://example.org/b>)",
        ),
        ("[ sh:oneOrMorePath ex:a ]", "<http://example.org/a>+"),
    ],
)
def test_complex_property_paths_are_rendered(path_ttl, expected):
    shapes = (
        PREFIXES
        + "ex:S a sh:NodeShape ; sh:targetClass ex:Person ; "
        + "sh:property [ sh:path %s ; sh:minCount 1 ] ." % path_ttl
    )
    report = validate(graph(BAD_DATA), {"content": shapes})
    assert report.results[0].path == expected


def test_data_graph_is_not_modified_even_with_inference(shapes_file):
    data = graph(BAD_DATA)
    shapes = load_shapes(str(shapes_file))
    size_data, size_shapes = len(data), len(shapes.graph)
    validate(data, shapes, options=ValidationOptions(inference="rdfs"))
    assert len(data) == size_data
    assert len(shapes.graph) == size_shapes


def test_data_as_text_and_format():
    report = validate(BAD_DATA, {"content": NAME_SHAPES})
    assert report.violations == 1
    jsonld = json.dumps(
        {
            "@context": {"ex": "http://example.org/"},
            "@id": "ex:bob",
            "@type": "ex:Person",
        }
    )
    report = validate(jsonld, {"content": NAME_SHAPES}, data_format="json-ld")
    assert report.violations == 1
    report = validate(BAD_DATA.encode("utf-8"), {"content": NAME_SHAPES})
    assert report.violations == 1


# --------------------------------------------------------------------------- #
# Several shape sets
# --------------------------------------------------------------------------- #
def test_several_sets_are_validated_separately_and_attributed():
    specs = [
        {"content": NAME_SHAPES, "id": "names", "name": "Names", "version": "1"},
        {"content": EMAIL_SHAPES, "id": "emails", "name": "Emails", "version": "2"},
    ]
    data = PREFIXES + 'ex:dave a ex:Person ; ex:name "Dave" .'
    report = validate(graph(data), specs)
    assert report.conforms is False
    assert [p.id for p in report.shapes_provenance] == ["names", "emails"]
    assert [(r.shapes, r.conforms) for r in report.runs] == [
        (["names"], True),
        (["emails"], False),
    ]
    assert [r.shapes_id for r in report.results] == ["emails"]
    assert len(report.filter(shapes_id="names")) == 0
    assert report.shapes_provenance[0].sha256 != report.shapes_provenance[1].sha256


def test_all_sets_conforming():
    specs = [{"content": NAME_SHAPES, "id": "a"}, {"content": EMAIL_SHAPES, "id": "b"}]
    report = validate(graph(GOOD_DATA), specs)
    assert report.conforms is True
    assert len(report.runs) == 2


def test_merged_mode_resolves_cross_references_and_does_not_attribute():
    base = (
        PREFIXES
        + "ex:NameOnly a sh:NodeShape ; sh:property [ sh:path ex:name ; sh:minCount 1 ] ."
    )
    extension = (
        PREFIXES
        + "ex:PersonShape a sh:NodeShape ; sh:targetClass ex:Person ; sh:node ex:NameOnly ."
    )
    specs = [
        {"content": base, "id": "base"},
        {"content": extension, "id": "extension"},
    ]
    merged = validate(graph(BAD_DATA), specs, combine="merged")
    assert merged.mode == "merged"
    assert merged.conforms is False
    assert len(merged.runs) == 1 and merged.runs[0].shapes == ["base", "extension"]
    assert all(r.shapes_id is None for r in merged.results)
    assert any("merged" in note for note in merged.notes)
    assert [p.id for p in merged.shapes_provenance] == ["base", "extension"]


def test_duplicate_ids_are_rejected():
    specs = [
        {"content": NAME_SHAPES, "id": "same"},
        {"content": EMAIL_SHAPES, "id": "same"},
    ]
    with pytest.raises(ShapesConfigError, match="Duplicate"):
        validate(graph(GOOD_DATA), specs)


def test_unsupported_shapes_argument():
    with pytest.raises(ShapesConfigError, match="Cannot interpret int"):
        validate(graph(GOOD_DATA), 42)


def test_malformed_property_path_falls_back_to_the_blank_node():
    from rdflib import RDF, BNode, URIRef
    from rdflib.namespace import SH

    cyclic, alt = BNode(), BNode()
    results = Graph()
    results.add((alt, SH.alternativePath, cyclic))
    results.add((cyclic, RDF.first, URIRef("http://example.org/a")))
    results.add((cyclic, RDF.rest, cyclic))  # recursive list
    assert validator_module._path_str(results, alt) == "_:%s" % alt


def test_loaded_shapes_can_be_reused():
    loaded = load_shapes({"content": NAME_SHAPES, "id": "reused"})
    first = validate(graph(GOOD_DATA), loaded)
    second = validate(graph(BAD_DATA), [loaded])
    assert first.conforms and not second.conforms
    assert first.shapes_provenance[0] is second.shapes_provenance[0]


# --------------------------------------------------------------------------- #
# Ontologies and inference
# --------------------------------------------------------------------------- #
def test_ontology_with_inference_extends_the_targets():
    ontology = PREFIXES + "ex:Student rdfs:subClassOf ex:Person ."
    data = PREFIXES + "ex:sam a ex:Student ."
    spec = {"content": NAME_SHAPES, "id": "names"}

    plain = validate(graph(data), spec)
    assert plain.conforms is True  # a Student is not targeted without inference

    inferred = validate(
        graph(data),
        spec,
        ontologies={"content": ontology, "id": "classes", "version": "0.1"},
        options={"inference": "rdfs"},
    )
    assert inferred.conforms is False
    assert inferred.options["inference"] == "rdfs"
    [onto] = inferred.ontology_provenance
    assert (onto.role, onto.id, onto.version) == ("ontology", "classes", "0.1")
    assert onto.sha256 == hashlib.sha256(ontology.encode()).hexdigest()
    assert inferred.to_dict()["ontology_provenance"][0]["id"] == "classes"
    assert "ontology" in inferred.as_indicator_result()[1][2]["message"]


# --------------------------------------------------------------------------- #
# Provenance: hash, name and version
# --------------------------------------------------------------------------- #
def test_hash_is_sha256_of_the_exact_bytes(shapes_file):
    expected = hashlib.sha256(shapes_file.read_bytes()).hexdigest()
    prov = validate(graph(GOOD_DATA), str(shapes_file)).shapes_provenance[0]
    assert prov.sha256 == expected
    assert (prov.hash_algorithm, prov.hash_input) == ("sha256", "raw-bytes")
    assert prov.size_bytes == len(shapes_file.read_bytes())
    assert prov.source == str(shapes_file)
    assert prov.source_type == "file"
    assert prov.format == "turtle"
    assert prov.triples > 0


def test_hash_is_stable_for_the_same_content_and_independent_of_the_origin(tmp_path):
    first = tmp_path / "one.ttl"
    second = tmp_path / "copy" / "two.ttl"
    second.parent.mkdir()
    first.write_text(NAME_SHAPES)
    second.write_text(NAME_SHAPES)
    hashes = {
        load_shapes(str(first)).provenance.sha256,
        load_shapes(str(first)).provenance.sha256,
        load_shapes(str(second)).provenance.sha256,
        load_shapes({"content": NAME_SHAPES}).provenance.sha256,
        load_shapes({"content": NAME_SHAPES.encode("utf-8")}).provenance.sha256,
    }
    assert len(hashes) == 1


def test_hash_changes_when_the_content_changes(tmp_path):
    path = tmp_path / "s.ttl"
    path.write_text(NAME_SHAPES)
    before = load_shapes(str(path)).provenance.sha256
    path.write_text(NAME_SHAPES.replace("sh:minCount 1", "sh:minCount 2"))
    assert load_shapes(str(path)).provenance.sha256 != before


def test_hash_is_over_raw_bytes_so_reformatting_changes_it():
    """Documented behaviour: the hash identifies the artefact, not the graph."""
    reformatted = NAME_SHAPES + "\n\n"
    a = load_shapes({"content": NAME_SHAPES}).provenance
    b = load_shapes({"content": reformatted}).provenance
    assert a.triples == b.triples
    assert a.sha256 != b.sha256


def test_expected_sha256_pin(shapes_file):
    digest = hashlib.sha256(shapes_file.read_bytes()).hexdigest()
    ok = load_shapes(
        {"source": str(shapes_file), "expected_sha256": "sha256:" + digest.upper()}
    )
    assert ok.provenance.sha256 == digest
    with pytest.raises(ShapesLoadError, match="SHA-256 mismatch"):
        load_shapes({"source": str(shapes_file), "expected_sha256": "0" * 64})
    with pytest.raises(ShapesConfigError, match="64 hexadecimal"):
        ShapesSpec(source=str(shapes_file), expected_sha256="abc")


def test_version_from_config_only(shapes_file):
    prov = load_shapes({"source": str(shapes_file), "version": "1.2.3"}).provenance
    assert (prov.version, prov.version_source) == ("1.2.3", "config")
    assert prov.discrepancies == []


def test_version_unspecified_when_nowhere_declared(shapes_file):
    prov = load_shapes(str(shapes_file)).provenance
    assert prov.version == UNSPECIFIED == "unspecified"
    assert prov.version_source == "unspecified"
    assert prov.declared_in_file["version"] is None
    assert not prov.has_discrepancies


def test_version_and_title_from_the_file():
    prov = load_shapes({"content": NAME_SHAPES + DECLARED_HEADER}).provenance
    assert (prov.version, prov.version_source) == ("2.1.0", "file")
    assert (prov.name, prov.name_source) == ("Person shapes", "file")
    assert prov.declared_in_file["version_property"] == "owl:versionInfo"


def test_version_from_dcterms_has_version():
    header = '<http://example.org/s> a owl:Ontology ; dcterms:hasVersion "3.0" .'
    prov = load_shapes({"content": NAME_SHAPES + header}).provenance
    assert (prov.version, prov.version_source) == ("3.0", "file")
    assert prov.declared_in_file["version_property"] == "dcterms:hasVersion"


def test_config_and_file_versions_that_differ_are_recorded():
    spec = {
        "content": NAME_SHAPES + DECLARED_HEADER,
        "version": "1.0.0",
        "name": "Mine",
    }
    prov = validate(graph(GOOD_DATA), spec).shapes_provenance[0]
    assert (prov.version, prov.version_source) == ("1.0.0", "config")
    assert prov.has_discrepancies
    assert {
        "field": "version",
        "config": "1.0.0",
        "file": "2.1.0",
    } in prov.discrepancies
    assert {
        "field": "name",
        "config": "Mine",
        "file": "Person shapes",
    } in prov.discrepancies
    assert prov.declared_in_file["version"] == "2.1.0"
    assert "discrepancy" in prov.describe()


def test_matching_config_and_file_metadata_is_not_a_discrepancy():
    spec = {
        "content": NAME_SHAPES + DECLARED_HEADER,
        "version": "2.1.0",
        "name": "person SHAPES",
    }
    assert load_shapes(spec).provenance.discrepancies == []


def test_name_falls_back_to_the_id():
    prov = load_shapes({"content": NAME_SHAPES, "id": "my-id"}).provenance
    assert (prov.name, prov.name_source) == ("my-id", "id")
    assert load_shapes({"content": NAME_SHAPES}).provenance.id == "shapes-1"


def test_ambiguous_declarations_are_ignored_and_noted():
    two_versions = (
        '<http://example.org/s> a owl:Ontology ; owl:versionInfo "1", "2" ; '
        'dcterms:title "T" .'
    )
    prov = load_shapes({"content": NAME_SHAPES + two_versions}).provenance
    assert prov.version == UNSPECIFIED
    assert any("ambiguous" in n for n in prov.notes)

    two_ontologies = (
        '<http://example.org/a> a owl:Ontology ; owl:versionInfo "1" . '
        '<http://example.org/b> a owl:Ontology ; owl:versionInfo "2" .'
    )
    prov = load_shapes({"content": NAME_SHAPES + two_ontologies}).provenance
    assert prov.version == UNSPECIFIED
    assert any("2 owl:Ontology" in n for n in prov.notes)


def test_conflicting_version_properties_prefer_version_info():
    header = (
        '<http://example.org/s> a owl:Ontology ; owl:versionInfo "1" ; '
        'dcterms:hasVersion "2" .'
    )
    prov = load_shapes({"content": NAME_SHAPES + header}).provenance
    assert prov.version == "1"
    assert any("differs" in n for n in prov.notes)


def test_owl_imports_are_listed_but_not_followed():
    header = (
        "<http://example.org/s> a owl:Ontology ; "
        "owl:imports <http://example.org/other-shapes> ."
    )
    prov = load_shapes({"content": NAME_SHAPES + header}).provenance
    assert prov.declared_in_file["imports"] == ["http://example.org/other-shapes"]


def test_title_in_other_languages_only_uses_the_first_one():
    header = (
        '<http://example.org/s> a owl:Ontology ; dcterms:title "Titre"@fr, "Titel"@de .'
    )
    assert load_shapes({"content": NAME_SHAPES + header}).provenance.name == "Titel"


def test_only_the_ontology_with_metadata_is_used_when_there_are_several():
    header = (
        "<http://example.org/plain> a owl:Ontology . "
        '<http://example.org/described> a owl:Ontology ; owl:versionInfo "4.5" .'
    )
    prov = load_shapes({"content": NAME_SHAPES + header}).provenance
    assert (prov.version, prov.version_source) == ("4.5", "file")


def test_title_prefers_untagged_then_english():
    header = (
        '<http://example.org/s> a owl:Ontology ; dcterms:title "Titel"@de, "Title"@en .'
    )
    assert load_shapes({"content": NAME_SHAPES + header}).provenance.name == "Title"


def test_document_without_shacl_terms_is_flagged():
    prov = load_shapes({"content": PREFIXES + "ex:a ex:b ex:c ."}).provenance
    assert any("vacuous" in n for n in prov.notes)


def test_relative_paths_use_base_dir(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "s.ttl").write_text(NAME_SHAPES)
    spec = ShapesSpec(source="sub/s.ttl", base_dir=str(tmp_path))
    assert load_shapes(spec).provenance.source == str(tmp_path / "sub" / "s.ttl")


def test_formats_are_detected_from_extension_and_alias(tmp_path):
    nt = tmp_path / "s.nt"
    nt.write_text(
        "<http://example.org/S> <http://www.w3.org/ns/shacl#targetClass> "
        "<http://example.org/Person> .\n"
    )
    assert load_shapes(str(nt)).provenance.format == "nt"
    assert (
        load_shapes({"source": str(nt), "format": "ntriples"}).provenance.format == "nt"
    )
    odd = tmp_path / "shapes.data"
    odd.write_text(NAME_SHAPES)
    with pytest.raises(ShapesLoadError, match="format"):
        load_shapes(str(odd))
    assert (
        load_shapes({"source": str(odd), "format": "TTL"}).provenance.format == "turtle"
    )


def test_jsonld_shapes(tmp_path):
    doc = {
        "@context": {"sh": "http://www.w3.org/ns/shacl#", "ex": "http://example.org/"},
        "@id": "ex:PersonShape",
        "@type": "sh:NodeShape",
        "sh:targetClass": {"@id": "ex:Person"},
        "sh:property": {"sh:path": {"@id": "ex:name"}, "sh:minCount": 1},
    }
    path = tmp_path / "shapes.jsonld"
    path.write_text(json.dumps(doc))
    report = validate(graph(BAD_DATA), str(path))
    assert report.shapes_provenance[0].format == "json-ld"
    assert report.violations == 1


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
def test_missing_shapes_file(tmp_path):
    missing = tmp_path / "nope.ttl"
    with pytest.raises(ShapesLoadError, match="not found") as excinfo:
        validate(graph(GOOD_DATA), str(missing))
    assert str(missing) in str(excinfo.value)
    assert isinstance(excinfo.value, ShaclError)


def test_directory_as_shapes_source(tmp_path):
    with pytest.raises(ShapesLoadError, match="Cannot read"):
        load_shapes({"source": str(tmp_path), "format": "turtle"})


def test_malformed_shapes(tmp_path):
    path = tmp_path / "broken.ttl"
    path.write_text("@prefix ex: <http://example.org/> .\nex:a ex:b [ .\n")
    with pytest.raises(ShapesLoadError, match="Could not parse"):
        load_shapes(str(path))
    wrong_format = {"content": NAME_SHAPES, "format": "json-ld"}
    with pytest.raises(ShapesLoadError, match="Could not parse"):
        load_shapes(wrong_format)


def test_empty_shapes_document(tmp_path):
    path = tmp_path / "empty.ttl"
    path.write_text("")
    with pytest.raises(ShapesLoadError, match="no RDF triples"):
        load_shapes(str(path))


def test_invalid_specifications():
    with pytest.raises(ShapesConfigError, match="exactly one"):
        ShapesSpec()
    with pytest.raises(ShapesConfigError, match="exactly one"):
        ShapesSpec(source="a.ttl", content="x")
    with pytest.raises(ShapesConfigError, match="empty"):
        ShapesSpec(source="  ")
    with pytest.raises(ShapesConfigError, match="str or bytes"):
        ShapesSpec(content=3)
    with pytest.raises(ShapesConfigError, match="timeout"):
        ShapesSpec(source="a.ttl", timeout=0)
    with pytest.raises(ShapesConfigError, match="timeout"):
        ShapesSpec(source="a.ttl", timeout="soon")
    with pytest.raises(ShapesConfigError, match="Unknown key"):
        ShapesSpec.coerce({"source": "a.ttl", "sourse": "typo"})
    with pytest.raises(ShapesConfigError, match="Cannot interpret"):
        ShapesSpec.coerce(42)
    with pytest.raises(ShapesConfigError, match="Unsupported RDF format"):
        load_shapes({"content": NAME_SHAPES, "format": "docx"})
    with pytest.raises(ShapesConfigError, match="scheme"):
        load_shapes("ftp://example.org/shapes.ttl")


def test_invalid_validate_arguments(shapes_file):
    with pytest.raises(ShapesConfigError, match="No SHACL shapes"):
        validate(graph(GOOD_DATA), [])
    with pytest.raises(ShapesConfigError, match="combine"):
        validate(graph(GOOD_DATA), str(shapes_file), combine="union")
    with pytest.raises(ShapesConfigError, match="inference"):
        validate(graph(GOOD_DATA), str(shapes_file), options={"inference": "magic"})
    with pytest.raises(ShapesConfigError, match="Unknown validation option"):
        validate(graph(GOOD_DATA), str(shapes_file), options={"fast": True})
    with pytest.raises(ShapesConfigError, match="Cannot interpret"):
        validate(graph(GOOD_DATA), str(shapes_file), options=3)


def test_bad_data_graphs(shapes_file):
    with pytest.raises(DataGraphError, match="empty"):
        validate(Graph(), str(shapes_file))
    with pytest.raises(DataGraphError, match="Could not parse"):
        validate("this is not turtle", str(shapes_file))
    with pytest.raises(DataGraphError, match="rdflib.Graph"):
        validate(12, str(shapes_file))
    report = validate(Graph(), str(shapes_file), options={"allow_empty_data": True})
    assert report.conforms is True and report.data_triples == 0


def test_engine_failure_is_wrapped(monkeypatch, shapes_file):
    import pyshacl

    def boom(*args, **kwargs):
        raise ValueError("engine exploded")

    monkeypatch.setattr(pyshacl, "validate", boom)
    with pytest.raises(ShaclRunError, match="engine exploded") as excinfo:
        validate(graph(GOOD_DATA), str(shapes_file))
    assert "person" in str(excinfo.value)


def test_invalid_shapes_graph_is_reported_by_the_engine():
    shapes = PREFIXES + (
        "ex:S a sh:NodeShape ; sh:targetClass ex:Person ; "
        'sh:property [ sh:path ex:name ; sh:minCount "many" ] .'
    )
    with pytest.raises(ShaclError):
        validate(graph(GOOD_DATA), {"content": shapes}, options={"meta_shacl": True})


def test_missing_pyshacl(monkeypatch, shapes_file):
    monkeypatch.setitem(__import__("sys").modules, "pyshacl", None)
    with pytest.raises(ShaclRunError, match="pyshacl"):
        validate(graph(GOOD_DATA), str(shapes_file))
    assert validator_module.software_versions()["pyshacl"] == "not installed"


# --------------------------------------------------------------------------- #
# Loading from a URL (mocked: tests cannot use the network)
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, content, url="https://shapes.example.org/person.ttl", **headers):
        self.content = content
        self.url = url
        self.headers = headers
        self.status_code = 200

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("%d Error" % self.status_code)


@pytest.fixture
def fake_get(monkeypatch):
    calls = []
    state = {"response": FakeResponse(NAME_SHAPES.encode())}

    def get(url, **kwargs):
        calls.append((url, kwargs))
        response = state["response"]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("fair_eva.shacl.loader.requests.get", get)
    return calls, state


def test_shapes_from_url_hash_is_of_the_downloaded_content(fake_get):
    calls, state = fake_get
    url = "https://shapes.example.org/person.ttl"
    report = validate(graph(BAD_DATA), {"source": url, "version": "9", "timeout": 5})
    prov = report.shapes_provenance[0]
    assert prov.source_type == "url" and prov.source == url
    assert prov.final_url == url
    assert prov.sha256 == hashlib.sha256(NAME_SHAPES.encode()).hexdigest()
    assert prov.id == "person" and prov.format == "turtle"
    assert report.violations == 1
    assert calls[0][1]["timeout"] == 5
    assert "text/turtle" in calls[0][1]["headers"]["Accept"]

    # The remote content changed between executions: so does the hash.
    state["response"] = FakeResponse(
        NAME_SHAPES.replace("minCount 1", "minCount 2").encode()
    )
    changed = validate(graph(BAD_DATA), url).shapes_provenance[0]
    assert changed.sha256 != prov.sha256


def test_url_format_comes_from_content_type_and_redirect_is_recorded(fake_get):
    _, state = fake_get
    state["response"] = FakeResponse(
        NAME_SHAPES.encode(),
        url="https://cdn.example.org/v2/shapes",
        **{"Content-Type": "text/turtle; charset=utf-8"},
    )
    prov = load_shapes("https://shapes.example.org/latest").provenance
    assert prov.format == "turtle"
    assert prov.source == "https://shapes.example.org/latest"
    assert prov.final_url == "https://cdn.example.org/v2/shapes"
    assert prov.id == "latest"


def test_url_without_detectable_format(fake_get):
    _, state = fake_get
    state["response"] = FakeResponse(
        NAME_SHAPES.encode(), **{"Content-Type": "text/plain"}
    )
    with pytest.raises(ShapesLoadError, match="format"):
        load_shapes("https://shapes.example.org/latest")


def test_url_errors_are_wrapped(fake_get):
    _, state = fake_get
    response = FakeResponse(b"", **{})
    response.status_code = 404
    state["response"] = response
    with pytest.raises(ShapesLoadError, match="Could not download"):
        load_shapes("https://shapes.example.org/missing.ttl")
    state["response"] = requests.ConnectionError("unreachable")
    with pytest.raises(ShapesLoadError, match="unreachable"):
        load_shapes("https://shapes.example.org/person.ttl")


# --------------------------------------------------------------------------- #
# Serialisation and indicator conversion
# --------------------------------------------------------------------------- #
def test_report_serialisation_and_provenance_block(monkeypatch, shapes_file):
    from datetime import datetime, timezone

    monkeypatch.setattr(
        validator_module,
        "_now",
        lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    )
    spec = {"source": str(shapes_file), "name": "Person shapes", "version": "1.0.0"}
    report = validate(graph(BAD_DATA), spec)
    data = json.loads(report.to_json())
    assert data == report.to_dict()
    assert data["validated_at"] == "2026-01-02T03:04:05+00:00"
    assert data["conforms"] is False
    assert data["summary"] == {"results": 1, "violations": 1, "warnings": 0, "infos": 0}
    assert set(data["software"]) == {"pyshacl", "rdflib", "fair_eva", "python"}
    assert re.match(r"^\d+\.\d+", data["software"]["rdflib"])
    prov = data["shapes_provenance"][0]
    assert set(prov) == {
        "role",
        "id",
        "name",
        "name_source",
        "version",
        "version_source",
        "sha256",
        "source",
        "source_type",
        "final_url",
        "format",
        "size_bytes",
        "triples",
        "hash_algorithm",
        "hash_input",
        "declared_in_file",
        "discrepancies",
        "notes",
    }
    assert re.fullmatch(r"[0-9a-f]{64}", prov["sha256"])
    assert (prov["name"], prov["version"]) == ("Person shapes", "1.0.0")
    assert data["runs"] == [
        {
            "shapes": ["person"],
            "conforms": False,
            "violations": 1,
            "warnings": 0,
            "infos": 0,
        }
    ]
    assert data["results"][0]["focus_node"] == "http://example.org/bob"
    # raw rdflib graphs stay available in memory but are not serialised
    assert report.runs[0].results_graph is not None
    assert report.runs[0].shapes_graph is not None


def test_as_indicator_result_conforming(shapes_file):
    report = validate(graph(GOOD_DATA), {"source": str(shapes_file), "version": "1"})
    points, messages = report.as_indicator_result()
    assert points == 100
    assert messages[0]["points"] == 100 and "conforms" in messages[0]["message"]
    assert report.shapes_provenance[0].sha256 in messages[1]["message"]
    assert "version 1" in messages[1]["message"]
    assert len(messages) == 2


def test_as_indicator_result_non_conforming(shapes_file):
    data = PREFIXES + "ex:a a ex:Person . ex:b a ex:Person . ex:c a ex:Person ."
    report = validate(graph(data), str(shapes_file))
    points, messages = report.as_indicator_result()
    assert points == 0
    assert "does NOT conform" in messages[0]["message"]
    assert "3 violation(s)" in messages[0]["message"]
    assert len(messages) == 1 + 1 + 3
    assert all(m["points"] == 0 for m in messages[1:])
    assert "http://example.org/a" in messages[2]["message"]

    points, messages = report.as_indicator_result(
        points_if_conforms=10, points_otherwise=5, max_results=1
    )
    assert points == 5
    assert messages[-1]["message"] == "... 2 more result(s) omitted"
    assert len(messages) == 1 + 1 + 1 + 1


def test_validation_options_defaults():
    options = ValidationOptions()
    assert options.to_dict() == {
        "inference": "none",
        "advanced": False,
        "allow_infos": False,
        "allow_warnings": False,
        "abort_on_first": False,
        "meta_shacl": False,
        "allow_empty_data": False,
    }
    assert ValidationOptions.coerce(options) is options
    assert ValidationOptions.coerce(None) == options


# --------------------------------------------------------------------------- #
# Long-running processes: parsed-graph cache and engine memo
# --------------------------------------------------------------------------- #
def test_parsed_documents_are_cached_by_content_hash(tmp_path):
    path = tmp_path / "s.ttl"
    path.write_text(NAME_SHAPES)
    first = load_shapes(str(path))
    again = load_shapes(str(path))
    assert again.graph is first.graph  # not parsed twice
    assert again.provenance.sha256 == first.provenance.sha256

    # The file is read and hashed on every load: a change is never masked.
    path.write_text(NAME_SHAPES.replace("minCount 1", "minCount 2"))
    changed = load_shapes(str(path))
    assert changed.provenance.sha256 != first.provenance.sha256
    assert changed.graph is not first.graph

    clear_graph_cache()
    assert load_shapes(str(path)).graph is not changed.graph


def test_graph_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(loader_module, "GRAPH_CACHE_SIZE", 2)
    docs = [NAME_SHAPES + "ex:n%d ex:p ex:o ." % i for i in range(3)]
    graphs = [load_shapes({"content": d}).graph for d in docs]
    assert load_shapes({"content": docs[2]}).graph is graphs[2]
    assert load_shapes({"content": docs[0]}).graph is not graphs[0]  # evicted


def test_validation_leaves_no_blank_node_memo_in_pyshacl(shapes_file):
    from pyshacl.rdfutil.stringify import stringify_blank_node

    for _ in range(3):
        validate(graph(BAD_DATA), str(shapes_file))
        assert len(stringify_blank_node.dict_cache) == 0


def test_releasing_engine_caches_tolerates_changed_internals(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyshacl.rdfutil.stringify", None)
    validator_module._release_engine_caches()  # must not raise


def test_concurrent_validations_share_the_cached_shapes(shapes_file):
    def run(_):
        report = validate(graph(BAD_DATA), str(shapes_file))
        return report.violations, report.shapes_provenance[0].sha256

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = set(pool.map(run, range(24)))
    assert len(outcomes) == 1
    assert next(iter(outcomes))[0] == 1
