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
from typing import Any
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_DB_URL_ENV = "TEST_POSTGRES_URL"
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
SNAPSHOT_UPDATE = "--snapshot-update"
# Set by tool/checks/test when it runs the whole suite, and by nothing else (#215).
FULL_SUITE_ENV = "COSMAI_FULL_SUITE"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(SNAPSHOT_UPDATE, action="store_true", help="Rewrite CLI snapshots instead of comparing.")


@pytest.fixture(scope="session", autouse=True)
def _default_registrations_survive_the_suite() -> Iterator[None]:
    """analysis.registry is a process-global dictionary: a test that goes through register() and
    unregister() without putting the defaults back leaves "no implementation" (exit 2) waiting for
    whatever file runs next, not for its own. That happened twice (#30), so a third one is caught
    at the end of the session.

    Armed only while the whole suite is running -- COSMAI_FULL_SUITE=1, which tool/checks/test sets.
    A focused run never registers the defaults at all, so outside the full suite this guard would end
    every `uv run pytest <file>` with a teardown ERROR about a run that did nothing wrong (#215).
    """
    if os.environ.get(FULL_SUITE_ENV) != "1":
        yield
        return
    from analysis import registry

    yield
    # Measuring comes first: load_implementations() really does register again (#99), so calling it
    # before the count would measure the repair rather than what the suite left behind.
    missing = [task for task in registry.TASKS if registry.get(task) is None]
    registry.load_implementations()
    assert not missing, f"default registrations not restored by suite end: {missing}"


@pytest.fixture
def snapshot_update(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption(SNAPSHOT_UPDATE))


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_create_connection = socket.create_connection
_real_psycopg_connect = psycopg.connect


def _allowed_port() -> int | None:
    url = os.environ.get(TEST_DB_URL_ENV)
    return (make_url(url).port or 5432) if url else None


def _socket_ports(request: pytest.FixtureRequest) -> frozenset[int]:
    """The throwaway Postgres, plus -- for `local_llm` only -- the local ollama the #6 plumbing test
    round-trips against. ollama is another process on this host, not the network the guard is about,
    and it is the only way to exercise the LLM code path without spending money at Anthropic."""
    ports = {port} if (port := _allowed_port()) is not None else set()
    if request.node.get_closest_marker("local_llm"):
        from analysis.polarity.ollama import ollama_url

        ports.add(urlparse(ollama_url()).port or 11434)
    return frozenset(ports)


def _psycopg_dsn_target(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str | None, int | None]:
    """The host/port one `psycopg.connect(...)` call is aimed at, from either calling convention this
    repo's code uses -- kwargs (db/seed/_common.py, storage/db.py's runtime_url via SQLAlchemy: every
    caller we found builds host=/port=/... kwargs, never a bare DSN string) or a libpq conninfo string
    (psycopg's own retry loop inside the real connect() builds one of these, and a caller elsewhere
    could pass one directly), parsed the same way psycopg itself parses it."""
    if "host" in kwargs or "port" in kwargs or "hostaddr" in kwargs:
        host = kwargs.get("hostaddr") or kwargs.get("host")
        port = kwargs.get("port")
        return host, (int(port) if port is not None else None)
    conninfo = kwargs.get("conninfo")
    if conninfo is None and args and isinstance(args[0], str):
        conninfo = args[0]
    if isinstance(conninfo, str) and conninfo:
        parsed = conninfo_to_dict(conninfo)
        raw_host = parsed.get("hostaddr") or parsed.get("host")
        raw_port = parsed.get("port")
        host = str(raw_host) if raw_host is not None else None
        return host, (int(raw_port) if raw_port is not None else None)
    return None, None


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if request.node.get_closest_marker("live"):
        yield
        return
    port = _allowed_port()
    open_ports = _socket_ports(request)

    def _open(address: object) -> bool:
        host, p = (address[0], address[1]) if isinstance(address, tuple) else (address, None)
        return host in LOCAL_HOSTS and p is not None and p in open_ports

    def _refuse(address: object) -> RuntimeError:
        return RuntimeError(
            f"{request.node.nodeid} tried to open a socket to {address!r}. Tests are offline by "
            "construction; mark it `live` if it genuinely needs the network, or use a fixture."
        )

    def refuse(self: socket.socket, address: object) -> object:
        if _open(address):
            return _real_connect(self, address)  # type: ignore[arg-type]
        raise _refuse(address)

    def refuse_connect_ex(self: socket.socket, address: object) -> object:
        if _open(address):
            return _real_connect_ex(self, address)  # type: ignore[arg-type]
        raise _refuse(address)

    # http.client (and so urllib, and so the ollama plumbing) opens its socket here, not via
    # socket.socket.connect -- an all-refusing stub would make an allowed port unreachable anyway.
    def refuse_create_connection(address: object, *args: object, **kwargs: object) -> object:
        if _open(address):
            return _real_create_connection(address, *args, **kwargs)  # type: ignore[arg-type]
        raise _refuse(address)

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse_connect_ex)
    monkeypatch.setattr(socket, "create_connection", refuse_create_connection)

    # libpq opens its socket in C, below `socket.socket.connect` entirely -- the guard above never
    # sees a psycopg connection at all (coordinator-confirmed, issue #8 수정 라운드 1 F-1: an
    # unguarded psycopg call reached real PostgreSQL and failed only at password auth, proving the
    # socket patch was bypassed, not that it worked). `_allowed_port`/`LOCAL_HOSTS` above still guard
    # every pure-Python socket path; this is the psycopg-specific half of the same rule.
    def refuse_psycopg(*args: object, **kwargs: object) -> object:
        host, p = _psycopg_dsn_target(args, kwargs)  # type: ignore[arg-type]
        if host in LOCAL_HOSTS and port is not None and p == port:
            return _real_psycopg_connect(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError(
            f"{request.node.nodeid} tried to open a psycopg connection to {host!r}:{p!r}. Tests are "
            "offline by construction; mark it `live` if it genuinely needs the network, or use a fixture."
        )

    monkeypatch.setattr(psycopg, "connect", refuse_psycopg)
    monkeypatch.setattr(psycopg.Connection, "connect", refuse_psycopg)
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
def runtime_url_for_tests() -> str:
    """The same server as `database_url_for_tests`, connected as needs_runtime and with no schema of
    its own -- for tests about database-wide state (advisory locks) rather than about rows.

    A role, not a convenience: db/bootstrap.sql gives the migrator `CONNECTION LIMIT 2`, so a test
    that has to stand a second process next to the one under test runs out of connections before it
    runs out of assertions. It is also the role production collectors actually run as, timeouts and
    all.
    """
    url = os.environ.get(TEST_RUNTIME_DB_URL_ENV)
    if not url:
        pytest.skip(f"set {TEST_RUNTIME_DB_URL_ENV}, or run tool/checks/test")
    return url


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


TUBEDEPTH_DDL = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "current" / "app.tubedepth.sql"
TUBEDEPTH_NEEDS_DIR = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "tubedepth"


def _apply_tubedepth_ddl(conn: Any, schema: str) -> None:
    """The current 13-table dump verbatim (same substitution `trend_radar_schema` uses), then every
    additive file in contracts/ddl/tubedepth/ on top."""
    lines = [
        ln
        for ln in TUBEDEPTH_DDL.read_text(encoding="utf-8").splitlines()
        if not ln.startswith("\\restrict") and not ln.startswith("\\unrestrict")
    ]
    ddl = "\n".join(lines).replace("CREATE SCHEMA tubedepth;", "").replace("tubedepth.", f'"{schema}".')
    conn.exec_driver_sql(ddl)
    for path in sorted(TUBEDEPTH_NEEDS_DIR.glob("*.sql")):
        conn.exec_driver_sql(path.read_text(encoding="utf-8").replace("tubedepth.", f'"{schema}".'))


@pytest.fixture
def tubedepth_schema(database_url_for_tests: str, _schema_name: str) -> str:
    """#8's tubedepth DDL applied to a throwaway schema -- op approval boundary: production `tubedepth` gets
    none of this, only the test schema does.
    """
    engine = create_engine(database_url_for_tests)
    with engine.begin() as conn:
        _apply_tubedepth_ddl(conn, _schema_name)
    engine.dispose()
    return database_url_for_tests


@pytest.fixture
def tubedepth_side_schema(database_url_for_tests: str, _schema_name: str) -> Iterator[str]:
    """tubedepth in a schema beside the test's main one, for a test that needs trend_radar and
    tubedepth at once (needs.collector_health binds both since #77). They cannot share one schema the
    way needs+trend_radar do: both dumps carry an `alembic_version` table. The name swaps the prefix
    instead of appending, because Postgres truncates identifiers at 63 bytes and `_schema_name`
    already sits on that limit -- an appended suffix would truncate back into the main schema's name.
    """
    schema = f"td_{_schema_name[len(PREFIX) + 1 :]}"
    engine = create_engine(database_url_for_tests)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            _apply_tubedepth_ddl(conn, schema)
        yield schema
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()
