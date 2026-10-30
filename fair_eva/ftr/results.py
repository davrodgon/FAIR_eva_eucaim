"""Test executions as FTR test results and test result sets."""

from datetime import timezone

from rdflib import Literal, URIRef
from rdflib.namespace import DCTERMS, PROV, RDF, XSD

from fair_eva.ftr.describe import add_test, new_graph
from fair_eva.ftr.identifiers import Identifiers
from fair_eva.ftr.vocab import FTR, INDETERMINATE, PASS

# Maximum score reported by FAIR EVA indicators.
MAX_POINTS = 100


def xsd_datetime(value):
    """xsd:dateTime literal in UTC, e.g. ``2026-01-31T10:00:00+00:00``."""
    return Literal(value.astimezone(timezone.utc), datatype=XSD.dateTime)


def _score(points):
    return ("%g" % points) if points is not None else "?"


def result_log(outcome):
    """Single ``ftr:log`` text: one line per check, then the relevant evaluator logs."""
    lines = []
    for message in outcome.messages:
        if message.points is None:
            lines.append(message.text)
        else:
            lines.append(
                "[%s/%d] %s" % (_score(message.points), MAX_POINTS, message.text)
            )
    if outcome.error:
        lines.append(outcome.error)
    lines.extend("evaluator %s" % line for line in outcome.log)
    return "\n".join(lines) or "The test did not produce any message."


def result_description(outcome):
    indicator = outcome.indicator
    summary = "%s (%s plugin) on %s: %s, score %s/%d." % (
        indicator.code,
        outcome.plugin,
        outcome.resource_identifier,
        outcome.status,
        _score(outcome.points),
        MAX_POINTS,
    )
    if outcome.error:
        return "%s The test could not be completed: %s" % (summary, outcome.error)
    texts = [m.text for m in outcome.messages if m.text]
    return " ".join([summary] + texts)


def suggestions(outcome):
    """``(title, description)`` pairs that guide the user to improve the resource.

    FAIR EVA messages carry the points of each check; those that did not earn the full
    score are the recommendations. The FTR shape for test results requires at least one
    suggestion, so a result without any gets an explicit "nothing to improve" one.
    """
    indicator = outcome.indicator
    if outcome.error:
        return [
            (
                "%s: the test could not be completed" % indicator.code,
                "The test failed with an error and could not assess the resource (%s). "
                "Check the plugin configuration and that the services it queries are "
                "reachable." % outcome.error,
            )
        ]
    found = [
        m.text
        for m in outcome.messages
        if m.text and m.points is not None and m.points < MAX_POINTS
    ]
    if not found and outcome.status != PASS:
        found = [
            "Review the metadata of the resource against the indicator: %s."
            % indicator.title
        ]
    if not found:
        found = [
            "No improvement is needed: every check of this indicator was satisfied."
        ]
    return [
        ("%s: suggestion %d" % (indicator.code, n), t) for n, t in enumerate(found, 1)
    ]


def _add_target(g, ids, plugin, resource_identifier):
    target = ids.target(plugin, resource_identifier)
    g.add((target, RDF.type, PROV.Entity))
    g.add((target, DCTERMS.identifier, Literal(str(resource_identifier))))
    return target


def add_result(g, settings, outcome):
    """Add the ``ftr:TestResult`` of ``outcome`` (with its test and activity) to ``g``."""
    ids = Identifiers(settings.base_uri)
    indicator = outcome.indicator
    test = add_test(g, settings, outcome.plugin, indicator)
    target = _add_target(g, ids, outcome.plugin, outcome.resource_identifier)

    activity = ids.execution(test, target, outcome.ended_at)
    g.add((activity, RDF.type, FTR.TestExecutionActivity))
    g.add((activity, PROV.used, target))
    g.add((activity, PROV.wasAssociatedWith, test))
    g.add((activity, PROV.startedAtTime, xsd_datetime(outcome.started_at)))
    g.add((activity, PROV.endedAtTime, xsd_datetime(outcome.ended_at)))

    result = ids.result(test, target, outcome.ended_at)
    g.add((result, RDF.type, FTR.TestResult))
    g.add((result, DCTERMS.identifier, Literal(str(result))))
    g.add(
        (
            result,
            DCTERMS.title,
            Literal(
                "Output from running test: %s [%s] (%s)"
                % (indicator.code, outcome.plugin, test)
            ),
        )
    )
    g.add((result, DCTERMS.description, Literal(result_description(outcome))))
    g.add((result, DCTERMS.license, URIRef(settings.result_license)))
    g.add((result, PROV.value, Literal(outcome.status)))
    g.add((result, FTR.log, Literal(result_log(outcome))))
    # The shape requires an integer (see docs/ftr_ostrails.md); 0..100 as in FAIR EVA.
    g.add(
        (
            result,
            FTR.completion,
            Literal(int(round(outcome.points)), datatype=XSD.integer),
        )
    )
    g.add((result, FTR.outputFromTest, test))
    g.add((result, FTR.assessmentTarget, target))
    g.add((result, PROV.wasGeneratedBy, activity))
    g.add((result, PROV.generatedAtTime, xsd_datetime(outcome.ended_at)))

    for number, (title, description) in enumerate(suggestions(outcome), 1):
        guidance = ids.suggestion(result, number)
        g.add((guidance, RDF.type, FTR.GuidanceContext))
        g.add((guidance, DCTERMS.title, Literal(title)))
        g.add((guidance, DCTERMS.description, Literal(description)))
        g.add((result, FTR.suggestion, guidance))
    return result


def result_graph(settings, outcome):
    g = new_graph()
    add_result(g, settings, outcome)
    return g


def result_set_graph(settings, outcomes):
    """``ftr:TestResultSet`` with one ``ftr:TestResult`` per outcome.

    All the outcomes must refer to the same plugin and resource (one request).
    """
    if not outcomes:
        raise ValueError("A test result set needs at least one outcome")
    first = outcomes[0]
    ids = Identifiers(settings.base_uri)
    g = new_graph()
    results = [add_result(g, settings, outcome) for outcome in outcomes]
    tests = [ids.test(o.plugin, o.indicator.id) for o in outcomes]
    target = _add_target(g, ids, first.plugin, first.resource_identifier)
    started_at = min(o.started_at for o in outcomes)
    ended_at = max(o.ended_at for o in outcomes)

    activity = ids.set_execution(tests, target, ended_at)
    g.add((activity, RDF.type, FTR.TestExecutionActivity))
    g.add((activity, PROV.used, target))
    for test in tests:
        g.add((activity, PROV.wasAssociatedWith, test))
    g.add((activity, PROV.startedAtTime, xsd_datetime(started_at)))
    g.add((activity, PROV.endedAtTime, xsd_datetime(ended_at)))

    result_set = ids.result_set(tests, target, ended_at)
    g.add((result_set, RDF.type, FTR.TestResultSet))
    g.add((result_set, DCTERMS.identifier, Literal(str(result_set))))
    g.add(
        (
            result_set,
            DCTERMS.title,
            Literal(
                "Results from running %d tests of the %s plugin on %s"
                % (len(outcomes), first.plugin, first.resource_identifier)
            ),
        )
    )
    passed = sum(1 for o in outcomes if o.status == PASS)
    undecided = sum(1 for o in outcomes if o.status == INDETERMINATE)
    g.add(
        (
            result_set,
            DCTERMS.description,
            Literal(
                "%d of %d tests passed (%d indeterminate)."
                % (passed, len(outcomes), undecided)
            ),
        )
    )
    g.add((result_set, DCTERMS.license, URIRef(settings.result_license)))
    g.add((result_set, FTR.assessmentTarget, target))
    g.add((result_set, PROV.wasGeneratedBy, activity))
    g.add((result_set, PROV.generatedAtTime, xsd_datetime(ended_at)))
    for result in results:
        g.add((result_set, PROV.hadMember, result))
    return g
