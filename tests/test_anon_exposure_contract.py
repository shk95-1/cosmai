"""#168: contracts/anon_exposure.md 가 실제와 어긋나면 실패한다.

이 레포가 오늘 세 번 데인 자리가 전부 "계약이 실제와 다른데 아무도 안 운다"였다(#170 이 일반형).
`postgrest_anon_needs.sql:3` 의 "Whitelist" 주석이 #168 을 만든 것도 같은 종류다. 그래서 계약의
`needs` 절은 **실제 DB 의 `has_table_privilege`** 와 대조한다 -- 파일끼리 맞춰 보면 둘이 함께
틀렸을 때 조용하다.

구 스택 두 스키마는 이 하네스가 재지 못한다: `trend_radar_reader` 도 tubedepth 전체 덤프도
tool/checks/test 의 throwaway Postgres 에 없다. 그 절은 좁히기 SQL 과 대조하고(파일끼리지만
GRANT 문이 정본이다), 운영 실측은 db/grants/postgrest_anon_check.sql 이 맡는다.
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
# GRANT SELECT ON a, b, c TO postgrest_anon -- 목록이 여러 줄에 걸친다.
GRANTED = re.compile(r"GRANT\s+SELECT\s+ON\s+(.*?)\s+TO\s+postgrest_anon", re.DOTALL | re.IGNORECASE)


def _section(heading: str) -> str:
    text_ = CONTRACT.read_text(encoding="utf-8")
    assert f"## {heading}\n" in text_, f"계약에 '## {heading}' 절이 없다"
    return text_.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def _listed(heading: str, schema: str) -> set[str]:
    return {name for s, name in QUALIFIED.findall(_section(heading)) if s == schema}


def _granted_by_narrowing(schema: str) -> set[str]:
    # 주석을 지우고 읽는다: 되돌리기 블록도 파일 이름도 GRANT 로 읽히면 안 된다.
    body = re.sub(r"--[^\n]*", "", NARROWING.read_text(encoding="utf-8"))
    names: set[str] = set()
    for target in GRANTED.findall(body):
        names.update(re.findall(rf"{schema}\.([a-z_]+)", target))
    return names


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


def test_the_needs_section_matches_what_the_database_actually_grants(conn: Connection) -> None:
    actual = _anon_can_read(conn, "needs")
    listed = _listed("needs", "needs")
    assert actual, "postgrest_anon 이 needs 에서 아무것도 못 본다 -- migrate.sh 의 grants 단계가 안 돌았다"
    assert listed == actual, f"계약에만: {sorted(listed - actual)} / DB 에만: {sorted(actual - listed)}"


@pytest.mark.parametrize("schema", ["trend_radar", "tubedepth"])
def test_the_after_sections_match_the_narrowing_sql(schema: str) -> None:
    assert _listed(f"{schema} 적용 후", schema) == _granted_by_narrowing(schema)


@pytest.mark.parametrize("schema", ["trend_radar", "tubedepth"])
def test_the_narrowing_never_regrants_what_the_after_section_calls_removed(schema: str) -> None:
    # 적용 전에는 있고 적용 후에는 없는 이름이 GRANT 줄에 다시 나오면 좁히기가 좁히지 않는다.
    before = {n for n in re.findall(r"`([a-z_]+)`", _section(f"{schema} 적용 전"))}
    removed = before - _listed(f"{schema} 적용 후", schema)
    assert removed, "적용 전 절에서 이름을 하나도 못 읽었다 -- 계약의 모양이 바뀌었다"
    assert not (removed & _granted_by_narrowing(schema)), sorted(removed & _granted_by_narrowing(schema))


def test_the_contract_names_the_two_paths_that_open_the_old_stack() -> None:
    # 계약이 "화이트리스트"만 적고 멤버십·직접 GRANT 를 빠뜨리면 #168 이 다시 생긴다.
    body = CONTRACT.read_text(encoding="utf-8")
    assert "trend_radar_reader" in body
    assert "40-postgrest-tubedepth-grants.sh" in body


def test_the_check_query_stays_read_only() -> None:
    # 읽기 전용 세션에서 도는 것이 이 파일의 안전장치다. 쓰는 문장이 섞이면 그 성질이 조용히 깨진다.
    body = re.sub(r"--[^\n]*", "", CHECK_QUERY.read_text(encoding="utf-8"))
    forbidden = re.compile(
        r"\b(GRANT|REVOKE|ALTER|CREATE|DROP|INSERT|UPDATE|DELETE|TRUNCATE|COPY|SET\s+ROLE|NOTIFY|REASSIGN)\b",
        re.IGNORECASE,
    )
    hits = [m.group(0) for m in forbidden.finditer(body)]
    assert not hits, hits
