import pytest

from fair_eva.api import rda
from fair_eva.ftr import settings as ftr_settings
from fair_eva.ftr.settings import Agent, load_settings, settings_from_section


def test_config_ini_ships_provisional_defaults():
    s = load_settings()
    assert "PROVISIONAL" in s.base_uri
    assert "PROVISIONAL" in s.contact.name
    assert s.contact.uri == s.base_uri + "/contact"
    assert s.version.startswith("fair_eva-")
    # Nothing is published unless the deployer decides it
    assert s.publisher is None and s.creator is None
    assert s.applicable_for == ()


def test_defaults_without_ftr_section():
    s = settings_from_section({})
    assert s.base_uri == ftr_settings.DEFAULT_BASE_URI
    assert s.endpoint_base_url == ""
    assert s.tool_uri == s.code_repository


def test_values_are_read_and_normalised():
    s = settings_from_section(
        {
            "base_uri": "https://ex.org/fair/",
            "endpoint_base_url": "https://api.ex.org/ftr/",
            "test_license": "https://ex.org/lic-t",
            "result_license": "https://ex.org/lic-r",
            "contact_type": "Individual",
            "contact_name": "Ada",
            "contact_email": "ada@ex.org",
            "publisher_name": "Pub",
            "creator_uri": "https://ex.org/people/ada",
            "creator_type": "individual",
            "tool_uri": "https://ex.org/tool",
            "applicable_for": "https://schema.org/Dataset, https://schema.org/SoftwareSourceCode",
            "version": "7",
        }
    )
    assert s.base_uri == "https://ex.org/fair"
    assert s.endpoint_base_url == "https://api.ex.org/ftr"
    assert (s.test_license, s.result_license) == (
        "https://ex.org/lic-t",
        "https://ex.org/lic-r",
    )
    assert s.contact == Agent(
        uri="https://ex.org/fair/contact",
        name="Ada",
        kind="individual",
        email="mailto:ada@ex.org",
    )
    assert s.publisher == Agent(uri="https://ex.org/fair/publisher", name="Pub")
    assert s.creator.uri == "https://ex.org/people/ada"
    assert s.tool_uri == "https://ex.org/tool"
    assert s.applicable_for == (
        "https://schema.org/Dataset",
        "https://schema.org/SoftwareSourceCode",
    )
    assert s.version == "7"


@pytest.mark.parametrize(
    "section",
    [
        {"base_uri": "not an iri"},
        {"test_license": "Apache-2.0"},
        {"applicable_for": "Dataset"},
        {"contact_type": "robot"},
    ],
)
def test_invalid_values_are_rejected(section):
    with pytest.raises(ValueError):
        settings_from_section(section)


@pytest.fixture
def fake_plugins(tmp_path, monkeypatch):
    package = tmp_path / "ftr_fake_plugins"
    (package / "mine").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "mine" / "__init__.py").write_text("")
    (package / "mine" / "config.ini").write_text(
        "[ftr]\n"
        "applicable_for = https://schema.org/Dataset\n"
        "contact_name = Plugin contact\n"
        "base_uri = https://elsewhere.example/\n"
        "result_license = https://elsewhere.example/license\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return "ftr_fake_plugins"


def test_plugin_config_overrides_the_plugin_level_keys(fake_plugins):
    main = load_settings()
    s = load_settings("mine", fake_plugins)
    assert s.applicable_for == ("https://schema.org/Dataset",)
    assert s.contact.name == "Plugin contact"
    # Deployment-wide keys cannot be overridden by a plugin
    assert s.base_uri == main.base_uri
    assert s.result_license == main.result_license


def test_plugin_without_ftr_section_uses_the_main_config():
    assert load_settings("dummy", rda.PLUGIN_PATH) == load_settings()
    assert load_settings("does-not-exist", rda.PLUGIN_PATH) == load_settings()
