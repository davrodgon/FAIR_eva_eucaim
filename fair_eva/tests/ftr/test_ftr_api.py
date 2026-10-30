import pytest
from rdflib import Graph, Literal
from rdflib.namespace import DCTERMS, PROV, RDF

from fair_eva import app
from fair_eva.ftr.indicators import all_indicators
from fair_eva.ftr.vocab import DCAT, FTR
from fair_eva.tests.ftr.support import API_ENDPOINT, PID, PLUGIN

BASE = "/v1.0/ftr"
TURTLE = {"Accept": "text/turtle"}


@pytest.fixture(scope="module")
def client():
    return app.test_client()


def parse(response):
    assert response.status_code == 200, response.text
    media = response.headers["content-type"].split(";")[0]
    return Graph().parse(
        data=response.text, format="turtle" if media == "text/turtle" else "json-ld"
    )


def iris_of_tests(g):
    return {str(t) for t in g.subjects(RDF.type, FTR.Test)}


# ---- tests ---------------------------------------------------------------------------


def test_list_tests_describes_every_indicator_of_every_plugin(client):
    g = parse(client.get(BASE + "/tests", headers=TURTLE))
    tests = iris_of_tests(g)
    assert len(tests) == len(all_indicators())
    assert all(
        "/test/dummy/" in t for t in tests
    )  # '__init__', '__pycache__'... are not plugins


def test_list_tests_default_is_json_ld(client):
    response = client.get(BASE + "/tests")
    assert response.headers["content-type"].startswith("application/ld+json")
    assert response.headers["vary"] == "Accept"
    assert len(iris_of_tests(parse(response))) == len(all_indicators())


def test_list_tests_of_a_plugin(client):
    g = parse(client.get(BASE + "/tests", params={"plugin": PLUGIN}, headers=TURTLE))
    assert len(iris_of_tests(g)) == len(all_indicators())
    assert client.get(BASE + "/tests", params={"plugin": "nope"}).status_code == 404


def test_endpoint_urls_follow_the_request(client):
    g = parse(client.get(BASE + "/tests/dummy/rda_f1_01m", headers=TURTLE))
    (test,) = g.subjects(RDF.type, FTR.Test)
    endpoint = str(g.value(test, DCAT.endpointURL))
    assert endpoint == "http://testserver/v1.0/ftr/assess/test/dummy/rda_f1_01m"


def test_get_one_test(client):
    g = parse(client.get(BASE + "/tests/dummy/rda_f1_01m", headers=TURTLE))
    assert iris_of_tests(g) == {
        "https://example.org/PROVISIONAL/fair-eva/test/dummy/rda_f1_01m"
    }


@pytest.mark.parametrize(
    "testid",
    [
        "dummy/rda_f1_01m",
        "https://example.org/PROVISIONAL/fair-eva/test/dummy/rda_f1_01m",
    ],
)
def test_testid_parameter_of_the_ftr_template(client, testid):
    g = parse(client.get(BASE + "/tests", params={"testid": testid}, headers=TURTLE))
    assert iris_of_tests(g) == {
        "https://example.org/PROVISIONAL/fair-eva/test/dummy/rda_f1_01m"
    }


@pytest.mark.parametrize(
    "path,status",
    [
        ("/tests/nope/rda_f1_01m", 404),
        ("/tests/dummy/rda_zz", 404),
        ("/tests/__pycache__/rda_f1_01m", 404),
        ("/tests?testid=just-one-part", 400),
        ("/metrics/rda_zz", 404),
    ],
)
def test_unknown_resources(client, path, status):
    response = client.get(BASE + path)
    assert response.status_code == status
    assert response.json()["code"] == status


def test_format_parameter_and_unacceptable_accept(client):
    response = client.get(BASE + "/tests/dummy/rda_f1_01m", params={"format": "turtle"})
    assert response.headers["content-type"].startswith("text/turtle")
    assert (
        client.get(
            BASE + "/tests/dummy/rda_f1_01m", headers={"Accept": "text/html"}
        ).status_code
        == 406
    )
    # ... and the format parameter is validated against the enum of the OpenAPI definition
    assert (
        client.get(
            BASE + "/tests/dummy/rda_f1_01m", params={"format": "rdfxml"}
        ).status_code
        == 400
    )


# ---- metrics -------------------------------------------------------------------------


def test_list_metrics(client):
    g = parse(client.get(BASE + "/metrics", headers=TURTLE))
    metrics = {str(m) for m in g.subjects(RDF.type, FTR.Metric)}
    assert len(metrics) == len(all_indicators())
    metric = "https://example.org/PROVISIONAL/fair-eva/metric/rda_f1_01m"
    assert metric in metrics


@pytest.mark.parametrize(
    "request_path",
    [
        "/metrics/rda_f1_01m",
        "/metrics?metricid=rda_f1_01m",
        "/metrics?metricid=https://example.org/PROVISIONAL/fair-eva/metric/rda_f1_01m",
    ],
)
def test_get_one_metric(client, request_path):
    g = parse(client.get(BASE + request_path, headers=TURTLE))
    assert [str(m) for m in g.subjects(RDF.type, FTR.Metric)] == [
        "https://example.org/PROVISIONAL/fair-eva/metric/rda_f1_01m"
    ]


# ---- assessment ----------------------------------------------------------------------


def assess(client, indicator="rda_f1_01m", **body):
    body.setdefault("resource_identifier", PID)
    body.setdefault("api_endpoint", API_ENDPOINT)
    return client.post(
        "%s/assess/test/dummy/%s" % (BASE, indicator), json=body, headers=TURTLE
    )


def test_assess_test_returns_a_test_result(client):
    g = parse(assess(client))
    (result,) = g.subjects(RDF.type, FTR.TestResult)
    assert g.value(result, PROV.value) == Literal("pass")
    assert len(iris_of_tests(g)) == 1  # the test the result comes from


def test_assess_test_failure_is_a_result_not_an_http_error(client):
    g = parse(assess(client, resource_identifier="internal"))
    (result,) = g.subjects(RDF.type, FTR.TestResult)
    assert g.value(result, PROV.value) == Literal("fail")


def test_assess_test_error_is_an_indeterminate_result(client):
    g = parse(assess(client, indicator="rda_a1_02m"))
    (result,) = g.subjects(RDF.type, FTR.TestResult)
    assert g.value(result, PROV.value) == Literal("indeterminate")


def test_assess_test_in_json_ld(client):
    response = client.post(
        BASE + "/assess/test/dummy/rda_f1_01m",
        json={"resource_identifier": PID, "api_endpoint": API_ENDPOINT},
    )
    assert response.headers["content-type"].startswith("application/ld+json")
    assert len(list(parse(response).subjects(RDF.type, FTR.TestResult))) == 1


@pytest.mark.parametrize(
    "path,body,status",
    [
        ("/assess/test/nope/rda_f1_01m", {"resource_identifier": PID}, 404),
        ("/assess/test/dummy/rda_zz", {"resource_identifier": PID}, 404),
        ("/assess/test/dummy/rda_f1_01m", {}, 400),
        ("/assess/test/dummy/rda_f1_01m", {"resource_identifier": " "}, 400),
        # no api_endpoint in the request nor in the configuration
        ("/assess/test/dummy/rda_f1_01m", {"resource_identifier": PID}, 400),
        ("/assess/plugin/nope", {"resource_identifier": PID}, 404),
        (
            "/assess/plugin/dummy",
            {
                "resource_identifier": PID,
                "api_endpoint": API_ENDPOINT,
                "tests": ["rda_zz"],
            },
            400,
        ),
    ],
)
def test_assess_errors(client, path, body, status):
    response = client.post(BASE + path, json=body)
    assert response.status_code == status, response.text
    # Our errors carry "code"; those of the request validation of connexion, "status"
    error = response.json()
    assert error.get("code", error.get("status")) == status


def test_assess_plugin_returns_a_result_set(client):
    response = client.post(
        BASE + "/assess/plugin/dummy",
        json={
            "resource_identifier": PID,
            "api_endpoint": API_ENDPOINT,
            "tests": ["rda_f1_01m", "rda_f1_02m", "rda_a1_02m"],
        },
        headers=TURTLE,
    )
    g = parse(response)
    (result_set,) = g.subjects(RDF.type, FTR.TestResultSet)
    assert len(set(g.objects(result_set, PROV.hadMember))) == 3
    assert g.value(result_set, DCTERMS.license) is not None


def test_assess_plugin_runs_every_test_by_default(client):
    response = client.post(
        BASE + "/assess/plugin/dummy",
        json={"resource_identifier": PID, "api_endpoint": API_ENDPOINT},
        headers=TURTLE,
    )
    g = parse(response)
    assert len(list(g.subjects(RDF.type, FTR.TestResult))) == len(all_indicators())


# ---- the existing API is untouched ---------------------------------------------------


def test_existing_endpoints_keep_returning_json(client):
    response = client.post(
        "/v1.0/rda/rda_f1_01m",
        json={"repo": PLUGIN, "id": PID, "api_endpoint": API_ENDPOINT},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()[PID]["test_status"] == "pass"


def test_both_apis_publish_their_description(client):
    main = client.get("/v1.0/openapi.json").json()
    ftr = client.get(BASE + "/openapi.json").json()
    assert "/rda/rda_f1_01m" in main["paths"]
    assert "/ftr/tests" not in main["paths"]
    assert (
        "/tests" in ftr["paths"] and "/assess/test/{plugin}/{indicator}" in ftr["paths"]
    )


def test_unacceptable_format_is_refused_before_running_the_test(client, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("the test was executed")

    monkeypatch.setattr("fair_eva.ftr.api.run_tests", must_not_run)
    response = client.post(
        BASE + "/assess/test/dummy/rda_f1_01m",
        json={"resource_identifier": PID, "api_endpoint": API_ENDPOINT},
        headers={"Accept": "text/html"},
    )
    assert response.status_code == 406


def test_invalid_ftr_configuration_is_reported(client, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("[ftr] base_uri must be an absolute IRI, got 'x'")

    monkeypatch.setattr("fair_eva.ftr.api.load_settings", broken)
    response = client.get(BASE + "/tests/dummy/rda_f1_01m")
    assert response.status_code == 500
    assert "Invalid [ftr] configuration" in response.json()["message"]
