"""db/migrate.sh on an empty database: step (0) makes trend_radar and tubedepth, and never touches
them again (#178).

The two schemas came from the init scripts of two repos that are archived and gone, so until this
step existed there was no path from an empty Postgres to the shape production runs on. What is asked
here is what that step has to get right:

1. an empty database gets both schemas, their roles and their grants, from the baseline dump plus
   every `contracts/ddl/<schema>/NNN_*.sql`;
2. a database that already has them -- production, always -- is left exactly as it was;
3. a build that fails takes its own schema with it, so the next run starts over rather than finding
   something half-made and calling it done;
4. a probe that cannot answer stops the run before anything is written;
5. what the deploy builds and what `tests/conftest.py` builds for a test are the same objects. That
   is why the harness stopped standing these schemas up its own way: two paths to one shape drift,
   and the drift shows up as a test that is green about something production does not have.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
DUMPS = REPO_ROOT / "contracts" / "ddl" / "current"
PROBE = "zz_step_zero_probe"
PROBE_DATABASE = "step_zero_probe"
# A file that sorts after the real additive ones and cannot apply: the failure case, planted for one
# test and removed again.
BROKEN_DDL = REPO_ROOT / "contracts" / "ddl" / "tubedepth" / "999_zz_broken_probe.sql"

# The roles db/bootstrap_source.sql makes, and whether each one logs in. trend_radar has the third:
# trend_radar_reader, which trend-radar-dashboard logs in with (contracts/anon_exposure.md).
EXPECTED_ROLES = {
    "trend_radar": {"trend_radar_owner": False, "trend_radar_runtime": True, "trend_radar_reader": True},
    "tubedepth": {"tubedepth_owner": False, "tubedepth_runtime": True},
}
# collectors/commerce/storage/db.py sizes its pool against this number, read out of production.
TREND_RADAR_RUNTIME_CONNECTION_LIMIT = 8

# pg_catalog rather than information_schema: the latter shows only what the current role may see, so
# a grant difference would silently shrink the comparison instead of failing it. Views and
# materialised views are in, not tables alone -- trend_radar's dump carries a view, and a comparison
# blind to it would call two different schemas equal.
RELATIONS = """
    SELECT c.relname, c.relkind, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_attribute a ON a.attrelid = c.oid
     WHERE n.nspname = '{schema}' AND c.relkind IN ('r', 'p', 'v', 'm')
       AND a.attnum > 0 AND NOT a.attisdropped
     ORDER BY 1, 3
"""
TABLES = """
    SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = '{schema}' AND c.relkind IN ('r', 'p')
"""


def _psql(container: str, database: str, sql: str) -> list[list[str]]:
    done = subprocess.run(
        ["docker", "exec", container, "psql", "-U", "fleet", "-d", database]
        + ["-X", "-Atq", "-F", "|", "-c", sql],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return [line.split("|") for line in done.stdout.splitlines() if line]


def _declared_tables(schema: str) -> set[str]:
    """Every table the contract composes for one schema: the baseline dump plus any additive file."""
    sources = [DUMPS / f"app.{schema}.sql", *sorted((REPO_ROOT / "contracts" / "ddl" / schema).glob("*.sql"))]
    found: set[str] = set()
    for path in sources:
        if path.exists():
            body = path.read_text(encoding="utf-8")
            found |= set(re.findall(rf"^CREATE TABLE {schema}\.(\w+) \(", body, re.M))
    return found


@pytest.fixture
def empty_database(harness_container: str) -> Iterator[str]:
    """A database of its own inside the harness container -- the closest thing to a fresh cluster a
    test can have without paying for a second container. The roles are cluster-wide and already
    exist; the schemas, which are what step (0) asks about, do not."""
    _psql(harness_container, "fleet", f"DROP DATABASE IF EXISTS {PROBE_DATABASE} WITH (FORCE)")
    _psql(harness_container, "fleet", f"CREATE DATABASE {PROBE_DATABASE}")
    try:
        yield PROBE_DATABASE
    finally:
        _psql(harness_container, "fleet", f"DROP DATABASE IF EXISTS {PROBE_DATABASE} WITH (FORCE)")


def test_an_empty_database_gets_both_source_schemas_from_the_contract(
    harness_container: str, empty_database: str, deploy: Callable[..., subprocess.CompletedProcess[str]]
):
    done = deploy(empty_database)
    assert done.returncode == 0, done.stderr
    for schema in ("trend_radar", "tubedepth"):
        assert f"{schema}: created from the baseline dump" in done.stdout

    for schema in ("trend_radar", "tubedepth"):
        tables = {row[0] for row in _psql(harness_container, empty_database, TABLES.format(schema=schema))}
        assert tables == _declared_tables(schema), f"{schema} is not the contract's composition"
    # The additive layer really ran: 003_jobs_dataset.sql is the only source of this column.
    columns = {
        (r[0], r[2]) for r in _psql(harness_container, empty_database, RELATIONS.format(schema="tubedepth"))
    }
    assert ("jobs", "dataset") in columns

    # The dump is --schema-only, so alembic_version arrives empty and nothing here writes to it: the
    # old repos' alembic does not run again, and from now on these schemas change by numbered file.
    for schema in ("trend_radar", "tubedepth"):
        rows = _psql(harness_container, empty_database, f"SELECT count(*) FROM {schema}.alembic_version")
        assert rows == [["0"]], f"{schema}.alembic_version was written to"


def test_the_roles_and_the_reader_grant_come_up_with_them(
    harness_container: str, empty_database: str, deploy: Callable[..., subprocess.CompletedProcess[str]]
):
    assert deploy(empty_database).returncode == 0

    login = dict(_psql(harness_container, empty_database, "SELECT rolname, rolcanlogin FROM pg_roles"))  # type: ignore[arg-type]
    for schema, roles in EXPECTED_ROLES.items():
        for role, can_login in roles.items():
            assert role in login, f"{schema}'s {role} was not created"
            assert (login[role] == "t") is can_login, f"{role} has the wrong login flag"

    limit = _psql(
        harness_container,
        empty_database,
        "SELECT rolconnlimit FROM pg_roles WHERE rolname = 'trend_radar_runtime'",
    )
    assert limit == [[str(TREND_RADAR_RUNTIME_CONNECTION_LIMIT)]]

    # Production's shape, measured 2026-08-27: the schema's USAGE and its DEFAULT PRIVILEGES both
    # hang on trend_radar_reader, and postgrest_anon is a member of nothing (contracts/anon_exposure.md).
    acl = _psql(
        harness_container,
        empty_database,
        "SELECT array_to_string(nspacl, ' ') FROM pg_namespace WHERE nspname = 'trend_radar'",
    )
    assert "trend_radar_reader=U/trend_radar_owner" in acl[0][0]
    default = _psql(
        harness_container,
        empty_database,
        "SELECT array_to_string(defaclacl, ' ') FROM pg_default_acl "
        "WHERE defaclnamespace = 'trend_radar'::regnamespace AND defaclobjtype = 'r'",
    )
    assert "trend_radar_reader=r/trend_radar_owner" in default[0][0]
    assert "postgrest_anon" not in default[0][0], "anon must not inherit this schema's future tables"


def test_a_build_that_fails_leaves_no_schema_behind(
    harness_container: str, empty_database: str, deploy: Callable[..., subprocess.CompletedProcess[str]]
):
    """The recovery path, and the reason the presence probe asks about the baseline table rather
    than the namespace: CREATE SCHEMA autocommits in the roles step while only the objects are in a
    transaction, so a schema left standing after a failed build would be read as "already there" by
    every later run -- and on production the only way out of that is a hand-approved DROP SCHEMA."""
    BROKEN_DDL.write_text(
        "-- Planted by tests/test_empty_db_bootstrap.py and removed in the same test.\n"
        "ALTER TABLE tubedepth.jobs ADD COLUMN zz_broken_probe no_such_type_exists;\n",
        encoding="utf-8",
    )
    try:
        failed = deploy(empty_database)
        assert failed.returncode != 0, "a broken additive file must fail the deploy"
        assert "tubedepth" in failed.stderr
        left = _psql(
            harness_container, empty_database, "SELECT count(*) FROM pg_namespace WHERE nspname = 'tubedepth'"
        )
        assert left == [["0"]], "the failed build left a schema the next run would skip forever"
    finally:
        BROKEN_DDL.unlink(missing_ok=True)

    done = deploy(empty_database)
    assert done.returncode == 0, done.stderr
    assert "tubedepth: created from the baseline dump + 3 additive file(s)" in done.stdout


def test_a_probe_that_cannot_answer_stops_before_anything_is_written(
    harness_container: str, deploy: Callable[..., subprocess.CompletedProcess[str]]
):
    """A psql that cannot connect prints nothing, and "nothing" read as `absent` would run the
    unguarded half of db/bootstrap_source.sql -- REVOKE, GRANT, ALTER DEFAULT PRIVILEGES, ALTER ROLE
    -- against whatever database it did reach. The exit status has to decide."""
    absent = "no_such_database_for_the_probe"
    done = deploy(absent)
    assert done.returncode != 0, "a probe that cannot answer must stop the run"
    assert "what state the schema is in" in done.stderr
    assert "created from the baseline dump" not in done.stdout
    assert "present, left alone" not in done.stdout
    rows = _psql(harness_container, "fleet", f"SELECT count(*) FROM pg_database WHERE datname = '{absent}'")
    assert rows == [["0"]], "nothing may have been created under a name the probe could not reach"


def test_a_schema_that_is_already_there_is_left_alone(
    harness_container: str, deploy: Callable[..., subprocess.CompletedProcess[str]]
):
    """The production path. A table nothing in this repo knows about is planted in the live
    trend_radar and must still be there afterwards -- step (0) either skips the whole schema or it
    does not, and a rebuild would take the planted table with it."""
    _psql(harness_container, "fleet", f"CREATE TABLE IF NOT EXISTS trend_radar.{PROBE} (one int)")
    try:
        done = deploy()
        assert done.returncode == 0, done.stderr
        assert "trend_radar: present, left alone" in done.stdout
        assert "tubedepth: present, left alone" in done.stdout
        assert "created from the baseline dump" not in done.stdout
        rows = _psql(harness_container, "fleet", f"SELECT to_regclass('trend_radar.{PROBE}') IS NOT NULL")
        assert rows == [["t"]], "step (0) rebuilt a schema that was already there"
    finally:
        _psql(harness_container, "fleet", f"DROP TABLE IF EXISTS trend_radar.{PROBE}")


def test_a_deploy_will_not_race_a_connection_something_else_is_holding(
    monkeypatch: pytest.MonkeyPatch, deploy: Callable[..., subprocess.CompletedProcess[str]]
):
    """The mechanism behind "too many connections cannot come back". needs_migrator has two
    connection slots for the whole cluster and db/migrate.sh needs one; a pooled engine still open
    anywhere in the session takes it, and what fails then is not the deploy but every test after it,
    for a reason none of them can name (#178 review 4). The shared fixture waits for both slots
    instead, and when they do not come free it says who is holding them."""
    monkeypatch.setenv("COSMAI_DEPLOY_SLOT_TIMEOUT_S", "1")
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            with pytest.raises(AssertionError, match="still held"):
                deploy()
    finally:
        engine.dispose()


@pytest.mark.parametrize("schema", ["trend_radar", "tubedepth"])
def test_the_deploy_and_the_test_fixture_build_the_same_schema(
    request: pytest.FixtureRequest, schema: str, _schema_name: str
):
    """conftest.py cannot call db/migrate.sh -- its schemas are renamed and one per test -- so what is
    held here is the thing that matters: both compose the same baseline and the same additive files,
    so the objects come out the same. This is what the harness's hand-built tubedepth failed at.
    """
    url = request.getfixturevalue(f"{schema}_schema")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            fixture = conn.execute(text(RELATIONS.format(schema=_schema_name))).fetchall()
            deployed = conn.execute(text(RELATIONS.format(schema=schema))).fetchall()
    finally:
        engine.dispose()
    assert deployed, f"the harness container has no {schema} schema"
    assert fixture == deployed
