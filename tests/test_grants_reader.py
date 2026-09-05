"""Contract test #4: what needs_runtime_reader.sql opens actually exists in ddl/current, and is nothing
but SELECT."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRANTS = ROOT / "db" / "grants" / "needs_runtime_reader.sql"
DUMPS = ROOT / "contracts" / "ddl" / "current"
GRANTED = re.compile(r"\('([a-z_]+\.[a-z_]+)'\)")
CREATED = re.compile(r"CREATE TABLE (\w+\.\w+)")


def _dumped_tables() -> set[str]:
    return {t for path in DUMPS.glob("*.sql") for t in CREATED.findall(path.read_text(encoding="utf-8"))}


def test_every_granted_table_exists_in_the_current_dumps():
    granted = set(GRANTED.findall(GRANTS.read_text(encoding="utf-8")))
    # T1: this test's purpose is to catch a line opening a table that does not exist, like the
    # `tubedepth.videos` issue #2 wrote down.
    assert granted
    assert granted <= _dumped_tables(), sorted(granted - _dumped_tables())


# migrate.sh runs this file as superuser -- if anything other than SELECT ever got mixed in, nothing
# would stop it.
FORBIDDEN = re.compile(
    r"\b(GRANT\s+(INSERT|UPDATE|DELETE|TRUNCATE|REFERENCES|TRIGGER|CREATE|CONNECT|TEMP\w*|EXECUTE|ALL)"
    r"|GRANT\s+\w+\s+TO\b"  # granting role membership
    r"|DEFAULT\s+PRIVILEGES|ALTER|CREATE|DROP|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM"
    r"|TRUNCATE|COPY|SET\s+ROLE|REASSIGN)\b",
    re.IGNORECASE,
)


def test_the_reader_role_gets_select_and_nothing_else():
    # Comments are stripped out: a line explaining why something is not granted must never read as
    # granting it.
    body = re.sub(r"--[^\n]*", "", GRANTS.read_text(encoding="utf-8"))
    hits = [m.group(0) for m in FORBIDDEN.finditer(body)]
    assert not hits, hits


def test_the_guard_catches_a_membership_grant():
    assert FORBIDDEN.search("GRANT needs_owner TO needs_runtime;")
    assert FORBIDDEN.search("ALTER DEFAULT PRIVILEGES IN SCHEMA trend_radar GRANT SELECT ON TABLES TO x;")
    assert not FORBIDDEN.search("GRANT SELECT ON trend_radar.review TO needs_runtime;")
