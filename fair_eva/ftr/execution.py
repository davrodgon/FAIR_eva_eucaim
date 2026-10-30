"""Execution of FAIR EVA tests (``plugin + indicator``) for the FTR endpoints.

The plugin is loaded and instantiated by the existing :func:`fair_eva.api.rda.load_plugin`
decorator, so plugin discovery, configuration and error handling are exactly those of the
``/rda/*`` endpoints. One instantiation is shared by all the indicators of a request
(as ``rda_all`` does), which matters for plugins that fetch the metadata on creation.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

import fair_eva.api.utils as ut
from fair_eva.api import rda
from fair_eva.ftr.indicators import Indicator
from fair_eva.ftr.vocab import FAIL, INDETERMINATE, PASS

logger = logging.getLogger("api.ftr")

# Logger that parents the plugin loggers ("api.plugin.<name>") and the one of the
# evaluator base class ("api.plugin.evaluation_steps").
EVALUATOR_LOGGER = "api.plugin"


class FtrRequestError(Exception):
    """The test could not be started (bad plugin, missing identifier, ...)."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Message:
    text: str
    points: Optional[float] = None


@dataclass
class Outcome:
    """What a test said about a resource, independent of how it is serialised."""

    plugin: str
    indicator: Indicator
    resource_identifier: str
    started_at: datetime
    ended_at: datetime
    status: str  # pass | fail | indeterminate
    points: float = 0
    messages: List[Message] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    error: Optional[str] = None


def utc_now():
    return datetime.now(timezone.utc)


def normalize_messages(msg):
    """Plugins return ``[{"message": ..., "points": ...}, ...]``; be lenient anyway."""
    if not msg:
        return []
    if not isinstance(msg, (list, tuple)):
        msg = [msg]
    messages = []
    for entry in msg:
        if isinstance(entry, dict):
            points = entry.get("points")
            messages.append(
                Message(
                    str(entry.get("message", "")),
                    points if isinstance(points, (int, float)) else None,
                )
            )
        else:
            messages.append(Message(str(entry)))
    return messages


class _ThreadLogCollector(logging.Handler):
    """Collects the evaluator logs emitted by the current thread."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.thread = threading.get_ident()
        self.lines = []

    def emit(self, record):
        if record.thread == self.thread:
            self.lines.append("[%s] %s" % (record.levelname, record.getMessage()))


def _run_indicator(eva, plugin, indicator, resource_identifier, clock):
    collector = _ThreadLogCollector()
    evaluator_logger = logging.getLogger(EVALUATOR_LOGGER)
    evaluator_logger.addHandler(collector)
    started_at = clock()
    error = None
    points, messages = 0, []
    try:
        points, msg = getattr(eva, indicator.id)()
        messages = normalize_messages(msg)
        status = ut.test_status(points)
    except Exception as e:  # same contract as the /rda/* endpoints: errors score 0
        logger.error("Indicator %s failed: %s" % (indicator.id, e))
        points, status, error = 0, INDETERMINATE, "Exception: %s" % e
    finally:
        evaluator_logger.removeHandler(collector)
    ended_at = clock()
    points = min(max(points, 0), 100)
    return Outcome(
        plugin=plugin,
        indicator=indicator,
        resource_identifier=resource_identifier,
        started_at=started_at,
        ended_at=ended_at,
        status=status if status in (PASS, FAIL, INDETERMINATE) else INDETERMINATE,
        points=points,
        messages=messages,
        log=collector.lines,
        error=error,
    )


@rda.load_plugin
def _run_indicators(body, eva):
    clock = body["_ftr_clock"]
    outcomes = [
        _run_indicator(eva, body["repo"], indicator, body["id"], clock)
        for indicator in body["_ftr_indicators"]
    ]
    return outcomes, 200


def run_tests(
    plugin,
    indicators,
    resource_identifier,
    api_endpoint=None,
    lang="en",
    clock: Callable[[], datetime] = utc_now,
):
    """Run ``indicators`` with ``plugin`` on ``resource_identifier``.

    Returns a list of :class:`Outcome`. Raises :class:`FtrRequestError` when the plugin
    cannot be loaded or instantiated.
    """
    if not resource_identifier or not str(resource_identifier).strip():
        raise FtrRequestError("A resource identifier is required.", 400)
    body = {
        "repo": plugin,
        "id": resource_identifier,
        "api_endpoint": api_endpoint,
        "lang": lang,
        "_ftr_indicators": list(indicators),
        "_ftr_clock": clock,
    }
    result, status = _run_indicators(body)
    if not isinstance(result, dict) or resource_identifier not in result:
        raise FtrRequestError(str(result), status)
    outcomes = result[resource_identifier]
    # The decorator also returns the evaluator logs of the whole request; they are
    # already attached to each outcome, so they are not repeated here.
    return outcomes
