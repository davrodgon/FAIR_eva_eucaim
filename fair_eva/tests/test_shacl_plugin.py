"""Plugin integration: ``EvaluatorBase.validate_shacl`` and the REST endpoint."""

import configparser
import hashlib

import pytest
from rdflib import Graph

from fair_eva import app
from fair_eva.api import rda
from fair_eva.api.evaluator import EvaluatorBase
from fair_eva.shacl import (
    DataGraphError,
    ShapesConfigError,
    ShapesLoadError,
    ValidationOptions,
)
from fair_eva.tests.plugins.shacl_demo import plugin as demo
from fair_eva.tests.test_shacl import BAD_DATA, GOOD_DATA, NAME_SHAPES, PREFIXES

PLUGIN = "shacl_demo"
DEMO_SHAPES = demo.__file__.rsplit("/", 1)[0] + "/shapes/person.ttl"


class MinimalEvaluator(EvaluatorBase):
    """Concrete evaluator bypassing the plugin configuration machinery."""

    def __init__(self, config, name="my_plugin", graph=None):
        self.config = config
        self.name = name
        self.graph = graph

    def get_metadata(self):
        return None

    def get_rdf_graph(self):
        return self.graph


def make_config(text):
    parser = configparser.ConfigParser()
    parser.read_string(text)
    return parser


@pytest.fixture
def shapes_dir(tmp_path):
    (tmp_path / "person.ttl").write_text(NAME_SHAPES)
    return tmp_path


@pytest.fixture
def evaluator(shapes_dir, monkeypatch):
    monkeypatch.setattr(
        "fair_eva.shacl.plugin.plugin_directory", lambda _evaluator: str(shapes_dir)
    )
    config = make_config(
        """
        [shacl:person]
        source = person.ttl
        version = 1.0

        [my_plugin]
        shacl_sets = ['person']
        """
    )
    return MinimalEvaluator(config, graph=Graph().parse(data=BAD_DATA, format="turtle"))


# --------------------------------------------------------------------------- #
# EvaluatorBase.validate_shacl
# --------------------------------------------------------------------------- #
def test_evaluator_validates_with_configured_sets(evaluator, shapes_dir):
    report = evaluator.validate_shacl()
    assert report.conforms is False and report.violations == 1
    prov = report.shapes_provenance[0]
    assert prov.id == "person" and prov.version == "1.0"
    assert (
        prov.sha256
        == hashlib.sha256((shapes_dir / "person.ttl").read_bytes()).hexdigest()
    )
    assert prov.source == str(
        shapes_dir / "person.ttl"
    )  # resolved against the plugin dir


def test_explicit_graph_selection_and_options(evaluator, shapes_dir):
    (shapes_dir / "other.ttl").write_text(
        NAME_SHAPES.replace("minCount 1", "minCount 2")
    )
    evaluator.config.read_string("[shacl:other]\nsource = other.ttl\n")
    good = Graph().parse(data=GOOD_DATA, format="turtle")
    report = evaluator.validate_shacl(data_graph=good, shape_sets=["person", "other"])
    assert [p.id for p in report.shapes_provenance] == ["person", "other"]
    assert [run.conforms for run in report.runs] == [True, False]

    merged = evaluator.validate_shacl(
        data_graph=good,
        shape_sets=["person"],
        combine="merged",
        options={"allow_warnings": True},
    )
    assert merged.mode == "merged" and merged.options["allow_warnings"] is True


def test_global_options_apply_and_can_be_overridden(evaluator):
    evaluator.config.read_string("[shacl]\ninference = rdfs\ncombine = merged\n")
    report = evaluator.validate_shacl()
    assert report.options["inference"] == "rdfs" and report.mode == "merged"
    report = evaluator.validate_shacl(combine="separate", options={"inference": "none"})
    assert report.options["inference"] == "none" and report.mode == "separate"


def test_options_object_replaces_the_configured_ones(evaluator):
    evaluator.config.read_string("[shacl]\ninference = rdfs\n")
    report = evaluator.validate_shacl(options=ValidationOptions(abort_on_first=True))
    assert report.options["inference"] == "none"
    assert report.options["abort_on_first"] is True


def test_ontologies_from_config(evaluator, shapes_dir):
    (shapes_dir / "classes.ttl").write_text(
        PREFIXES + "ex:Student rdfs:subClassOf ex:Person ."
    )
    evaluator.config.read_string(
        "[shacl:classes]\nsource = classes.ttl\nversion = 3\n"
        "[shacl]\ndefault_ontologies = ['classes']\ninference = rdfs\n"
    )
    evaluator.graph = Graph().parse(
        data=PREFIXES + "ex:sam a ex:Student .", format="turtle"
    )
    report = evaluator.validate_shacl()
    assert report.conforms is False
    assert [(p.id, p.version) for p in report.ontology_provenance] == [("classes", "3")]


def test_missing_configuration_and_graph(evaluator):
    bare = MinimalEvaluator(
        make_config("[my_plugin]\nidentifier_term = []\n"), graph=Graph()
    )
    with pytest.raises(ShapesConfigError, match="No SHACL shape sets are configured"):
        bare.validate_shacl()
    with pytest.raises(ShapesConfigError, match="no configuration"):
        MinimalEvaluator(None).validate_shacl()

    evaluator.graph = None
    with pytest.raises(DataGraphError, match="get_rdf_graph"):
        evaluator.validate_shacl()

    with pytest.raises(ShapesConfigError, match="Unknown SHACL shape set"):
        evaluator.validate_shacl(data_graph=Graph(), shape_sets=["ghost"])


def test_default_hook_returns_none():
    assert MinimalEvaluator.get_rdf_graph(MinimalEvaluator(None)) is None


def test_missing_shapes_file_is_a_clear_error(evaluator, shapes_dir):
    (shapes_dir / "person.ttl").unlink()
    with pytest.raises(ShapesLoadError, match="not found"):
        evaluator.validate_shacl()


# --------------------------------------------------------------------------- #
# Real plugin package + RDA endpoint + SHACL endpoint
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def demo_plugin_path(monkeypatch):
    monkeypatch.setattr(rda, "PLUGIN_PATH", "fair_eva.tests.plugins")


@pytest.fixture(scope="module")
def client():
    return app.test_client()


@pytest.mark.parametrize("item_id, points", [("good", 100), ("bad", 0)])
def test_indicator_in_a_plugin_uses_the_validation(client, item_id, points):
    response = client.post(
        "/v1.0/rda/rda_r1_3_01m",
        json={"repo": PLUGIN, "id": item_id, "api_endpoint": "http://x"},
    )
    assert response.status_code == 200
    result = response.json()[item_id]
    assert result["points"] == points
    messages = result["msg"]
    digest = hashlib.sha256(open(DEMO_SHAPES, "rb").read()).hexdigest()
    assert any(
        digest in m["message"] and "version 1.0.0" in m["message"] for m in messages
    )
    if points == 0:
        assert any("A person must have a name" in m["message"] for m in messages)


def test_endpoint_validates_with_the_plugin_configuration(client):
    response = client.post(
        "/v1.0/shacl/validate", json={"data": BAD_DATA, "repo": PLUGIN}
    )
    assert response.status_code == 200
    report = response.json()
    assert report["conforms"] is False
    assert report["summary"]["violations"] == 1
    [prov] = report["shapes_provenance"]
    assert (prov["id"], prov["name"], prov["version"]) == (
        "demo_person",
        "Person shapes",
        "1.0.0",
    )
    assert prov["sha256"] == hashlib.sha256(open(DEMO_SHAPES, "rb").read()).hexdigest()
    assert report["results"][0]["focus_node"] == "http://example.org/bob"

    ok = client.post("/v1.0/shacl/validate", json={"data": GOOD_DATA, "repo": PLUGIN})
    assert ok.json()["conforms"] is True


def test_endpoint_accepts_other_formats_and_explicit_sets(client):
    jsonld = '{"@context": {"ex": "http://example.org/"}, "@id": "ex:x", "@type": "ex:Person"}'
    response = client.post(
        "/v1.0/shacl/validate",
        json={
            "data": jsonld,
            "format": "json-ld",
            "repo": PLUGIN,
            "shape_sets": ["demo_person"],
            "combine": "merged",
        },
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "merged"
    assert response.json()["conforms"] is False


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"data": ""}, "Missing 'data'"),
        ({"data": BAD_DATA}, "No SHACL shape sets"),  # core ships none
        ({"data": BAD_DATA, "repo": "ghost"}, "Could not find plugin"),
        ({"data": BAD_DATA, "repo": PLUGIN, "shape_sets": ["ghost"]}, "Unknown SHACL"),
        ({"data": "not turtle at all", "repo": PLUGIN}, "Could not parse"),
        (
            {"data": BAD_DATA, "repo": PLUGIN, "format": "docx"},
            "Unsupported RDF format",
        ),
    ],
)
def test_endpoint_errors_are_400(client, body, fragment):
    response = client.post("/v1.0/shacl/validate", json=body)
    assert response.status_code == 400
    assert fragment in response.json()["message"]


def test_endpoint_requires_data_field(client):
    assert client.post("/v1.0/shacl/validate", json={"repo": PLUGIN}).status_code == 400


def test_endpoint_reports_unloadable_shapes_as_server_error(client, monkeypatch):
    monkeypatch.setattr(
        "fair_eva.api.shacl_api.load_core_config",
        lambda: make_config("[shacl:broken]\nsource = /nonexistent/shapes.ttl\n"),
    )
    response = client.post(
        "/v1.0/shacl/validate", json={"data": BAD_DATA, "shape_sets": ["broken"]}
    )
    assert response.status_code == 500
    assert "not found" in response.json()["message"]


def test_endpoint_reports_unreadable_plugin_configuration(client, monkeypatch):
    def broken(_package):
        raise FileNotFoundError("config.ini is gone")

    monkeypatch.setattr(EvaluatorBase, "load_config", staticmethod(broken))
    response = client.post(
        "/v1.0/shacl/validate", json={"data": BAD_DATA, "repo": PLUGIN}
    )
    assert response.status_code == 500
    assert "config.ini is gone" in response.json()["message"]
