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
from typing import NamedTuple

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
CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(\w+)\.(\w+)", re.IGNORECASE)
# An index or a grant on a table the *same* file creates needs no undo of its own -- dropping that
# table takes it along. One naming a table this file did not make falls through to the assertion in
# `_owed_by`, which is where an unhandled statement has to stop.
ON_A_TABLE = re.compile(r"(?:CREATE\s+INDEX\s+\w+\s+ON|GRANT\s+[\w\s,]+?\s+ON)\s+(\w+)\.(\w+)", re.IGNORECASE)


class Owed(NamedTuple):
    """One object an unadopted file adds, and what has to be there after the catch-up. `column` is
    None for a table the file creates outright -- #280's `tubedepth.collector_runs` is the first of
    those, and until it every additive file here only ever added a column."""

    table: str
    column: str | None


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


def _files_the_deploy_still_owes() -> list[Path]:
    """Every additive file the adoption list does not name -- exactly what production is missing,
    and what the deploy's catch-up has to apply. This is the count the deploy prints."""
    adopted = _adopted()
    return [path for path in _additive_files() if path.stem not in adopted]


def _owed_by(path: Path) -> list[Owed]:
    """What one unadopted file adds, and so what this test takes back out to age the database.

    Every statement has to be accounted for: silently undoing less would leave the "aged" database
    already current and measure the catch-up against nothing. A statement no branch here knows fails
    the run, naming the file that has to teach this test how (#223)."""
    owed: list[Owed] = []
    made_here: set[str] = set()
    for statement in _statements(path):
        if made := CREATE_TABLE.match(statement):
            made_here.add(made.group(2))
            owed.append(Owed(table=made.group(2), column=None))
            continue
        if added := ADD_COLUMN.match(statement):
            owed.append(Owed(table=added.group(2), column=added.group(3)))
            continue
        alongside = ON_A_TABLE.match(statement)
        assert alongside and alongside.group(2) in made_here, (
            f"{path.name} carries a statement this test cannot undo; teach it how: {statement[:60]!r}"
        )
    return owed


def _objects_the_deploy_still_owes() -> list[Owed]:
    return [owed for path in _files_the_deploy_still_owes() for owed in _owed_by(path)]


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


#: The `needs` views that read something this test takes back out. Since fork #95 there is
#: `needs.live_listing_route`, which reads `tubedepth.artifacts.fetch_route`; #280 adds
#: `needs.collector_health`, which reads the whole of `tubedepth.collector_runs`, and
#: `needs.pipeline_health`, which the CASCADE then takes along because it reads that view. None of
#: them is something an aged schema could have had either: the object was not there, so nothing
#: could have been built on it. The deploy's view sweep (step (f)) puts them back on the way out, so
#: a CASCADE here reproduces the aged state rather than losing anything, and the assertion below is
#: what says so out loud instead of leaving it to the flag.
DEPENDENT_VIEWS = (
    "needs.live_listing_route",
    "needs.collector_health",
    "needs.pipeline_health",
)


def _age_it(container: str, database: str) -> None:
    """Turn a freshly built database into production's shape: the schema is there, it carries every
    version the adoption list names, and it carries neither the ledger nor anything later."""
    _psql(container, database, f"DROP TABLE {SOURCE}.schema_migration")
    # Newest first, the order the files were applied in run backwards: a later file may rest on an
    # earlier one's object, and nothing here may rest on one it has already taken away.
    for owed in reversed(_objects_the_deploy_still_owes()):
        if owed.column is None:
            _psql(container, database, f"DROP TABLE {SOURCE}.{owed.table} CASCADE")
        else:
            _psql(container, database, f"ALTER TABLE {SOURCE}.{owed.table} DROP COLUMN {owed.column} CASCADE")


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
    owed = _objects_the_deploy_still_owes()
    files = _files_the_deploy_still_owes()
    assert owed, "the adoption list names every file, so there is no catch-up left to measure"

    done = deploy(AGED_DATABASE)
    assert done.returncode == 0, done.stderr
    assert f"{SOURCE}: present, left alone" in done.stdout
    assert "created from the baseline dump" not in done.stdout
    # Files, not objects: the deploy counts what it applied, and a file may carry more than one
    # object (#280's table, its index and its grant are one migration).
    assert f"{SOURCE}: {len(files)} migration(s) applied, {len(_adopted())} already present" in done.stdout

    rows = _psql(
        harness_container, AGED_DATABASE, f"SELECT version FROM {SOURCE}.schema_migration ORDER BY 1"
    )
    assert [row[0] for row in rows] == [path.stem for path in _additive_files()]
    for owed_object in owed:
        present = _psql(
            harness_container,
            AGED_DATABASE,
            f"SELECT to_regclass('{SOURCE}.{owed_object.table}') IS NOT NULL",
        )
        assert present == [["t"]], f"{SOURCE}.{owed_object.table} has a ledger row and no table"
        if owed_object.column is None:
            continue
        columns = _psql(
            harness_container,
            AGED_DATABASE,
            f"SELECT a.attname FROM pg_attribute a WHERE a.attrelid = '{SOURCE}.{owed_object.table}'"
            "::regclass AND a.attnum > 0 AND NOT a.attisdropped",
        )
        assert [owed_object.column] in columns, (
            f"{SOURCE}.{owed_object.table}.{owed_object.column} has a ledger row and no column"
        )

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
