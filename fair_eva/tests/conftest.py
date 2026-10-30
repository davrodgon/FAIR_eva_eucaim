import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests must be hermetic: any attempt to open a connection fails."""

    def guard(*args, **kwargs):
        raise RuntimeError("Network access is disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", guard)
