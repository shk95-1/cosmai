"""origin: service/trend-radar/tests/conftest.py + service/yt-scrapper/tests/conftest.py:70-104
reuse: drop into tests/conftest.py; set TEST_DB_URL_ENV to the env var your DB fixture reads.

Offline by construction: a test not marked `live` cannot open a socket, except to the one
local port the test database URL names. The failure message carries the test id.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator

import pytest
from sqlalchemy.engine import make_url  # or urllib.parse.urlsplit if SQLAlchemy is absent

TEST_DB_URL_ENV = "TEST_POSTGRES_URL"
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

_real_connect = socket.socket.connect


def _allowed_port() -> int | None:
    url = os.environ.get(TEST_DB_URL_ENV)
    return (make_url(url).port or 5432) if url else None


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if request.node.get_closest_marker("live"):
        yield
        return
    port = _allowed_port()

    def refuse(self: socket.socket, address: object) -> object:
        host, p = (address[0], address[1]) if isinstance(address, tuple) else (address, None)
        if host in LOCAL_HOSTS and p is not None and p == port:
            return _real_connect(self, address)  # type: ignore[arg-type]
        raise RuntimeError(
            f"{request.node.nodeid} tried to open a socket to {address!r}. Tests are offline by "
            "construction; mark it `live` if it genuinely needs the network, or use a fixture."
        )

    def refuse_plain(*args: object, **kwargs: object) -> object:
        raise RuntimeError(f"{request.node.nodeid} tried to open a socket. Tests are offline by construction.")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_plain)
    monkeypatch.setattr(socket, "create_connection", refuse_plain)
    yield
