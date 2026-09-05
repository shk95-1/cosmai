"""#168: fails when contracts/anon_exposure.md disagrees with reality.

Every one of the three spots this repo has been bitten so far was "the contract says something
different from reality and no one cries" (#170's general shape). The "Whitelist" comment at
`postgrest_anon_needs.sql:3` that created #168 is the same kind of thing. So the contract's `needs`
section is checked against **the real DB's `has_table_privilege`** -- comparing two files against each
other stays quiet when both are wrong the same way.

This harness cannot measure the old stack's two schemas: neither `trend_radar_reader` nor a full
tubedepth dump exists on tool/checks/test's throwaway Postgres. That section is instead checked against
the narrowing SQL (file against file, but the GRANT statement is the source of truth there), and
db/grants/postgrest_anon_check.sql owns the measurement in production.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, text

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "anon_exposure.md"
NARROWING = ROOT / "db" / "grants" / "postgrest_anon_old_stack.sql"
CHECK_QUERY = ROOT / "db" / "grants" / "postgrest_anon_check.sql"

QUALIFIED = re.compile(r"`(needs|trend_radar|tubedepth)\.([a-z_]+)`")
# GRANT SELECT ON a, b, c TO postgrest_anon -- the list spans several lines.
GRANTED = re.compile(r"GRANT\s+SELECT\s+ON\s+(.*?)\s+TO\s+postgrest_anon", re.DOTALL | re.IGNORECASE)


def _section(heading: str) -> str:
    text_ = CONTRACT.read_text(encoding="utf-8")
    assert f"## {heading}\n" in text_, f"계약에 '## {heading}' 절이 없다"
    return text_.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def _listed(heading: str, schema: str) -> set[str]:
    return {name for s, name in QUALIFIED.findall(_section(heading)) if s == schema}


def _granted_by_narrowing(schema: str) -> set[str]:
    # Read with comments stripped out: neither the rollback block nor a file name may ever read as a
    # GRANT.
    body = re.sub(r"--[^\n]*", "", NARROWING.read_text(encoding="utf-8"))
    names: set[str] = set()
    for target in GRANTED.findall(body):
        names.update(re.findall(rf"{schema}\.([a-z_]+)", target))
    return names


def _removed_by_narrowing(heading: str) -> set[str]:
    paragraphs = [p for p in _section(heading).split("\n\n") if "좁히기가 닫은" in p]
    assert len(paragraphs) == 1, f"계약의 {heading} 절에서 좁히기가 닫은 목록을 하나 찾지 못했다"
    return set(re.findall(r"`([a-z_]+)`", paragraphs[0]))


@pytest.fixture
def conn() -> Iterator[Connection]:
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as c:
        yield c
    engine.dispose()  # needs_migrator has CONNECTION LIMIT 2 -- always release, pass or fail.


def _anon_can_read(conn: Connection, schema: str) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relkind IN ('r','v','m','p','f') "
            "AND has_table_privilege('postgrest_anon', c.oid, 'SELECT')"
        ),
        {"s": schema},
    )
    return {r[0] for r in rows}


def test_anon_can_use_the_schema_it_is_granted_tables_in(conn: Connection) -> None:
    """Measuring SELECT alone hides this hole: has_table_privilege returns t regardless of schema
    privilege, but without USAGE PostgREST returns 401 and that schema is the same as zero. This is the
    spot where trend_radar's 9 tables were all returning 401 right after applying on 2026-08-27 -- anon
    had also inherited USAGE through trend_radar_reader membership."""
    usable = conn.execute(
        text("SELECT has_schema_privilege('postgrest_anon', 'needs', 'USAGE')")
    ).scalar_one()
    assert usable, "postgrest_anon 이 needs 에 USAGE 가 없다 -- 표가 몇 개든 API 는 401 이다"


def test_the_narrowing_regrants_schema_usage_where_membership_carried_it() -> None:
    """trend_radar's nspacl reads trend_radar_reader=U with no postgrest_anon entry (measured in
    production 2026-08-27). Revoking the membership takes USAGE along with it, so the narrowing has to
    grant it back. tubedepth needs no such line -- it already has postgrest_anon=U directly in nspacl,
    and granting it again would add a grant that does not exist today."""
    body = re.sub(r"--[^\n]*", "", NARROWING.read_text(encoding="utf-8"))
    granted = {
        m.group(1).lower()
        for m in re.finditer(
            r"GRANT\s+USAGE\s+ON\s+SCHEMA\s+(\w+)\s+TO\s+postgrest_anon", body, re.IGNORECASE
        )
    }
    assert granted == {"trend_radar"}, sorted(granted)


def test_the_needs_section_matches_what_the_database_actually_grants(conn: Connection) -> None:
    actual = _anon_can_read(conn, "needs")
    listed = _listed("needs", "needs")
    assert actual, "postgrest_anon 이 needs 에서 아무것도 못 본다 -- migrate.sh 의 grants 단계가 안 돌았다"
    assert listed == actual, f"계약에만: {sorted(listed - actual)} / DB 에만: {sorted(actual - listed)}"


@pytest.mark.parametrize("schema", ["trend_radar", "tubedepth"])
def test_the_current_sections_match_the_narrowing_sql(schema: str) -> None:
    assert _listed(schema, schema) == _granted_by_narrowing(schema)


@pytest.mark.parametrize("schema", ["trend_radar", "tubedepth"])
def test_the_narrowing_never_regrants_what_the_current_section_calls_removed(schema: str) -> None:
    # If a relation the current section names as closed shows up again in a GRANT line, the narrowing
    # is not narrowing.
    removed = _removed_by_narrowing(schema)
    assert removed, "좁히기가 닫은 관계를 하나도 못 읽었다 -- 계약의 모양이 바뀌었다"
    assert not (removed & _granted_by_narrowing(schema)), sorted(removed & _granted_by_narrowing(schema))


DEFAULT_PRIVILEGES = re.compile(
    r"ALTER\s+DEFAULT\s+PRIVILEGES\s+FOR\s+ROLE\s+(\w+)\s+IN\s+SCHEMA\s+(\w+)\s+"
    r"REVOKE\s+SELECT\s+ON\s+TABLES\s+FROM\s+(\w+)",
    re.IGNORECASE,
)


def test_the_narrowing_only_removes_default_privileges_that_name_anon() -> None:
    """trend_radar's default privilege benefits trend_radar_reader, the role trend-radar-dashboard logs
    into directly (service/stack/docker-compose.yml:172). Erasing it would keep that screen from ever
    reading a table that appears in this schema later -- and cutting the membership alone already stops
    anon's drift, so there is no reason to erase it either. This is a line that was wrongly added once
    before (#168's confirmation round), and this cries out if it comes back."""
    body = re.sub(r"--[^\n]*", "", NARROWING.read_text(encoding="utf-8"))
    targets = {(schema, grantee) for _, schema, grantee in DEFAULT_PRIVILEGES.findall(body)}
    assert ("tubedepth", "postgrest_anon") in targets, (
        "tubedepth 의 기본권한은 anon 에게 직접 걸려 있어 반드시 지워야 한다"
    )
    assert all(g == "postgrest_anon" for _, g in targets), sorted(targets)
    assert not any(s_ == "trend_radar" for s_, _ in targets), (
        "trend_radar 의 기본권한은 dashboard 의 롤 것이다"
    )


def test_the_contract_names_the_two_paths_that_open_the_old_stack() -> None:
    # If the contract only wrote down "whitelist" and left out membership and direct GRANTs, #168
    # happens again.
    body = CONTRACT.read_text(encoding="utf-8")
    assert "trend_radar_reader" in body
    assert "40-postgrest-tubedepth-grants.sh" in body
    # If the asymmetry is not written down, the next person erases trend_radar's default privilege too.
    assert "rolcanlogin=t" in body
    # Counting tables alone and leaving out USAGE broke applying this once already -- the contract has
    # to write down both together.
    assert "USAGE" in body


def test_the_check_query_stays_read_only() -> None:
    # Running inside a read-only session is this file's own safeguard. If a write statement ever got
    # mixed in, that property would quietly break.
    body = re.sub(r"--[^\n]*", "", CHECK_QUERY.read_text(encoding="utf-8"))
    forbidden = re.compile(
        r"\b(GRANT|REVOKE|ALTER|CREATE|DROP|INSERT|UPDATE|DELETE|TRUNCATE|COPY|SET\s+ROLE|NOTIFY|REASSIGN)\b",
        re.IGNORECASE,
    )
    hits = [m.group(0) for m in forbidden.finditer(body)]
    assert not hits, hits


def test_the_check_query_covers_column_grants_and_public() -> None:
    """Counting table privilege alone hides the two doors a column GRANT or PUBLIC could open. Both
    measured 0 after #168 was applied, and a plain read-only check stays green even if the section that
    measures that invariant vanishes entirely, so this holds it separately."""
    body = CHECK_QUERY.read_text(encoding="utf-8")
    assert "information_schema.column_privileges" in body
    assert "NOT has_table_privilege" in body
    assert re.search(r"a\.grantee\s*=\s*0", body)
