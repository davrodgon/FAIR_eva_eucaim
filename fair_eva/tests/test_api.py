import pytest

from fair_eva import app


@pytest.fixture(scope="module")
def client():
    return app.test_client()


def test_endpoints_without_plugins_returns_404(client):
    # Plugins live in `fair_eva.plugin`, which is not installed in the repo
    response = client.get("/v1.0/endpoints")
    assert response.status_code == 404


def test_rda_without_id_or_query_returns_400(client):
    response = client.post("/v1.0/rda/rda_f1_01m", json={"repo": "example"})
    assert response.status_code == 400
