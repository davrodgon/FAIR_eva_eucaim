"""Reading the SHACL configuration (``[shacl:<id>]`` sections, selection, options)."""

import configparser

import pytest

from fair_eva.shacl import (
    ShapesConfigError,
    available_set_ids,
    load_core_config,
    ontologies_from_config,
    options_from_config,
    selected_ids,
    shapes_from_config,
)
from fair_eva.shacl.config import combine_from_config, spec_from_section

INI = """
[shacl:basic]
source = shapes/basic.ttl
format = turtle
name = Basic shapes
version = 1.2.0

[shacl:extra]
source = https://shapes.example.org/extra%20v2.jsonld?download=1
version = "2.0"
expected_sha256 = sha256:{digest}
timeout = 5

[shacl:classes]
source = /abs/classes.ttl

[shacl]
default_sets = ['basic']
default_ontologies = classes
inference = rdfs
allow_warnings = yes
combine = merged

[plugin_a]
shacl_sets = ['basic', 'extra']

[plugin_b]
shacl_sets = extra, basic
shacl_ontologies = []

[plugin_c]
identifier_term = []
""".format(
    digest="ab" * 32
)


@pytest.fixture
def config():
    parser = configparser.ConfigParser()
    parser.read_string(INI)
    return parser


def test_available_set_ids(config):
    assert available_set_ids(config) == ["basic", "extra", "classes"]


def test_spec_from_section_resolves_all_fields(config):
    spec = spec_from_section(config, "basic", base_dir="/plugins/x")
    assert (spec.id, spec.source, spec.format) == (
        "basic",
        "shapes/basic.ttl",
        "turtle",
    )
    assert (spec.name, spec.version, spec.base_dir) == (
        "Basic shapes",
        "1.2.0",
        "/plugins/x",
    )

    extra = spec_from_section(config, "extra")
    # '%' in URLs is not subject to ConfigParser interpolation; quotes are stripped
    assert extra.source == "https://shapes.example.org/extra%20v2.jsonld?download=1"
    assert extra.version == "2.0"
    assert extra.expected_sha256 == "ab" * 32
    assert extra.timeout == 5.0


def test_selection_precedence(config):
    assert selected_ids(config, "plugin_a") == ["basic", "extra"]  # plugin section
    assert selected_ids(config, "plugin_b") == ["extra", "basic"]  # comma separated
    assert selected_ids(config, "plugin_c") == ["basic"]  # global default
    assert selected_ids(config) == ["basic"]
    assert selected_ids(config, "plugin_a", selection=["extra"]) == [
        "extra"
    ]  # explicit
    assert selected_ids(config, "plugin_a", selection=[]) == []


def test_ontology_selection(config):
    assert selected_ids(config, "plugin_a", ontologies=True) == ["classes"]
    assert selected_ids(config, "plugin_b", ontologies=True) == []
    specs = ontologies_from_config(config, "plugin_a", base_dir="/b")
    assert [s.id for s in specs] == ["classes"]


def test_shapes_from_config(config):
    specs = shapes_from_config(config, "plugin_a", base_dir="/b")
    assert [s.id for s in specs] == ["basic", "extra"]
    assert all(s.base_dir == "/b" for s in specs)
    assert shapes_from_config(configparser.ConfigParser()) == []


def test_empty_selection_value_selects_nothing():
    parser = configparser.ConfigParser()
    parser.read_string("[plugin]\nshacl_sets =\n")
    assert selected_ids(parser, "plugin") == []


def test_unknown_set_lists_the_configured_ones(config):
    with pytest.raises(ShapesConfigError, match="Unknown SHACL shape set 'nope'") as e:
        shapes_from_config(config, selection=["nope"])
    assert "basic" in str(e.value)


@pytest.mark.parametrize(
    "ini, message",
    [
        ("[shacl:a]\nformat = turtle\n", "needs a 'source'"),
        ("[shacl:a]\nsource = x.ttl\nsourse = typo\n", "Unknown key"),
        ("[shacl:a]\nsource = x.ttl\ntimeout = soon\n", "timeout must be a number"),
        ("[shacl:a]\nsource = x.ttl\nexpected_sha256 = 12\n", "64 hexadecimal"),
        ("[shacl]\ndefault_sets = ['a'\n", "Invalid list"),
        ("[shacl]\ndefault_sets = [1, 2]\n", "list of strings"),
    ],
)
def test_invalid_configuration(ini, message):
    parser = configparser.ConfigParser()
    parser.read_string(ini)
    with pytest.raises(ShapesConfigError, match=message):
        shapes_from_config(parser, selection=None if "default_sets" in ini else ["a"])


def test_options_from_config(config):
    options = options_from_config(config)
    assert options.inference == "rdfs"
    assert options.allow_warnings is True
    assert options.advanced is False
    assert combine_from_config(config) == "merged"


def test_options_defaults_without_section():
    parser = configparser.ConfigParser()
    options = options_from_config(parser)
    assert options.inference == "none" and options.allow_warnings is False
    assert combine_from_config(parser) == "separate"


@pytest.mark.parametrize(
    "ini, message",
    [
        ("[shacl]\ninference = magic\n", "inference"),
        ("[shacl]\nadvanced = maybe\n", "advanced"),
    ],
)
def test_invalid_options(ini, message):
    parser = configparser.ConfigParser()
    parser.read_string(ini)
    with pytest.raises(ShapesConfigError, match=message):
        options_from_config(parser)


def test_invalid_combine():
    parser = configparser.ConfigParser()
    parser.read_string("[shacl]\ncombine = union\n")
    with pytest.raises(ShapesConfigError, match="combine"):
        combine_from_config(parser)


def test_core_config_ships_no_shape_sets():
    """The core defines no shapes of its own: they come from plugins/users."""
    core = load_core_config()
    assert core.has_section("Generic")
    assert available_set_ids(core) == []
    assert selected_ids(core, "anything") == []
