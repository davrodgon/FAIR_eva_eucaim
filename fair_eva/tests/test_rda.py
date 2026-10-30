import pytest

from fair_eva import app
from fair_eva.api import rda
from fair_eva.tests.plugins.dummy.plugin import PID

PLUGIN = "dummy"
URL = "/v1.0/rda/rda_f1_01m"

# Metadata indicators that can be evaluated offline with the dummy plugin
OFFLINE_INDICATORS = [
    "rda_f1_01m",
    "rda_f1_02m",
    "rda_f2_01m",
    "rda_f3_01m",
    "rda_f4_01m",
    "rda_a1_01m",
    "rda_a1_03m",
    "rda_a1_04m",
    "rda_a1_1_01m",
    "rda_a2_01m",
    "rda_i1_01m",
    "rda_i1_02m",
    "rda_i2_01m",
    "rda_i3_01m",
    "rda_i3_02m",
    "rda_i3_03m",
    "rda_i3_04m",
    "rda_r1_01m",
    "rda_r1_1_01m",
    "rda_r1_1_02m",
    "rda_r1_1_03m",
    "rda_r1_2_01m",
    "rda_r1_2_02m",
    "rda_r1_3_01m",
]


@pytest.fixture(autouse=True)
def dummy_plugin_path(monkeypatch):
    monkeypatch.setattr(rda, "PLUGIN_PATH", "fair_eva.tests.plugins")


@pytest.fixture(scope="module")
def client():
    return app.test_client()


def test_endpoints_lists_plugins(client):
    response = client.get("/v1.0/endpoints")
    assert response.status_code == 200
    assert PLUGIN in response.json()


def test_f1_01m_persistent_id(client):
    response = client.post(
        URL, json={"repo": PLUGIN, "id": PID, "api_endpoint": "http://x"}
    )
    assert response.status_code == 200
    result = response.json()[PID]
    assert result["name"] == "RDA_F1_01M"
    assert result["points"] == 100
    assert result["test_status"] == "pass"
    assert result["score"] == {"earned": 100, "total": 100}


def test_f1_01m_internal_id(client):
    response = client.post(
        URL, json={"repo": PLUGIN, "id": "internal", "api_endpoint": "http://x"}
    )
    assert response.status_code == 200
    result = response.json()["internal"]
    assert result["points"] == 0
    assert result["test_status"] == "fail"


def test_query_evaluates_every_returned_id(client):
    # The API schema requires "id"; "q" takes precedence over it
    response = client.post(
        URL,
        json={"repo": PLUGIN, "id": "ignored", "q": "any", "api_endpoint": "http://x"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body[PID]["points"] == 100
    assert body["other-id"]["points"] == 0


def test_evaluator_logs_are_returned(client):
    response = client.post(
        URL, json={"repo": PLUGIN, "id": PID, "api_endpoint": "http://x"}
    )
    assert isinstance(response.json()["evaluator_logs"], list)


def test_unknown_plugin_returns_400(client):
    response = client.post(URL, json={"repo": "nope", "id": PID})
    assert response.status_code == 400
    assert "nope" in response.text


@pytest.mark.parametrize("indicator", OFFLINE_INDICATORS)
def test_indicator_returns_well_formed_result(client, indicator):
    response = client.post(
        "/v1.0/rda/%s" % indicator,
        json={"repo": PLUGIN, "id": PID, "api_endpoint": "http://x"},
    )
    assert response.status_code == 200
    result = response.json()[PID]
    # Sub-principles are named with dots, e.g. RDA_R1.1_01M
    assert result["name"].replace(".", "_") == indicator.upper()
    assert 0 <= result["points"] <= 100
    assert result["test_status"] in ("fail", "indeterminate", "pass")
    assert result["score"]["earned"] == result["points"]
    assert result["score"]["total"] == 100


@pytest.mark.parametrize("indicator", ["rda_f1_01m", "rda_f1_02m", "rda_f3_01m"])
def test_identifier_indicators_pass_with_persistent_id(client, indicator):
    response = client.post(
        "/v1.0/rda/%s" % indicator,
        json={"repo": PLUGIN, "id": PID, "api_endpoint": "http://x"},
    )
    assert response.json()[PID]["points"] == 100


@pytest.mark.xfail(
    strict=True,
    reason="rda.py error handlers reference 'points', which is unbound when the "
    "indicator raises: the API answers 500 instead of 422",
)
def test_indicator_failure_returns_422(client):
    # rda_a1_02m resolves the PID over the network, which is disabled in tests
    response = client.post(
        "/v1.0/rda/rda_a1_02m",
        json={"repo": PLUGIN, "id": PID, "api_endpoint": "http://x"},
    )
    assert response.status_code == 422
