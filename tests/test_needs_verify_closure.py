"""needs_verify is closed, and db/bootstrap_needs_verify.sql runs early enough to make it so (#258).

The schema holds a re-identification sample -- author_hash back to channel_id -- so what it is worth
is who cannot read it: needs_owner owns it, needs_verify_reader reads it, and the roles that read
`needs` do not.

Both halves are measured against a database db/migrate.sh built in its real step order, because that
order is the defect this file exists to catch. A DDL file creating a table inside needs_verify is
planted for the run -- the shape of the fork's 029, which this checkout does not carry -- so the
table is born in the DDL loop at step (c), which is exactly where a default privilege granted at
(d)/(e) would miss it. A table the test created after the deploy would be granted either way, and
asserting that the schema exists is the weakest test of all: every "cannot read" assertion passes
while the one role that must read cannot.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# serial: this file deploys with db/migrate.sh, which drops every view in `needs` and wants a
# needs_migrator connection nothing else is holding -- neither survives a parallel worker (#216).
pytestmark = [pytest.mark.postgres, pytest.mark.serial]

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATE = REPO_ROOT / "db" / "migrate.sh"
BOOTSTRAP = REPO_ROOT / "db" / "bootstrap_needs_verify.sql"
# This file exists only in a temporary archive of HEAD, never in the checkout. A high number avoids
# colliding with the fork's real 029 when it lands upstream.
PROBE_DDL_RELATIVE = Path("contracts/ddl/needs/999_zz_needs_verify_probe.sql")
PROBE_TABLE = "needs_verify.zz_author_sample_probe"
PROBE_DDL_BODY = f"""-- Planted by tests/test_needs_verify_closure.py in a temporary archive: the
-- shape of the fork's DDL 029, a table inside needs_verify created under SET ROLE needs_owner.
CREATE TABLE {PROBE_TABLE} (author_hash text PRIMARY KEY, channel_id text NOT NULL);
INSERT INTO {PROBE_TABLE} VALUES ('hash', 'UC0');
"""
# Its own database inside the harness container: the ordering is only visible on a build that starts
# empty, and a schema left from an earlier run would grant the default privilege before the run
# under test ever got to it.
PROBE_DATABASE = "needs_verify_probe"
# The roles that read `needs` and must not read this one. needs_runtime is the application's,
# postgrest_anon is the screen's. `needs_runtime_reader` is a file (db/grants/needs_runtime_reader.sql)
# and not a role in this checkout -- its grants go to needs_runtime and needs_owner, so needs_runtime
# is what answers for it here.
CLOSED_TO = ("needs_runtime", "postgrest_anon")


def _archive_head(destination: Path) -> None:
    """Give the deploy a committed tree so its probe cannot outlive an interrupted test."""
    archive = subprocess.Popen(["git", "archive", "HEAD"], cwd=REPO_ROOT, stdout=subprocess.PIPE)
    assert archive.stdout is not None
    extracted = subprocess.run(["tar", "-x", "-C", str(destination)], stdin=archive.stdout, check=False)
    archive.stdout.close()
    assert archive.wait() == 0, "git archive failed"
    assert extracted.returncode == 0, "extracting the committed tree failed"


def _psql(
    container: str, database: str, sql: str, *, role: str | None = None
) -> subprocess.CompletedProcess[str]:
    """One statement as the harness superuser, optionally under SET ROLE.

    SET ROLE rather than a connection: needs_verify_reader is NOLOGIN by design, and asking
    has_table_privilege() instead would measure the catalogue rather than what the server does when
    the role selects -- which is the sentence this file has to be able to write down."""
    prelude = f"SET ROLE {role}; " if role else ""
    return subprocess.run(
        ["docker", "exec", container, "psql", "-U", "fleet", "-d", database]
        + ["-X", "-Atq", "-v", "ON_ERROR_STOP=1", "-c", prelude + sql],
        capture_output=True,
        text=True,
        check=False,
    )


def _value(container: str, database: str, sql: str, *, role: str | None = None) -> str:
    done = _psql(container, database, sql, role=role)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture
def probe_database(harness_container: str) -> Iterator[str]:
    """A database of its own, dropped afterwards: the deploy under test builds it from empty, and
    nothing it records outlives the test."""
    _psql(harness_container, "fleet", f"DROP DATABASE IF EXISTS {PROBE_DATABASE} WITH (FORCE)")
    assert _psql(harness_container, "fleet", f"CREATE DATABASE {PROBE_DATABASE}").returncode == 0
    try:
        yield PROBE_DATABASE
    finally:
        _psql(harness_container, "fleet", f"DROP DATABASE IF EXISTS {PROBE_DATABASE} WITH (FORCE)")


def test_only_the_verify_reader_reads_what_the_ddl_loop_put_in_needs_verify(
    harness_container: str,
    probe_database: str,
    tmp_path: Path,
    deploy: Callable[..., subprocess.CompletedProcess[str]],
):
    _archive_head(tmp_path)
    (tmp_path / PROBE_DDL_RELATIVE).write_text(PROBE_DDL_BODY, encoding="utf-8")
    # Twice: db/migrate.sh is re-run on every deploy, so the new step has to be a no-op the
    # second time -- and the second run is also the one that would fail if CREATE ROLE or
    # CREATE SCHEMA were unguarded.
    for attempt in (1, 2):
        done = deploy(probe_database, cwd=tmp_path)
        assert done.returncode == 0, f"deploy {attempt} failed: {done.stderr}"

    owner = _value(
        harness_container,
        probe_database,
        "SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'needs_verify'",
    )
    assert owner == "needs_owner", "the DDL loop's SET ROLE needs_owner could not have created in it"

    # The positive half, and the only assertion here that sees the ordering: the row exists because
    # ALTER DEFAULT PRIVILEGES ran before the loop created the table.
    read = _psql(
        harness_container, probe_database, f"SELECT count(*) FROM {PROBE_TABLE}", role="needs_verify_reader"
    )
    assert read.returncode == 0, (
        "needs_verify_reader cannot read a table the DDL loop created -- the default privilege was "
        f"granted after step (c) rather than before it: {read.stderr}"
    )
    assert read.stdout.strip() == "1"

    for role in CLOSED_TO:
        denied = _psql(harness_container, probe_database, f"SELECT count(*) FROM {PROBE_TABLE}", role=role)
        assert denied.returncode != 0, f"{role} can read the re-identification sample"
        assert "permission denied" in denied.stderr, denied.stderr

    # PUBLIC is every role there is, including ones no file here names. A grant to it would open the
    # schema to all of them at once, so its absence is asked for rather than assumed.
    public = _value(
        harness_container,
        probe_database,
        "SELECT count(*) FROM pg_namespace n, unnest(n.nspacl) acl "
        "WHERE n.nspname = 'needs_verify' AND acl::text LIKE '=%'",
    )
    assert public == "0", "the schema is granted to PUBLIC"

    # Idempotence with a witness: two runs leave one default-privilege row, whose only beneficiary
    # is the reader role. A second ALTER DEFAULT PRIVILEGES for another role would show up here.
    default = _value(
        harness_container,
        probe_database,
        "SELECT coalesce(array_to_string(defaclacl, ' '), '') FROM pg_default_acl "
        "WHERE defaclnamespace = 'needs_verify'::regnamespace AND defaclobjtype = 'r'",
    )
    assert default == "needs_verify_reader=r/needs_owner", default

    # And nothing of `needs` moved: its own default privileges still name needs_runtime, which is
    # what a deploy that rewrote them would break at the first request after it.
    needs_default = _value(
        harness_container,
        probe_database,
        "SELECT array_to_string(defaclacl, ' ') FROM pg_default_acl "
        "WHERE defaclnamespace = 'needs'::regnamespace AND defaclobjtype = 'r'",
    )
    assert "needs_runtime=arwd/needs_owner" in needs_default, needs_default
    assert "needs_verify_reader" not in needs_default, "the verify reader reached the needs schema"


def test_the_bootstrap_file_is_applied_before_the_ddl_loop_and_before_the_grants():
    """The two independent requirements on the step's position, asked of the script itself.

    The measurement above is the real answer, and it skips where there is no harness container --
    which is every checkout that runs the suite against an external database. This one cannot skip,
    and a reordering is the one edit that makes the whole file worthless."""
    body = MIGRATE.read_text(encoding="utf-8")
    applied = body.index("\nsuperuser_psql < db/bootstrap_needs_verify.sql")
    loop = body.index("\nfor file in contracts/ddl/needs/*.sql")
    grants = body.index("\nsuperuser_psql < db/grants/postgrest_anon_needs.sql")
    assert applied < loop, "the DDL loop would create a table in a schema that does not exist yet"
    assert applied < grants, "a default privilege set with the grants files misses step (c)'s tables"


def test_the_bootstrap_file_gives_nothing_to_the_roles_that_read_needs():
    """The schema is worth what it refuses. A GRANT added here to one of the three roles that read
    `needs` would undo it silently, and no deploy would report anything."""
    body = BOOTSTRAP.read_text(encoding="utf-8")
    for role in (*CLOSED_TO, "needs_runtime_reader"):
        assert role not in body, f"{role} is named in the file that closes the schema against it"
