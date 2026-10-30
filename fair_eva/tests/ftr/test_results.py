from datetime import datetime, timezone

import pytest
from rdflib import Literal, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import DCTERMS, PROV, RDF, XSD

from fair_eva.ftr import results
from fair_eva.ftr.execution import (
    FtrRequestError,
    Message,
    Outcome,
    normalize_messages,
    run_tests,
)
from fair_eva.ftr.identifiers import Identifiers
from fair_eva.ftr.indicators import get_indicator
from fair_eva.ftr.vocab import FTR
from fair_eva.tests.ftr.support import API_ENDPOINT, PID, PLUGIN

F1_01M = get_indicator("rda_f1_01m")


def run(indicator, resource, clock=None):
    kwargs = {"clock": clock} if clock else {}
    return run_tests(
        PLUGIN,
        [get_indicator(indicator)],
        resource,
        api_endpoint=API_ENDPOINT,
        **kwargs,
    )[0]


# ---- execution -----------------------------------------------------------------------


def test_passing_test(clock):
    o = run("rda_f1_01m", PID, clock)
    assert (o.status, o.points, o.error) == ("pass", 100, None)
    assert o.messages == [
        Message("Found persistent identifier for the metadata: %s" % PID, 100)
    ]
    assert o.started_at < o.ended_at


def test_failing_test():
    o = run("rda_f1_01m", "internal")
    assert (o.status, o.points, o.error) == ("fail", 0, None)
    assert o.messages[0].points == 0


def test_execution_error_is_indeterminate_not_fail():
    # This indicator resolves the PID over the network, which is disabled in tests
    o = run("rda_a1_02m", PID)
    assert o.status == "indeterminate"
    assert o.points == 0
    assert o.error.startswith("Exception:")


def test_one_plugin_instance_serves_all_the_indicators(clock):
    outcomes = run_tests(
        PLUGIN,
        [get_indicator(i) for i in ("rda_f1_01m", "rda_f1_02m", "rda_f3_01m")],
        PID,
        api_endpoint=API_ENDPOINT,
        clock=clock,
    )
    assert [o.indicator.id for o in outcomes] == [
        "rda_f1_01m",
        "rda_f1_02m",
        "rda_f3_01m",
    ]
    assert all(o.resource_identifier == PID for o in outcomes)


def test_evaluator_logs_are_attached_to_their_own_outcome():
    o = run("rda_r1_1_01m", "internal")
    assert any(line.startswith("[WARNING] No access information") for line in o.log)
    # Logs of one indicator do not leak into the next one
    assert run("rda_f1_01m", PID).log == []


def test_unknown_plugin_cannot_be_started():
    with pytest.raises(FtrRequestError) as error:
        run_tests("nope", [F1_01M], PID, api_endpoint=API_ENDPOINT)
    assert error.value.status == 400
    assert "nope" in error.value.message


@pytest.mark.parametrize("resource", ["", "  ", None])
def test_resource_identifier_is_required(resource):
    with pytest.raises(FtrRequestError) as error:
        run_tests(PLUGIN, [F1_01M], resource, api_endpoint=API_ENDPOINT)
    assert error.value.status == 400


def test_plugin_without_endpoint_is_a_request_error():
    # No api_endpoint in the request and no [Generic] endpoint in the configuration
    with pytest.raises(FtrRequestError) as error:
        run_tests(PLUGIN, [F1_01M], PID)
    assert error.value.status == 400


def test_messages_are_normalised():
    assert normalize_messages(None) == []
    assert normalize_messages("text") == [Message("text")]
    assert normalize_messages(
        [{"message": "a", "points": 50}, "b", {"message": "c"}]
    ) == [
        Message("a", 50),
        Message("b"),
        Message("c"),
    ]


# ---- RDF -----------------------------------------------------------------------------


def single(g, cls):
    (node,) = g.subjects(RDF.type, cls)
    return node


def test_result_properties(settings, clock):
    o = run("rda_f1_01m", PID, clock)
    g = results.result_graph(settings, o)
    result = single(g, FTR.TestResult)
    ids = Identifiers(settings.base_uri)
    test = ids.test(PLUGIN, "rda_f1_01m")

    assert str(result).startswith(settings.base_uri + "/result/")
    assert g.value(result, DCTERMS.identifier) == Literal(str(result))
    assert g.value(result, PROV.value) == Literal("pass")
    assert g.value(result, FTR.completion) == Literal(100, datatype=XSD.integer)
    assert g.value(result, FTR.outputFromTest) == test
    assert (test, RDF.type, FTR.Test) in g  # the test is described in the result
    assert g.value(result, DCTERMS.license) == URIRef(settings.result_license)
    assert "Found persistent identifier" in str(g.value(result, FTR.log))
    assert "[100/100]" in str(g.value(result, FTR.log))

    target = g.value(result, FTR.assessmentTarget)
    assert target == URIRef("https://doi.org/" + PID)
    assert g.value(target, DCTERMS.identifier) == Literal(PID)

    activity = g.value(result, PROV.wasGeneratedBy)
    assert (activity, RDF.type, FTR.TestExecutionActivity) in g
    assert g.value(activity, PROV.used) == target
    assert g.value(activity, PROV.wasAssociatedWith) == test
    started = g.value(activity, PROV.startedAtTime).toPython()
    ended = g.value(activity, PROV.endedAtTime).toPython()
    assert started == datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc)
    assert ended == datetime(2026, 1, 31, 10, 0, 1, tzinfo=timezone.utc)
    assert g.value(activity, PROV.endedAtTime).datatype == XSD.dateTime
    assert g.value(result, PROV.generatedAtTime) == g.value(activity, PROV.endedAtTime)


def test_a_pass_gets_an_explicit_no_improvement_suggestion(settings):
    g = results.result_graph(settings, run("rda_f1_01m", PID))
    (guidance,) = g.objects(single(g, FTR.TestResult), FTR.suggestion)
    assert (guidance, RDF.type, FTR.GuidanceContext) in g
    assert "No improvement" in str(g.value(guidance, DCTERMS.description))


def test_a_fail_turns_the_unsatisfied_checks_into_suggestions(settings):
    o = run("rda_f1_01m", "internal")
    g = results.result_graph(settings, o)
    descriptions = {
        str(g.value(s, DCTERMS.description))
        for s in g.objects(single(g, FTR.TestResult), FTR.suggestion)
    }
    assert descriptions == {"Identifier is not persistent for the metadata: internal"}
    assert g.value(single(g, FTR.TestResult), PROV.value) == Literal("fail")
    assert g.value(single(g, FTR.TestResult), FTR.completion).toPython() == 0


def test_an_error_is_reported_in_the_log_and_the_suggestion(settings):
    g = results.result_graph(settings, run("rda_a1_02m", PID))
    result = single(g, FTR.TestResult)
    assert g.value(result, PROV.value) == Literal("indeterminate")
    assert "Exception:" in str(g.value(result, FTR.log))
    (guidance,) = g.objects(result, FTR.suggestion)
    assert "could not be completed" in str(g.value(guidance, DCTERMS.title))


def test_a_failure_without_messages_still_gets_a_suggestion(settings, clock):
    now = clock()
    o = Outcome(PLUGIN, F1_01M, "x", now, now, "fail")
    assert results.result_log(o) == "The test did not produce any message."
    ((title, text),) = results.suggestions(o)
    assert "Review the metadata" in text


def test_fractional_points_are_rounded_to_an_integer(settings, clock):
    now = clock()
    o = Outcome(PLUGIN, F1_01M, "x", now, now, "indeterminate", points=66.67)
    g = results.result_graph(settings, o)
    assert g.value(single(g, FTR.TestResult), FTR.completion) == Literal(
        67, datatype=XSD.integer
    )


def test_same_execution_same_iris(settings):
    def fixed():
        return datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc)

    first = results.result_graph(settings, run("rda_f1_01m", PID, fixed))
    second = results.result_graph(settings, run("rda_f1_01m", PID, fixed))
    assert isomorphic(first, second)


def test_different_executions_get_different_iris(settings, clock):
    first = results.result_graph(settings, run("rda_f1_01m", PID, clock))
    second = results.result_graph(settings, run("rda_f1_01m", PID, clock))
    assert single(first, FTR.TestResult) != single(second, FTR.TestResult)


def test_result_set(settings, clock):
    outcomes = run_tests(
        PLUGIN,
        [get_indicator(i) for i in ("rda_f1_01m", "rda_f1_02m")],
        "internal",
        api_endpoint=API_ENDPOINT,
        clock=clock,
    )
    g = results.result_set_graph(settings, outcomes)
    rs = single(g, FTR.TestResultSet)
    assert len(set(g.objects(rs, PROV.hadMember))) == 2
    assert all(
        (m, RDF.type, FTR.TestResult) in g for m in g.objects(rs, PROV.hadMember)
    )
    assert (
        str(g.value(rs, DCTERMS.description))
        == "0 of 2 tests passed (0 indeterminate)."
    )
    activity = g.value(rs, PROV.wasGeneratedBy)
    assert len(set(g.objects(activity, PROV.wasAssociatedWith))) == 2
    # The set activity spans the activities of its members
    assert g.value(activity, PROV.startedAtTime).toPython() == outcomes[0].started_at
    assert g.value(activity, PROV.endedAtTime).toPython() == outcomes[-1].ended_at


def test_empty_result_set_is_rejected(settings):
    with pytest.raises(ValueError):
        results.result_set_graph(settings, [])


@pytest.mark.parametrize(
    "identifier,expected",
    [
        ("10.1234/abcd.5678", "https://doi.org/10.1234/abcd.5678"),
        ("doi:10.1234/abcd.5678", "https://doi.org/10.1234/abcd.5678"),
        ("https://example.org/records/1", "https://example.org/records/1"),
        ("http://example.org/a b", "http://example.org/a%20b"),
        (
            "urn:uuid:123e4567-e89b-12d3-a456-426614174000",
            "urn:uuid:123e4567-e89b-12d3-a456-426614174000",
        ),
        # Numeric ids are not turned into a PubMed URL: they are repository-internal
        ("123456", "https://example.org/base/resource/dummy/123456"),
        ("internal id/1", "https://example.org/base/resource/dummy/internal%20id%2F1"),
    ],
)
def test_assessment_target_iri(identifier, expected):
    assert (
        str(Identifiers("https://example.org/base/").target("dummy", identifier))
        == expected
    )
