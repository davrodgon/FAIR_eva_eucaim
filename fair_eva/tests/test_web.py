"""Tests for the Flask web front-end (``fair_eva/web.py``)."""

import copy
import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "fair_eva"

CONFIG = """
[local]
logo_url = https://example.org
title = FAIR EVA test
only_local = False
repo = dummy

[Repositories]
dummy = dummy
oai-pmh = oai-pmh

[dummy]
oai_base = http://oai.example/oai
"""

DOI = "10.1234/abcd.5678"


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    """Import ``fair_eva/web.py`` as a module with a throw-away config file.

    ``web.py`` parses ``sys.argv`` and reads the configuration at import time and uses
    top-level imports (``api.utils``, ``utils.pdf_gen``), hence the path/argv setup.
    """
    cfg = tmp_path_factory.mktemp("web") / "config.ini"
    cfg.write_text(CONFIG)
    old_argv = sys.argv
    sys.argv = ["web.py", "-c", str(cfg)]
    sys.path.insert(0, str(PACKAGE_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "fair_eva_web", PACKAGE_DIR / "web.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["fair_eva_web"] = module
        spec.loader.exec_module(module)
    finally:
        sys.argv = old_argv
        sys.path.remove(str(PACKAGE_DIR))
    return module


@pytest.fixture
def client(web, monkeypatch):
    # web.py never sets a secret key nor finds the templates (see xfail tests below),
    # so the fixture provides what a real deployment is expected to have.
    monkeypatch.setattr(web.app, "template_folder", str(REPO_ROOT / "templates"))
    monkeypatch.setattr(web.app, "static_folder", str(REPO_ROOT / "static"))
    monkeypatch.setattr(web.app, "secret_key", "test")
    return web.app.test_client()


def make_indicator(name, points, weight=20):
    return {
        "name": name,
        "points": points,
        "color": "#2ECC71",
        "score": {"weight": weight},
        "msg": [{"message": "ok", "points": points}],
    }


def make_api_result(with_data_test=False):
    """Body returned by the (mocked) ``/v1.0/rda/rda_all`` endpoint."""
    block = {
        "findable": {
            "rda_f1_01m": make_indicator("rda_f1_01m", 100, 20),
            "rda_f1_02m": make_indicator("rda_f1_02m", 50, 10),
        },
        "accessible": {"rda_a1_01m": make_indicator("rda_a1_01m", 0, 20)},
        "interoperable": {"rda_i1_01m": make_indicator("rda_i1_01m", 75, 15)},
        "reusable": {"rda_r1_01m": make_indicator("rda_r1_01m", 90, 20)},
    }
    if with_data_test:
        block["data_test"] = {"rda_d1_01d": make_indicator("rda_d1_01d", 100, 20)}
        block["data_test"]["rda_d1_01d"]["msg"] = "all fine"
    return {"fair_dummy": block, "evaluator_logs": {"x": 1}}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        # views mutate the parsed answer: hand out a fresh copy each time
        return copy.deepcopy(self.payload)


@pytest.fixture
def api(web, monkeypatch):
    """Mock every external call made by the evaluator/export_pdf views."""
    post = mock.Mock(return_value=FakeResponse(make_api_result()))
    monkeypatch.setattr(web.requests, "post", post)
    monkeypatch.setattr(web.ut, "check_url", mock.Mock(return_value=True))
    doi_flow = mock.Mock(return_value=("dummy", "http://flow.example/oai"))
    monkeypatch.setattr(web.Smart_plugin, "doi_flow", doi_flow)
    # fair_chart is broken with bokeh>=3 (see test_fair_chart_with_bokeh_3), keep the
    # view tests independent of the installed bokeh version.
    monkeypatch.setattr(
        web, "fair_chart", mock.Mock(return_value=("<script></script>", "<div></div>"))
    )
    post.doi_flow = doi_flow
    return post


def sent_body(post):
    return json.loads(post.call_args.kwargs["data"])


def evaluator_url(**params):
    params.setdefault("item_id", DOI)
    params.setdefault("repo", "dummy")
    query = "&".join("%s=%s" % (k, v) for k, v in params.items() if v is not None)
    return "/en/evaluator?" + query


# --------------------------------------------------------------------------- helpers


def test_repo_oai_base(web):
    assert web.repo_oai_base("dummy") == "http://oai.example/oai"
    assert web.repo_oai_base("oai-pmh") == ""  # no section
    assert web.repo_oai_base("nonexistent") == ""


def test_repo_oai_base_section_without_oai_base(web, monkeypatch):
    web.config.add_section("nooai")
    try:
        web.config["nooai"]["foo"] = "bar"
        assert web.repo_oai_base("nooai") == ""
    finally:
        web.config.remove_section("nooai")


def test_check_id_form_fields(web, monkeypatch):
    monkeypatch.setattr(web.app, "secret_key", "test")
    with web.app.test_request_context():
        form = web.CheckIDForm()
        assert set(form.repo.choices) == {"dummy", "oai-pmh"}
        assert "item_id" in form.data and "oai_base" in form.data


def test_set_parser(web, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["web.py"])
    assert web.set_parser().config_file == "config.ini"
    monkeypatch.setattr(sys, "argv", ["web.py", "-c", "other.ini"])
    assert web.set_parser().config_file == "other.ini"


@pytest.mark.parametrize(
    "header, expected",
    [
        ("en-GB,en;q=0.9", "en"),
        ("en-IE", "en"),
        ("es-MX", "es"),
        ("fr-FR", "es"),  # no match -> default locale
        ("", "es"),
    ],
)
def test_fallback_lang(web, header, expected):
    headers = {"Accept-Language": header} if header else {}
    with web.app.test_request_context("/", headers=headers):
        assert web.fallback_lang() == expected


def test_lang_in_session(web, monkeypatch):
    monkeypatch.setattr(web.app, "secret_key", "test")
    with web.app.test_request_context("/"):
        assert not web.lang_in_session()
        web.session["lang"] = "fr"
        assert not web.lang_in_session()
        web.session["lang"] = "en"
        assert web.lang_in_session()


# ----------------------------------------------------------------- language handling


@pytest.mark.parametrize("lang", ["es", "en", "en-GB", "es-MX"])
def test_get_locale_from_path(web, monkeypatch, lang):
    monkeypatch.setattr(web.app, "secret_key", "test")
    with web.app.test_request_context("/%s/faq" % lang):
        assert web.get_locale() == lang
        assert web.session["lang"] == lang


def test_get_locale_from_session(web, monkeypatch):
    monkeypatch.setattr(web.app, "secret_key", "test")
    with web.app.test_request_context("/unknown"):
        web.session["lang"] = "en"
        assert web.get_locale() == "en"


def test_get_locale_from_accept_language(web, monkeypatch):
    monkeypatch.setattr(web.app, "secret_key", "test")
    headers = {"Accept-Language": "en-US,en;q=0.8"}
    with web.app.test_request_context("/unknown", headers=headers):
        assert web.get_locale() == "en"
        assert web.session["lang"] == "en"


def test_language_is_remembered_across_requests(client):
    client.get("/en/faq")
    response = client.get("/unknown-page")  # no language in the path
    assert response.status_code == 302
    assert response.headers["Location"] == "/en/not-found"


# ------------------------------------------------------------------- simple pages


@pytest.mark.parametrize("lang", ["es", "en"])
def test_index(client, lang):
    response = client.get("/%s" % lang)
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'name="item_id"' in page
    # advanced parameters are only offered through the "select a plugin" link
    assert 'name="repo"' not in page
    assert "FAIR EVA test" in page  # TITLE from config


def test_index_with_additional_params(client, api):
    page = client.get(evaluator_url(item_id="")).get_data(as_text=True)
    assert 'name="repo"' in page
    assert 'name="oai_base"' in page


@pytest.mark.parametrize("page", ["faq", "not-found", "about_us", "evaluations"])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_static_pages(client, page, lang):
    response = client.get("/%s/%s" % (lang, page))
    assert response.status_code == 200
    assert "<html" in response.get_data(as_text=True)


@pytest.mark.parametrize("page", ["about_us", "evaluations"])
def test_pages_depend_on_language(client, page):
    es = client.get("/es/%s" % page).get_data(as_text=True)
    en = client.get("/en/%s" % page).get_data(as_text=True)
    assert es != en


# --------------------------------------------------------------------- catch_all


def test_root_redirects_to_home_in_negotiated_language(client):
    response = client.get("/", headers={"Accept-Language": "en"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/en"


def test_root_redirects_to_default_language(client):
    response = client.get("/")
    assert response.headers["Location"] == "/es"


def test_catch_all_unknown_page_with_language(client):
    response = client.get("/en/does-not-exist")
    assert response.status_code == 302
    assert response.headers["Location"] == "/en/not-found"


def test_catch_all_unknown_page_without_language(client):
    response = client.get("/does-not-exist", headers={"Accept-Language": "en"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/en/not-found"


def test_catch_all_known_page_without_language(client):
    response = client.get("/about_us?x=1", headers={"Accept-Language": "en"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/en/about_us?x=1"


def test_catch_all_extra_prefix_segment_is_dropped(client):
    response = client.get("/prefix/evaluations")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/evaluations")
    response = client.get("/prefix/en/evaluations")
    assert response.headers["Location"] == "/en/evaluations"


@pytest.mark.xfail(
    strict=True,
    reason="BUG: catch_all returns None (HTTP 500) for a two-segment path whose "
    "first segment is not a language and whose second is not a known page",
)
def test_catch_all_unknown_two_segment_path(client):
    response = client.get("/foo/bar")
    assert response.status_code in (302, 404)


@pytest.mark.xfail(
    strict=True,
    reason="BUG: language variants listed in BABEL_LOCALES (en-GB, es-MX...) have no "
    "routes/endpoints, so redirecting to their home or pages raises BuildError",
)
@pytest.mark.parametrize("path", ["/en-GB", "/en-GB/evaluations", "/en-GB/nope"])
def test_catch_all_language_variants(client, path):
    response = client.get(path)
    assert response.status_code == 302


# ---------------------------------------------------------------------- evaluator


def test_evaluator_empty_item_id_shows_form(client, api):
    response = client.get(evaluator_url(item_id=""))
    assert response.status_code == 200
    assert 'name="item_id"' in response.get_data(as_text=True)
    api.assert_not_called()


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a request without item_id raises KeyError, which is swallowed and "
    "later causes UnboundLocalError (HTTP 500) instead of showing the form",
)
def test_evaluator_missing_item_id_shows_form(client, api):
    response = client.get("/en/evaluator")
    assert response.status_code == 200


def test_evaluator_with_explicit_repo(client, api):
    response = client.get(evaluator_url())
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "http://doi.org/%s" % DOI in page
    body = sent_body(api)
    assert body["id"] == DOI
    assert body["repo"] == "dummy"
    assert body["lang"] == "en"
    # oai_base defaults to the one configured for the repo
    assert body["oai_base"] == "http://oai.example/oai"
    assert api.call_args.args[0] == "http://localhost:9090/v1.0/rda/rda_all"
    assert api.call_args.kwargs["headers"] == {"Content-Type": "application/json"}
    api.doi_flow.assert_not_called()


def test_evaluator_computes_weighted_score(client, api):
    page = client.get(evaluator_url()).get_data(as_text=True)
    # (100*20 + 50*10 + 0*20 + 75*15 + 90*20) / (20+10+20+15+20) = 5425/85
    assert "63.82" in page


def test_evaluator_spanish(client, api):
    response = client.get("/es/evaluator?item_id=%s&repo=dummy" % DOI)
    assert response.status_code == 200
    assert sent_body(api)["lang"] == "es"


def test_evaluator_uses_smart_plugin_when_no_repo(client, api):
    response = client.get(evaluator_url(repo=None))
    assert response.status_code == 200
    api.doi_flow.assert_called_once_with(DOI)
    body = sent_body(api)
    assert body["repo"] == "dummy"
    assert body["oai_base"] == "http://flow.example/oai"


def test_evaluator_only_local_uses_configured_repo(client, api, web, monkeypatch):
    monkeypatch.setitem(web.config["local"], "only_local", "True")
    response = client.get(evaluator_url(repo="ignored"))
    assert response.status_code == 200
    assert sent_body(api)["repo"] == "dummy"
    api.doi_flow.assert_not_called()


def test_evaluator_no_repo_found_shows_form(client, api):
    api.doi_flow.return_value = (None, None)
    response = client.get(evaluator_url(repo=None))
    assert response.status_code == 200
    assert 'name="item_id"' in response.get_data(as_text=True)
    api.assert_not_called()


def test_evaluator_oai_base_argument_is_used_when_valid(client, api):
    assert client.get(evaluator_url(oai_base="http://custom/oai")).status_code == 200
    assert sent_body(api)["oai_base"] == "http://custom/oai"


def test_evaluator_oai_base_argument_ignored_when_unreachable(client, api, web):
    web.ut.check_url.return_value = False
    assert client.get(evaluator_url(oai_base="http://custom/oai")).status_code == 200
    assert sent_body(api)["oai_base"] == "http://oai.example/oai"


def test_evaluator_unreachable_default_oai_base_is_blanked(client, api, web):
    web.ut.check_url.return_value = False
    assert client.get(evaluator_url()).status_code == 200
    assert sent_body(api)["oai_base"] == ""


def test_evaluator_check_url_error_is_tolerated(client, api, web):
    web.ut.check_url.side_effect = ValueError("boom")
    assert client.get(evaluator_url()).status_code == 200


def test_evaluator_plain_mode(client, api):
    plain = client.get(evaluator_url(plain="True")).get_data(as_text=True)
    full = client.get(evaluator_url()).get_data(as_text=True)
    assert "Indicator Level" in full
    assert "Indicator Level" not in plain
    assert "Your Digital Object is" in plain


def test_evaluator_plain_false_uses_full_template(client, api):
    page = client.get(evaluator_url(plain="False")).get_data(as_text=True)
    assert "Indicator Level" in page


def test_evaluator_with_data_test_block(client, api):
    api.return_value = FakeResponse(make_api_result(with_data_test=True))
    response = client.get(evaluator_url())
    assert response.status_code == 200
    assert "all fine" in response.get_data(as_text=True)


def test_evaluator_translations_directory_is_switched(client, api, web, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(web.os.path, "exists", lambda path: True)
        assert client.get(evaluator_url()).status_code == 200
        assert web.app.config["BABEL_TRANSLATION_DIRECTORIES"] == (
            "plugins/dummy/translations"
        )
    # the next request resets it to the generic translations
    client.get(evaluator_url())
    assert web.app.config["BABEL_TRANSLATION_DIRECTORIES"] == "translations"


def test_evaluator_api_unreachable_for_pid(client, api):
    api.side_effect = ConnectionError("API down")
    response = client.get(evaluator_url())
    assert response.status_code == 200
    # the error page links to the resolver URL of every detected PID type
    assert "http://doi.org/%s" % DOI in response.get_data(as_text=True)


def test_evaluator_api_unreachable_for_internal_id(client, api):
    api.side_effect = ConnectionError("API down")
    response = client.get(evaluator_url(item_id="not-a-pid"))
    assert response.status_code == 200
    assert "not-a-pid" in response.get_data(as_text=True)


def test_evaluator_malformed_api_result_shows_error(client, api):
    api.return_value = FakeResponse({"fair": {"findable": {"a": {"name": "x"}}}})
    response = client.get(evaluator_url())
    assert response.status_code == 200
    assert DOI in response.get_data(as_text=True)


def test_evaluator_zero_weight_shows_error(client, api):
    result = make_api_result()
    for group in result["fair_dummy"].values():
        for indicator in group.values():
            indicator["score"]["weight"] = 0
    api.return_value = FakeResponse(result)
    response = client.get(evaluator_url())
    assert response.status_code == 200
    assert "Your Digital Object is" not in response.get_data(as_text=True)


def test_evaluator_internal_id_success(client, api):
    response = client.get(evaluator_url(item_id="not-a-pid"))
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Your Digital Object is" in page
    assert 'href="not-a-pid"' in page  # internal ids are not turned into URLs


# ------------------------------------------------------------------- export_pdf


@pytest.fixture
def pdf(web, api, monkeypatch):
    create_pdf = mock.Mock(return_value=b"%PDF-1.4 fake")
    monkeypatch.setattr(web.pdf_gen, "create_pdf", create_pdf)
    # export_pdf cannot cope with the "evaluator_logs" key the real API always adds
    # (see test_export_pdf_with_real_api_payload), so the other tests omit it.
    payload = make_api_result()
    del payload["evaluator_logs"]
    api.return_value = FakeResponse(payload)
    return create_pdf


@pytest.mark.xfail(
    strict=True,
    reason="BUG: export_pdf takes the last key of the API answer as the results, but "
    "the API always appends 'evaluator_logs' last (evaluator() skips it), so PDF "
    "export always fails with the real API",
)
def test_export_pdf_with_real_api_payload(client, api, pdf):
    api.return_value = FakeResponse(make_api_result())  # includes evaluator_logs
    response = client.get("/en/export_pdf?item_id=%s&repo=dummy" % DOI)
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"


def test_export_pdf(client, api, pdf):
    response = client.get("/en/export_pdf?item_id=%s&repo=dummy" % DOI)
    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert (
        response.headers["Content-Disposition"]
        == "attachment; filename=fair_report.pdf"
    )
    assert response.data == b"%PDF-1.4 fake"
    args = pdf.call_args.args
    assert args[1] == "fair_report.pdf"
    assert args[2] == "http://doi.org/%s" % DOI
    assert args[5] == 63.82
    # the group summary is injected before calling the generator
    assert args[0]["findable"]["result"]["points"] == 83.33
    assert sent_body(api)["id"] == DOI


def test_export_pdf_without_repo_uses_smart_plugin(client, api, pdf):
    assert client.get("/es/export_pdf?item_id=%s" % DOI).status_code == 200
    api.doi_flow.assert_called_with(DOI)
    assert sent_body(api)["repo"] == "dummy"


def test_export_pdf_only_local_with_unset_repo_uses_smart_plugin(
    client, api, pdf, web, monkeypatch
):
    monkeypatch.setitem(web.config["local"], "only_local", "True")
    monkeypatch.setitem(web.config["local"], "repo", "None")
    assert client.get("/en/export_pdf?item_id=%s" % DOI).status_code == 200
    api.doi_flow.assert_called_with(DOI)


def test_export_pdf_translations_directory_is_switched(
    client, api, pdf, web, monkeypatch
):
    monkeypatch.setattr(web.os.path, "exists", lambda path: True)
    assert client.get("/en/export_pdf?item_id=%s&repo=dummy" % DOI).status_code == 200
    assert web.app.config["BABEL_TRANSLATION_DIRECTORIES"] == (
        "plugins/dummy/translations"
    )


def test_export_pdf_oai_base_argument(client, api, pdf):
    url = "/en/export_pdf?item_id=%s&repo=dummy&oai_base=http://custom/oai" % DOI
    assert client.get(url).status_code == 200
    assert sent_body(api)["oai_base"] == "http://custom/oai"


def test_export_pdf_unreachable_oai_base_is_blanked(client, api, pdf, web):
    web.ut.check_url.return_value = False
    assert client.get("/en/export_pdf?item_id=%s&repo=dummy" % DOI).status_code == 200
    assert sent_body(api)["oai_base"] == ""


def test_export_pdf_check_url_error_is_tolerated(client, api, pdf, web):
    web.ut.check_url.side_effect = ValueError("boom")
    assert client.get("/en/export_pdf?item_id=%s&repo=dummy" % DOI).status_code == 200


@pytest.mark.xfail(
    strict=True,
    reason="BUG: export_pdf without item_id swallows the KeyError and then returns "
    "None (HTTP 500) instead of a 4xx answer",
)
def test_export_pdf_missing_item_id(client, api, pdf):
    response = client.get("/en/export_pdf")
    assert response.status_code == 400


@pytest.mark.xfail(
    strict=True,
    reason="BUG: when the API call or PDF generation fails, export_pdf logs the "
    "error and returns None, so Flask answers with an HTTP 500 TypeError",
)
def test_export_pdf_failure_returns_http_error_response(client, api, pdf):
    api.side_effect = ConnectionError("API down")
    response = client.get("/en/export_pdf?item_id=%s&repo=dummy" % DOI)
    assert response.status_code in (400, 502, 503)


# ------------------------------------------------------------------- app set-up


@pytest.mark.xfail(
    strict=True,
    reason="BUG: no SECRET_KEY is configured, so session['lang'] raises RuntimeError "
    "and every page returns HTTP 500 out of the box",
)
def test_app_works_without_manual_secret_key(web, monkeypatch):
    monkeypatch.setattr(web.app, "template_folder", str(REPO_ROOT / "templates"))
    monkeypatch.setattr(web.app, "secret_key", None)
    assert web.app.test_client().get("/en/faq").status_code == 200


@pytest.mark.xfail(
    strict=True,
    reason="BUG: Flask(__name__) looks for templates in fair_eva/templates, but they "
    "live in <repo>/templates since web.py moved into the package",
)
def test_default_template_folder_exists(web):
    assert (Path(web.app.root_path) / web.app.template_folder).is_dir()


# -------------------------------------------------------------------------- charts


def scored_blocks(with_result=True):
    block = make_api_result()["fair_dummy"]
    for group in block.values():
        points = sum(i["points"] * i["score"]["weight"] for i in group.values())
        weight = sum(i["score"]["weight"] for i in group.values())
        for indicator in group.values():
            indicator["name_smart"] = indicator["name"]
        if with_result:
            group["result"] = {"points": round(points / weight, 2), "color": "#fff"}
    return block


def bokeh_major():
    import bokeh

    return int(bokeh.__version__.split(".")[0])


def test_group_chart(web):
    script, div = web.group_chart(scored_blocks(with_result=False))
    assert "<script" in script
    assert "<div" in div


@pytest.mark.skipif(
    bokeh_major() >= 3, reason="plot_height was removed in bokeh 3 (see xfail below)"
)
def test_fair_chart(web):
    script, div = web.fair_chart(scored_blocks(), 63.8)
    assert "<script" in script
    assert "<div" in div


@pytest.mark.skipif(bokeh_major() < 3, reason="only relevant with bokeh>=3")
@pytest.mark.xfail(
    strict=True,
    reason="BUG: fair_chart passes plot_height to figure(), removed in bokeh 3 "
    "(bokeh is not pinned), so every evaluation page crashes with AttributeError",
)
def test_fair_chart_with_bokeh_3(web):
    script, div = web.fair_chart(scored_blocks(), 63.8)
    assert "<div" in div
