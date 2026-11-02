"""Shape sets made of several documents (``sources`` or a glob ``source``).

The documents of a set are merged into one shapes graph and validated in a
single run; the set is hashed over the sorted SHA-256 of its documents.
"""

import configparser
import hashlib
import shutil
import subprocess

import pytest

from fair_eva.shacl import (
    UNSPECIFIED,
    ShapesConfigError,
    ShapesLoadError,
    ShapesSpec,
    clear_graph_cache,
    load_shapes,
    shapes_from_config,
    validate,
)
from fair_eva.shacl.loader import documents_sha256
from fair_eva.tests.test_shacl import (
    BAD_DATA,
    EMAIL_SHAPES,
    GOOD_DATA,
    NAME_SHAPES,
    PREFIXES,
)

NAME_MODULE = NAME_SHAPES
EMAIL_MODULE = EMAIL_SHAPES
# A module that only works together with another one of the set
ADDRESS_USER_MODULE = (
    PREFIXES
    + """
ex:ResidentShape a sh:NodeShape ;
    sh:targetClass ex:Resident ;
    sh:property [ sh:path ex:address ; sh:minCount 1 ; sh:node ex:AddressShape ] .
"""
)
ADDRESS_MODULE = (
    PREFIXES
    + """
ex:AddressShape a sh:NodeShape ;
    sh:property [ sh:path ex:city ; sh:minCount 1 ] .
"""
)


def versioned(module, version):
    iri = "http://example.org/module/%s" % hashlib.sha1(module.encode()).hexdigest()
    return module + "\n<%s> a owl:Ontology ; owl:versionInfo %r ." % (iri, version)


@pytest.fixture(autouse=True)
def fresh_graph_cache():
    clear_graph_cache()
    yield
    clear_graph_cache()


@pytest.fixture
def modules(tmp_path):
    directory = tmp_path / "profile" / "v1.0"
    directory.mkdir(parents=True)
    (directory / "a-name.ttl").write_text(NAME_MODULE)
    (directory / "b-email.ttl").write_text(EMAIL_MODULE)
    return directory


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# Loading and validating a set
# --------------------------------------------------------------------------- #
def test_glob_source_loads_every_matching_file_as_one_set(modules):
    loaded = load_shapes({"id": "profile", "source": str(modules / "*.ttl")})
    prov = loaded.provenance
    assert prov.is_set and prov.source_type == "set"
    assert [d["source"] for d in prov.documents] == [
        str(modules / "a-name.ttl"),
        str(modules / "b-email.ttl"),
    ]
    assert prov.triples == len(loaded.graph)
    assert prov.size_bytes == sum(d["size_bytes"] for d in prov.documents)
    assert prov.format == "turtle"


def test_set_is_validated_in_a_single_run_attributed_to_the_set(modules):
    report = validate(BAD_DATA, {"id": "profile", "source": str(modules / "*.ttl")})
    assert not report.conforms
    assert [run.shapes for run in report.runs] == [["profile"]]
    # one violation from each module
    assert report.violations == 2
    assert {r.shapes_id for r in report.results} == {"profile"}
    assert validate(
        GOOD_DATA, {"id": "profile", "source": str(modules / "*.ttl")}
    ).conforms


def test_modules_of_a_set_reference_each_other(tmp_path):
    (tmp_path / "resident.ttl").write_text(ADDRESS_USER_MODULE)
    (tmp_path / "address.ttl").write_text(ADDRESS_MODULE)
    data = PREFIXES + "ex:r a ex:Resident ; ex:address [ ex:street 'x' ] ."
    report = validate(data, {"id": "s", "sources": [str(tmp_path / "*.ttl")]})
    assert not report.conforms
    assert any(r.path == "http://example.org/city" for r in report.results)


def test_sources_mixes_paths_and_patterns_relative_to_base_dir(modules, tmp_path):
    (tmp_path / "extra.ttl").write_text(ADDRESS_MODULE)
    spec = ShapesSpec(
        id="s",
        sources=["profile/v1.0/*.ttl", "extra.ttl", "profile/v1.0/a-name.ttl"],
        base_dir=str(tmp_path),
    )
    prov = load_shapes(spec).provenance
    # sorted expansion, explicit entries in order, duplicates removed
    assert [d["source"] for d in prov.documents] == [
        str(modules / "a-name.ttl"),
        str(modules / "b-email.ttl"),
        str(tmp_path / "extra.ttl"),
    ]
    assert prov.source == "profile/v1.0/*.ttl, extra.ttl, profile/v1.0/a-name.ttl"


def test_recursive_glob(tmp_path):
    (tmp_path / "x" / "y").mkdir(parents=True)
    (tmp_path / "x" / "one.ttl").write_text(NAME_MODULE)
    (tmp_path / "x" / "y" / "two.ttl").write_text(EMAIL_MODULE)
    prov = load_shapes({"id": "s", "source": str(tmp_path / "x" / "**" / "*.ttl")})
    assert len(prov.provenance.documents) == 2


def test_pattern_matching_nothing_is_an_error(tmp_path):
    with pytest.raises(ShapesLoadError, match="No shapes file matches"):
        load_shapes({"id": "s", "source": str(tmp_path / "missing" / "*.ttl")})


def test_set_with_a_single_file_is_still_a_set(modules):
    prov = load_shapes({"id": "s", "sources": [str(modules / "a-name.ttl")]})
    prov = prov.provenance
    assert prov.is_set
    assert prov.sha256 == documents_sha256([sha256_of(modules / "a-name.ttl")])
    assert prov.sha256 != sha256_of(modules / "a-name.ttl")


def test_url_documents_in_a_set(modules, monkeypatch):
    class Response:
        content = ADDRESS_MODULE.encode()
        url = "https://shapes.example.org/address"
        headers = {"Content-Type": "text/turtle"}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        "fair_eva.shacl.loader.requests.get", lambda url, **kwargs: Response()
    )
    prov = load_shapes(
        {
            "id": "s",
            "sources": [str(modules / "a-name.ttl"), "https://shapes.example.org/a"],
        }
    ).provenance
    assert [d["source_type"] for d in prov.documents] == ["file", "url"]
    assert prov.documents[1]["final_url"] == "https://shapes.example.org/address"
    assert prov.documents[1]["sha256"] == hashlib.sha256(Response.content).hexdigest()


def test_set_of_ontologies(modules):
    report = validate(
        GOOD_DATA,
        {"id": "s", "source": str(modules / "a-name.ttl")},
        ontologies={"id": "onto", "source": str(modules / "*.ttl")},
    )
    assert report.ontology_provenance[0].is_set
    assert report.ontology_provenance[0].role == "ontology"


# --------------------------------------------------------------------------- #
# Hash of a set
# --------------------------------------------------------------------------- #
def test_set_hash_is_over_the_sorted_hashes_of_the_documents(modules):
    prov = load_shapes({"id": "s", "source": str(modules / "*.ttl")}).provenance
    digests = sorted(sha256_of(p) for p in modules.glob("*.ttl"))
    manifest = "".join(d + "\n" for d in digests).encode()
    assert prov.sha256 == hashlib.sha256(manifest).hexdigest()
    assert (prov.hash_algorithm, prov.hash_input) == (
        "sha256",
        "sorted-document-sha256",
    )
    assert [d["sha256"] for d in prov.documents] == [
        sha256_of(modules / "a-name.ttl"),
        sha256_of(modules / "b-email.ttl"),
    ]


@pytest.mark.skipif(
    not all(shutil.which(t) for t in ("sha256sum", "cut", "sort", "sh")),
    reason="coreutils not available",
)
def test_set_hash_can_be_reproduced_with_sha256sum(modules):
    prov = load_shapes({"id": "s", "source": str(modules / "*.ttl")}).provenance
    output = subprocess.run(
        ["sh", "-c", "sha256sum *.ttl | cut -d' ' -f1 | LC_ALL=C sort | sha256sum"],
        cwd=modules,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert output.split()[0] == prov.sha256


def test_set_hash_ignores_file_names_and_order(modules, tmp_path):
    original = load_shapes({"id": "s", "source": str(modules / "*.ttl")})
    other = tmp_path / "renamed"
    other.mkdir()
    (other / "z.ttl").write_text(NAME_MODULE)
    (other / "a.ttl").write_text(EMAIL_MODULE)
    renamed = load_shapes(
        {"id": "s", "sources": [str(other / "z.ttl"), str(other / "a.ttl")]}
    )
    assert renamed.provenance.sha256 == original.provenance.sha256


def test_set_hash_changes_when_a_file_is_added_or_modified(modules):
    spec = {"id": "s", "source": str(modules / "*.ttl")}
    before = load_shapes(spec).provenance.sha256
    (modules / "c-address.ttl").write_text(ADDRESS_MODULE)
    added = load_shapes(spec).provenance.sha256
    (modules / "c-address.ttl").write_text(ADDRESS_MODULE + "\n")
    modified = load_shapes(spec).provenance.sha256
    assert len({before, added, modified}) == 3


def test_expected_sha256_pins_the_set(modules):
    digest = load_shapes({"id": "s", "source": str(modules / "*.ttl")})
    digest = digest.provenance.sha256
    pinned = {"id": "s", "source": str(modules / "*.ttl"), "expected_sha256": digest}
    assert load_shapes(pinned).provenance.sha256 == digest
    (modules / "c-address.ttl").write_text(ADDRESS_MODULE)
    with pytest.raises(ShapesLoadError, match="SHA-256 mismatch"):
        load_shapes(pinned)


def test_a_set_larger_than_the_cache_is_parsed_once(tmp_path, monkeypatch):
    from fair_eva.shacl import loader

    for i in range(loader.GRAPH_CACHE_SIZE + 4):
        (tmp_path / ("m%02d.ttl" % i)).write_text(
            PREFIXES + "ex:S%d a sh:NodeShape ; sh:targetClass ex:C%d ." % (i, i)
        )
    calls = []
    parse = loader._parse_document
    monkeypatch.setattr(
        loader, "_parse_document", lambda *a: calls.append(1) or parse(*a)
    )
    spec = {"id": "s", "source": str(tmp_path / "*.ttl")}
    first = load_shapes(spec)
    second = load_shapes(spec)
    assert len(calls) == loader.GRAPH_CACHE_SIZE + 4
    assert second.graph is first.graph
    assert len(second.provenance.documents) == loader.GRAPH_CACHE_SIZE + 4


def test_provenance_of_a_cached_set_is_not_shared(modules):
    spec = {"id": "s", "source": str(modules / "*.ttl")}
    load_shapes(spec).provenance.documents[0]["declared_in_file"]["imports"].append("x")
    assert (
        load_shapes(spec).provenance.documents[0]["declared_in_file"]["imports"] == []
    )


def test_merged_graph_is_cached_until_the_content_changes(modules):
    spec = {"id": "s", "source": str(modules / "*.ttl")}
    first = load_shapes(spec).graph
    assert load_shapes(spec).graph is first
    (modules / "b-email.ttl").write_text(EMAIL_MODULE + "\n")
    assert load_shapes(spec).graph is not first


# --------------------------------------------------------------------------- #
# Name and version of a set
# --------------------------------------------------------------------------- #
def test_set_version_from_config_records_per_document_discrepancies(modules):
    (modules / "b-email.ttl").write_text(versioned(EMAIL_MODULE, "0.9"))
    prov = load_shapes(
        {"id": "s", "source": str(modules / "*.ttl"), "version": "1.0"}
    ).provenance
    assert (prov.version, prov.version_source) == ("1.0", "config")
    assert prov.discrepancies == [
        {
            "field": "version",
            "config": "1.0",
            "file": "0.9",
            "document": str(modules / "b-email.ttl"),
        }
    ]
    assert prov.documents[1]["declared_in_file"]["version"] == "0.9"


def test_set_version_from_the_documents_when_they_agree(modules):
    (modules / "a-name.ttl").write_text(versioned(NAME_MODULE, "1.0"))
    prov = load_shapes({"id": "s", "source": str(modules / "*.ttl")}).provenance
    assert (prov.version, prov.version_source) == ("1.0", "file")
    assert "declared by 1 of 2 documents" in " ".join(prov.notes)


def test_set_version_is_unspecified_when_the_documents_disagree(modules):
    (modules / "a-name.ttl").write_text(versioned(NAME_MODULE, "1.0"))
    (modules / "b-email.ttl").write_text(versioned(EMAIL_MODULE, "2.0"))
    prov = load_shapes({"id": "s", "source": str(modules / "*.ttl")}).provenance
    assert (prov.version, prov.version_source) == (UNSPECIFIED, UNSPECIFIED)
    assert "different versions (1.0, 2.0)" in " ".join(prov.notes)


def test_set_name_is_not_compared_with_the_titles_of_its_modules(modules):
    prov = load_shapes(
        {"id": "s", "source": str(modules / "*.ttl"), "name": "My profile"}
    ).provenance
    assert (prov.name, prov.discrepancies) == ("My profile", [])


def test_describe_mentions_the_number_of_documents(modules):
    prov = load_shapes({"id": "s", "source": str(modules / "*.ttl")}).provenance
    assert "over 2 documents" in prov.describe()


@pytest.mark.parametrize("value", ["unspecified", "Unspecified", " UNSPECIFIED "])
def test_explicit_unspecified_version_is_not_a_declared_version(tmp_path, value):
    path = tmp_path / "s.ttl"
    path.write_text(versioned(NAME_MODULE, "3.0"))
    prov = load_shapes({"source": str(path), "version": value}).provenance
    # the file still provides the version, and there is no discrepancy
    assert (prov.version, prov.version_source) == ("3.0", "file")
    assert prov.discrepancies == []
    path.write_text(NAME_MODULE)
    clear_graph_cache()
    prov = load_shapes({"source": str(path), "version": value}).provenance
    assert (prov.version, prov.version_source) == (UNSPECIFIED, UNSPECIFIED)


# --------------------------------------------------------------------------- #
# Specification and configuration
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "fields",
    [
        {"source": "a.ttl", "sources": ["b.ttl"]},
        {"sources": ["a.ttl"], "content": "x"},
        {"sources": []},
        {"sources": ["  "]},
        {"sources": [1]},
    ],
)
def test_invalid_set_specifications(fields):
    with pytest.raises(ShapesConfigError):
        ShapesSpec(**fields)


def test_sources_accepts_a_single_string():
    assert ShapesSpec(sources="a/*.ttl").sources == ["a/*.ttl"]


def test_glob_source_has_no_file_name_based_default_id(modules):
    prov = load_shapes({"source": str(modules / "*.ttl"), "name": "Profile"})
    assert prov.provenance.id == "Profile"


def config_with(section):
    parser = configparser.ConfigParser()
    parser.read_string(section)
    return parser


def test_sources_in_configuration_one_per_line(modules, tmp_path):
    config = config_with(
        """
[shacl:profile]
sources =
    profile/v1.0/*.ttl
name = Profile
version = 1.0

[plugin]
shacl_sets = ['profile']
"""
    )
    (spec,) = shapes_from_config(config, "plugin", base_dir=str(tmp_path))
    assert spec.sources == ["profile/v1.0/*.ttl"]
    report = validate(BAD_DATA, spec)
    assert report.shapes_provenance[0].documents[0]["source"] == str(
        modules / "a-name.ttl"
    )
    assert report.runs[0].shapes == ["profile"]


def test_sources_in_configuration_as_a_list(tmp_path):
    config = config_with("[shacl:p]\nsources = ['a.ttl', 'b/*.ttl']\n")
    (spec,) = shapes_from_config(config, selection=["p"])
    assert spec.sources == ["a.ttl", "b/*.ttl"]


def test_source_and_sources_together_are_rejected():
    config = config_with("[shacl:p]\nsource = a.ttl\nsources = ['b.ttl']\n")
    with pytest.raises(ShapesConfigError, match="either a 'source'"):
        shapes_from_config(config, selection=["p"])
