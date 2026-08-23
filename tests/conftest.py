"""Offline by construction + one schema per test on a real Postgres.

From playbook minimum set #2/#3 (snippets conftest_no_network.py, db_schema_per_test.py).
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

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
        raise RuntimeError(
            f"{request.node.nodeid} tried to open a socket. Tests are offline by construction."
        )

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_plain)
    monkeypatch.setattr(socket, "create_connection", refuse_plain)
    yield


TEST_DB_URL_ENV = "TEST_POSTGRES_URL"
PREFIX = "nd"


def _schema_name_for(nodeid: str) -> str:
    """Readable slug + sha1 tail; Postgres identifiers top out at 63 bytes."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", nodeid).strip("_").lower()
    digest = hashlib.sha1(nodeid.encode()).hexdigest()[:10]
    return f"{PREFIX}_{slug[: 63 - len(digest) - len(PREFIX) - 2]}_{digest}"


@pytest.fixture
def database_url_for_tests(request: pytest.FixtureRequest) -> Iterator[str]:
    """The one place the suite names a database. Schema per test, dropped on teardown."""
    server_url = os.environ.get(TEST_DB_URL_ENV)
    if not server_url:
        pytest.skip(f"set {TEST_DB_URL_ENV}, or run tool/checks/test")

    schema = _schema_name_for(request.node.nodeid)
    admin = create_engine(server_url)
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()

    url = make_url(server_url).update_query_dict({"options": f"-csearch_path={schema},pg_catalog"})
    try:
        # Not str(url): SQLAlchemy masks the password as *** there.
        yield url.render_as_string(hide_password=False)
    finally:
        admin = create_engine(server_url)
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
