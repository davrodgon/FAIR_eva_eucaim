from pathlib import Path

import pytest
import yaml

from fair_eva import app
from fair_eva.api import rda
from fair_eva.tests.plugins.dummy.plugin import PID, Plugin

URL = "/v1.0/rda/rda_all"
BODY = {"repo": "dummy", "id": PID, "api_endpoint": "http://x"}
GROUPS = {
    "Findable": "findable",
    "Accessible": "accessible",
    "Interoperable": "interoperable",
    "Reusable": "reusable",
}
# These indicators resolve the PID over the network, which is disabled in tests
NETWORK_INDICATORS = {"rda_a1_02m": "accessible", "rda_r1_3_02m": "reusable"}


def api_paths():
    api = Path(rda.__file__).resolve().parents[1] / "fair-api.yaml"
    return yaml.safe_load(api.read_text())["paths"]


def expected_indicators():
    """Map indicator code -> (group, weight) as declared in the API spec."""
    expected = {}
    for path, spec in api_paths().items():
        if spec.get("x-indicator"):
            group = next(g for p, g in GROUPS.items() if p in spec["x-principle"])
            expected[path.split("/")[-1]] = (group, spec["x-level"])
    return expected


@pytest.fixture(autouse=True)
def dummy_plugin_path(monkeypatch):
    monkeypatch.setattr(rda, "PLUGIN_PATH", "fair_eva.tests.plugins")


@pytest.fixture(scope="module")
def client():
    return app.test_client()


def evaluate(client):
    response = client.post(URL, json=BODY)
    assert response.status_code == 200
    return response.json()[PID]


def test_rda_all_survives_failing_indicators(client):
    result = evaluate(client)
    assert set(result) == set(GROUPS.values())  # no data tests in the dummy plugin
    for code, group in NETWORK_INDICATORS.items():
        entry = result[group][code]
        assert entry["name"] == "[ERROR] - %s" % code
        assert entry["msg"].startswith("Exception: ")
        assert entry["points"] == 0
        assert entry["test_status"] == "fail"
        assert entry["score"]["earned"] == 0


def test_rda_all_reports_every_indicator_in_its_group_with_its_weight(client):
    result = evaluate(client)
    expected = expected_indicators()
    found = {code: group for group in result for code in result[group]}
    assert found == {code: group for code, (group, _) in expected.items()}
    for code, (group, weight) in expected.items():
        assert result[group][code]["score"]["weight"] == weight


def test_failing_first_indicator_does_not_reuse_previous_values(client, monkeypatch):
    def boom(self):
        raise ValueError("boom")

    monkeypatch.setattr(Plugin, "rda_f1_01m", boom)
    result = evaluate(client)
    failed = result["findable"]["rda_f1_01m"]
    assert failed["name"] == "[ERROR] - rda_f1_01m"
    assert failed["msg"] == "Exception: boom"
    assert failed["points"] == 0
    # the following indicator keeps its own result
    assert result["findable"]["rda_f1_02m"]["name"] == "rda_f1_02m"
    assert result["findable"]["rda_f1_02m"]["points"] == 100


def test_failure_is_filed_under_its_own_principle(client, monkeypatch):
    # rda_i1_01m follows the accessible indicators: its error must not land there
    def boom(self, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(Plugin, "rda_i1_01m", boom)
    result = evaluate(client)
    assert result["interoperable"]["rda_i1_01m"]["name"] == "[ERROR] - rda_i1_01m"
    assert "rda_i1_01m" not in result["accessible"]
    assert result["accessible"]["rda_a2_01m"]["name"] == "rda_a2_01m"


def test_data_tests_are_included_when_the_plugin_implements_them(client, monkeypatch):
    monkeypatch.setattr(Plugin, "data_01", lambda self: (100, "ok"), raising=False)
    result = evaluate(client)
    entry = result["data_test"]["data_01"]
    assert entry["points"] == 100
    assert entry["score"]["weight"] == api_paths()["/plugins/data_01"]["x-level"]
    # data_02 is not implemented: it is skipped instead of reported as an error
    assert "data_02" not in result["data_test"]
