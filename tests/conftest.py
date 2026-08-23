"""Offline by construction + one schema per test on a real Postgres.

From playbook minimum set #2/#3 (snippets conftest_no_network.py, db_schema_per_test.py).
"""

from __future__ import annotations

import hashlib
import os
import re
import socket
from collections.abc import Iterator
from pathlib import Path

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
TEST_RUNTIME_DB_URL_ENV = "TEST_POSTGRES_RUNTIME_URL"
PREFIX = "nd"


def _schema_name_for(nodeid: str) -> str:
    """Readable slug + sha1 tail; Postgres identifiers top out at 63 bytes."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", nodeid).strip("_").lower()
    digest = hashlib.sha1(nodeid.encode()).hexdigest()[:10]
    return f"{PREFIX}_{slug[: 63 - len(digest) - len(PREFIX) - 2]}_{digest}"


@pytest.fixture
def _schema_name(request: pytest.FixtureRequest) -> str:
    """One source of truth for the per-test schema name, so downstream fixtures don't re-derive it."""
    return _schema_name_for(request.node.nodeid)


@pytest.fixture
def database_url_for_tests(_schema_name: str) -> Iterator[str]:
    """The one place the suite names a database. Schema per test, dropped on teardown."""
    server_url = os.environ.get(TEST_DB_URL_ENV)
    if not server_url:
        pytest.skip(f"set {TEST_DB_URL_ENV}, or run tool/checks/test")

    schema = _schema_name
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


@pytest.fixture
def needs_schema(database_url_for_tests: str, _schema_name: str) -> str:
    """DDL applied as needs_owner (table ownership matches production) then opened to needs_runtime the
    way bootstrap.sql opens `needs`. The schema itself stays migrator-owned, so needs_owner needs an
    explicit CREATE grant to build tables in it, and the schema-level USAGE grant to needs_runtime runs
    while migrator (the actual owner) is still the active role, before SET ROLE hands off to needs_owner."""
    ddl_dir = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "needs"
    schema = _schema_name
    engine = create_engine(database_url_for_tests)
    with engine.begin() as conn:
        conn.exec_driver_sql(f'GRANT USAGE, CREATE ON SCHEMA "{schema}" TO needs_owner')
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO needs_runtime')
        conn.exec_driver_sql("SET ROLE needs_owner")
        for path in sorted(ddl_dir.glob("*.sql")):
            conn.exec_driver_sql(path.read_text(encoding="utf-8").replace("needs.", f'"{schema}".'))
        conn.exec_driver_sql(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO needs_runtime'
        )
        conn.exec_driver_sql(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO needs_runtime')
    engine.dispose()
    return database_url_for_tests


@pytest.fixture
def needs_runtime_url(needs_schema: str, _schema_name: str) -> str:
    """Same schema as needs_schema but as needs_runtime -- the role and timeouts production uses."""
    runtime_url = os.environ.get(TEST_RUNTIME_DB_URL_ENV)
    if not runtime_url:
        pytest.skip(f"set {TEST_RUNTIME_DB_URL_ENV}, or run tool/checks/test")
    url = make_url(runtime_url).update_query_dict({"options": f"-csearch_path={_schema_name},pg_catalog"})
    return url.render_as_string(hide_password=False)


TREND_RADAR_DDL = (
    Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "current" / "app.trend_radar.sql"
)


@pytest.fixture
def trend_radar_schema(database_url_for_tests: str, _schema_name: str) -> str:
    """collectors/commerce's DDL applied straight from contracts/ddl/current/app.trend_radar.sql -- the
    one authority for that schema's shape (#7's completion bar is diff = 0 against this exact file, so
    the fixture must apply it verbatim rather than a hand-written CREATE TABLE).

    No role switch, unlike `needs_schema`: `trend_radar` predates the needs-style owner/runtime split
    (contracts/README.md) and is already live in production without one, so the per-test schema is
    applied and read back as the same role that created it.
    """
    schema = _schema_name
    engine = create_engine(database_url_for_tests)
    lines = [
        ln
        for ln in TREND_RADAR_DDL.read_text(encoding="utf-8").splitlines()
        if not ln.startswith("\\restrict") and not ln.startswith("\\unrestrict")
    ]
    # The per-test schema already exists (database_url_for_tests); the dump's own CREATE SCHEMA would
    # collide with it, and every object in the file is qualified with the schema name it is renaming.
    ddl = "\n".join(lines).replace("CREATE SCHEMA trend_radar;", "").replace("trend_radar.", f'"{schema}".')
    with engine.begin() as conn:
        conn.exec_driver_sql(ddl)
    engine.dispose()
    return database_url_for_tests
