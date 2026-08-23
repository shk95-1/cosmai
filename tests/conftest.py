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

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_DB_URL_ENV = "TEST_POSTGRES_URL"
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
SNAPSHOT_UPDATE = "--snapshot-update"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(SNAPSHOT_UPDATE, action="store_true", help="Rewrite CLI snapshots instead of comparing.")


@pytest.fixture
def snapshot_update(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption(SNAPSHOT_UPDATE))


_real_connect = socket.socket.connect
_real_psycopg_connect = psycopg.connect


def _allowed_port() -> int | None:
    url = os.environ.get(TEST_DB_URL_ENV)
    return (make_url(url).port or 5432) if url else None


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


@pytest.fixture
def tubedepth_schema(database_url_for_tests: str, _schema_name: str) -> str:
    """#8's tubedepth DDL applied to a throwaway schema: the current 13-table dump verbatim (same
    substitution `trend_radar_schema` uses), then every additive file in contracts/ddl/tubedepth/ on
    top -- op §승인 경계: production `tubedepth` gets none of this, only the test schema does.
    """
    schema = _schema_name
    engine = create_engine(database_url_for_tests)
    lines = [
        ln
        for ln in TUBEDEPTH_DDL.read_text(encoding="utf-8").splitlines()
        if not ln.startswith("\\restrict") and not ln.startswith("\\unrestrict")
    ]
    ddl = "\n".join(lines).replace("CREATE SCHEMA tubedepth;", "").replace("tubedepth.", f'"{schema}".')
    with engine.begin() as conn:
        conn.exec_driver_sql(ddl)
        for path in sorted(TUBEDEPTH_NEEDS_DIR.glob("*.sql")):
            conn.exec_driver_sql(path.read_text(encoding="utf-8").replace("tubedepth.", f'"{schema}".'))
    engine.dispose()
    return database_url_for_tests
