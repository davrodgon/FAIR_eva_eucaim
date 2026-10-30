from datetime import datetime, timedelta, timezone

import pytest

from fair_eva.api import rda
from fair_eva.ftr.settings import load_settings
from fair_eva.tests.ftr.support import PLUGIN


@pytest.fixture(autouse=True)
def dummy_plugin_path(monkeypatch):
    monkeypatch.setattr(rda, "PLUGIN_PATH", "fair_eva.tests.plugins")


@pytest.fixture
def settings():
    return load_settings(PLUGIN, rda.PLUGIN_PATH)


@pytest.fixture
def clock():
    """Deterministic clock: every reading is one second after the previous one."""
    state = {"now": datetime(2026, 1, 31, 10, 0, 0, tzinfo=timezone.utc)}

    def tick():
        value = state["now"]
        state["now"] = value + timedelta(seconds=1)
        return value

    return tick
