"""The per-schema migration ledger trend_radar and tubedepth did not have (#223).

`db/migrate.sh` step (0) composes a source schema out of the baseline dump plus every
`contracts/ddl/<schema>/NNN_*.sql`, but only when the schema is *absent*. Production has both
schemas, so production always took the skip -- and from #178 until #223 an additive file added after
a schema was built had nothing anywhere that would apply it. Measured on production 2026-09-12:
`contracts/ddl/tubedepth/004_video_snapshot_metadata.sql` and `005_artifact_fetch_route.sql` had
been on main for a week, the deploy printed `tubedepth: present, left alone` on every run, and
neither column existed.

What is asked here is that state, reproduced: a schema that is already there and predates two of its
own additive files. The assertions go to the ledger *and* to the column, because a ledger row
without the column is the failure a ledger of its own introduces.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# serial, like tests/test_empty_db_bootstrap.py: every test here runs db/migrate.sh, which drops
# every view in `needs` and wants a needs_migrator connection nothing else is holding (#216).
pytestmark = [pytest.mark.postgres, pytest.mark.serial]

REPO_ROOT = Path(__file__).resolve().parents[1]
DDL_ROOT = REPO_ROOT / "contracts" / "ddl"
# tubedepth is the schema the hole was measured on and the only source schema with additive files.
SOURCE = "tubedepth"
SOURCE_DIR = DDL_ROOT / SOURCE
ADOPTION = SOURCE_DIR / "applied_before_the_ledger.txt"
FRESH_DATABASE = "source_ledger_fresh"
AGED_DATABASE = "source_ledger_aged"

ADD_COLUMN = re.compile(r"ALTER\s+TABLE\s+(\w+)\.(\w+)\s+ADD\s+COLUMN\s+(\w+)", re.IGNORECASE)


def _psql(container: str, database: str, sql: str) -> list[list[str]]:
    """A copy of tests/test_empty_db_bootstrap.py's helper rather than a shared one: `docker exec`
    spends none of needs_migrator's two connection slots, which is the only property either file
    needs of it (#178 review 4)."""
    done = subprocess.run(
        ["docker", "exec", container, "psql", "-U", "fleet", "-d", database]
        + ["-X", "-Atq", "-F", "|", "-c", sql],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return [line.split("|") for line in done.stdout.splitlines() if line]


def _dump(container: str, database: str, schema: str) -> list[str]:
    """tool/checks/ddl-drift's `normalise`, applied to the same pg_dump flags: comments carry the
    server banner and SET lines carry session defaults, and neither is schema."""
    done = subprocess.run(
        ["docker", "exec", container, "pg_dump", "-U", "fleet", "-d", database]
        + ["--schema-only", "--no-owner", "-x", "-n", schema],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    skip = ("--", "\\restrict", "\\unrestrict", "SET ", "SELECT pg_catalog.set_config")
    return [ln for ln in done.stdout.splitlines() if ln.strip() and not ln.startswith(skip)]


def _additive_files() -> list[Path]:
    return sorted(SOURCE_DIR.glob("*.sql"))


def _adopted() -> list[str]:
    """The adoption list, read the way db/migrate.sh reads it."""
    body = re.sub(r"#.*", "", ADOPTION.read_text(encoding="utf-8"))
    return [line.split()[0] for line in body.splitlines() if line.split()]


def _statements(path: Path) -> list[str]:
    body = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    return [s.strip() for s in body.split(";") if s.strip()]


def _columns_the_deploy_still_owes() -> list[tuple[str, str]]:
    """(table, column) for every additive file the adoption list does not name -- which is exactly
    what production is missing, and what this test undoes to reproduce it.

    A file that is not ADD COLUMN alone cannot be undone this way, and silently undoing less would
    leave the "aged" database already current and measure the catch-up against nothing. It fails
    here instead, naming the file that has to teach this test how (#223)."""
    adopted = _adopted()
    owed: list[tuple[str, str]] = []
    for path in _additive_files():
        if path.stem in adopted:
            continue
        found = ADD_COLUMN.findall("\n".join(_statements(path)))
        assert len(found) == len(_statements(path)), (
            f"{path.name} is not ADD COLUMN alone; teach this test how to undo it"
        )
        owed += [(table, column) for _, table, column in found]
    return owed


@pytest.fixture
def probe_databases(harness_container: str) -> Iterator[None]:
    """Two databases of their own inside the harness container. The roles are cluster-wide and
    already there; the schemas, which are what step (0) asks about, are not."""
    for database in (FRESH_DATABASE, AGED_DATABASE):
        _psql(harness_container, "fleet", f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
        _psql(harness_container, "fleet", f"CREATE DATABASE {database}")
    try:
        yield
    finally:
        for database in (FRESH_DATABASE, AGED_DATABASE):
            _psql(harness_container, "fleet", f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")


#: A `needs` view that reads one of the columns this test takes back out. Since fork #95 there is
#: one -- `needs.live_listing_route` reads `tubedepth.artifacts.fetch_route` -- and a view is not
#: something an aged schema could have had either: the column was not there, so nothing could have
#: been built on it. The deploy's view sweep (step (f)) puts it back on the way out, so a CASCADE
#: here reproduces the aged state rather than losing anything, and the assertion below is what says
#: so out loud instead of leaving it to the flag.
DEPENDENT_VIEWS = ("needs.live_listing_route",)


def _age_it(container: str, database: str) -> None:
    """Turn a freshly built database into production's shape: the schema is there, it carries every
    version the adoption list names, and it carries neither the ledger nor anything later."""
    _psql(container, database, f"DROP TABLE {SOURCE}.schema_migration")
    for table, column in _columns_the_deploy_still_owes():
        _psql(container, database, f"ALTER TABLE {SOURCE}.{table} DROP COLUMN {column} CASCADE")


def test_a_fresh_build_records_every_additive_file_it_applied(
    harness_container: str, probe_databases: None, deploy: Callable[..., subprocess.CompletedProcess[str]]
):
    """Step (0)'s half of the ledger. A schema built from absent is current by construction, so the
    catch-up step has nothing left to do the moment it is built -- if it did, every deploy after a
    build would re-apply something."""
    assert deploy(FRESH_DATABASE).returncode == 0
    recorded = {
        row[0]
        for row in _psql(harness_container, FRESH_DATABASE, f"SELECT version FROM {SOURCE}.schema_migration")
    }
    assert recorded == {path.stem for path in _additive_files()}

    again = deploy(FRESH_DATABASE)
    assert again.returncode == 0, again.stderr
    assert f"{SOURCE}: 0 migration(s) applied, {len(_additive_files())} already present" in again.stdout


def test_a_present_schema_applies_the_files_it_predates_exactly_once(
    harness_container: str, probe_databases: None, deploy: Callable[..., subprocess.CompletedProcess[str]]
):
    """The production case. The ledger is asserted *and* the column is, in that order: a ledger row
    written for a file that did not run is the one failure a ledger adds that no earlier check had."""
    assert deploy(AGED_DATABASE).returncode == 0
    _age_it(harness_container, AGED_DATABASE)
    owed = _columns_the_deploy_still_owes()
    assert owed, "the adoption list names every file, so there is no catch-up left to measure"

    done = deploy(AGED_DATABASE)
    assert done.returncode == 0, done.stderr
    assert f"{SOURCE}: present, left alone" in done.stdout
    assert "created from the baseline dump" not in done.stdout
    assert f"{SOURCE}: {len(owed)} migration(s) applied, {len(_adopted())} already present" in done.stdout

    rows = _psql(
        harness_container, AGED_DATABASE, f"SELECT version FROM {SOURCE}.schema_migration ORDER BY 1"
    )
    assert [row[0] for row in rows] == [path.stem for path in _additive_files()]
    for table, column in owed:
        present = _psql(
            harness_container, AGED_DATABASE, f"SELECT to_regclass('{SOURCE}.{table}') IS NOT NULL"
        )
        assert present == [["t"]]
        columns = _psql(
            harness_container,
            AGED_DATABASE,
            f"SELECT a.attname FROM pg_attribute a WHERE a.attrelid = '{SOURCE}.{table}'::regclass"
            " AND a.attnum > 0 AND NOT a.attisdropped",
        )
        assert [column] in columns, f"{SOURCE}.{table}.{column} has a ledger row and no column"

    # The CASCADE in _age_it took these with the column. A deploy that catches the column up and
    # leaves the view behind is a surface gone quiet, which is what this asks about rather than trusts.
    for view in DEPENDENT_VIEWS:
        assert _psql(harness_container, AGED_DATABASE, f"SELECT to_regclass('{view}') IS NOT NULL") == [
            ["t"]
        ], f"{view} depends on a caught-up column and the deploy did not put it back"

    second = deploy(AGED_DATABASE)
    assert second.returncode == 0, second.stderr
    assert f"{SOURCE}: 0 migration(s) applied, {len(_additive_files())} already present" in second.stdout
    counted = _psql(harness_container, AGED_DATABASE, f"SELECT count(*) FROM {SOURCE}.schema_migration")
    assert counted == [[str(len(_additive_files()))]], "a re-run wrote a second row for a version"


def test_the_caught_up_schema_is_what_a_fresh_build_makes(
    harness_container: str, probe_databases: None, deploy: Callable[..., subprocess.CompletedProcess[str]]
):
    """tool/checks/ddl-drift's comparison, run here where it can be: its expected side is this
    script standing the schema up from absent, and its actual side is production. A caught-up
    database that does not dump identically to a fresh one is drift the moment the deploy lands."""
    assert deploy(FRESH_DATABASE).returncode == 0
    assert deploy(AGED_DATABASE).returncode == 0
    _age_it(harness_container, AGED_DATABASE)
    assert deploy(AGED_DATABASE).returncode == 0

    assert _dump(harness_container, AGED_DATABASE, SOURCE) == _dump(harness_container, FRESH_DATABASE, SOURCE)


def test_the_adoption_list_names_only_versions_that_have_a_file():
    """The list is a one-time record of what production already carried, so it may never name a
    version the directory does not -- a row seeded for an absent file marks nothing applied, and a
    typo would silently skip the file it meant to name."""
    stems = {path.stem for path in _additive_files()}
    assert set(_adopted()) <= stems, f"{ADOPTION.name} names versions no file matches"
    assert _adopted() == sorted(_adopted()), "the list reads in filename order or it reads wrong"


def test_the_drift_check_composes_the_source_schemas_through_the_deploy():
    """#223's own lesson, pinned. `tool/checks/ddl-drift` used to rebuild trend_radar and tubedepth
    with a loop of its own -- a second copy of the composition rule, which is how "expected" came to
    carry a column the deploy had no path to. A loop like that coming back is the divergence
    returning, and this check has no test that can run it (it wants the production database)."""
    body = (REPO_ROOT / "tool" / "checks" / "ddl-drift").read_text(encoding="utf-8")
    code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    assert 'contracts/ddl/"$schema"' not in code, "ddl-drift composes a source schema itself again"
    assert "db/migrate.sh" in code, "nothing in ddl-drift builds the expected state any more"
    # step (0) asks for these three only where the source schemas are absent, which is that
    # throwaway container -- without them migrate.sh stops before it builds either schema.
    for key in ("TREND_RADAR_DB_RUNTIME", "TREND_RADAR_DB_READER", "TUBEDEPTH_DB_RUNTIME"):
        assert key in code, f"ddl-drift's throwaway secret file has no {key}"
