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


def test_anon_can_use_the_schema_it_is_granted_tables_in(conn: Connection) -> None:
    """SELECT 만 재면 이 구멍이 안 보인다: has_table_privilege 는 스키마 권한과 무관하게 t 를 내는데,
    USAGE 가 없으면 PostgREST 는 401 이고 그 스키마는 0개와 같다. 2026-08-27 적용 직후 trend_radar
    9개가 전부 401 이었던 자리다 -- anon 이 USAGE 도 trend_radar_reader 멤버십으로 물려받고 있었다."""
    usable = conn.execute(
        text("SELECT has_schema_privilege('postgrest_anon', 'needs', 'USAGE')")
    ).scalar_one()
    assert usable, "postgrest_anon 이 needs 에 USAGE 가 없다 -- 표가 몇 개든 API 는 401 이다"


def test_the_narrowing_regrants_schema_usage_where_membership_carried_it() -> None:
    """trend_radar 의 nspacl 은 trend_radar_reader=U 이고 postgrest_anon 항목이 없다(운영 실측
    2026-08-27). 멤버십 REVOKE 가 USAGE 를 함께 가져가므로 좁히기가 그것을 다시 줘야 한다.
    tubedepth 는 nspacl 에 postgrest_anon=U 가 직접 있어 필요 없다 -- 주면 오늘 없던 부여가 는다."""
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
def test_the_after_sections_match_the_narrowing_sql(schema: str) -> None:
    assert _listed(f"{schema} 적용 후", schema) == _granted_by_narrowing(schema)


@pytest.mark.parametrize("schema", ["trend_radar", "tubedepth"])
def test_the_narrowing_never_regrants_what_the_after_section_calls_removed(schema: str) -> None:
    # 적용 전에는 있고 적용 후에는 없는 이름이 GRANT 줄에 다시 나오면 좁히기가 좁히지 않는다.
    before = {n for n in re.findall(r"`([a-z_]+)`", _section(f"{schema} 적용 전"))}
    removed = before - _listed(f"{schema} 적용 후", schema)
    assert removed, "적용 전 절에서 이름을 하나도 못 읽었다 -- 계약의 모양이 바뀌었다"
    assert not (removed & _granted_by_narrowing(schema)), sorted(removed & _granted_by_narrowing(schema))


DEFAULT_PRIVILEGES = re.compile(
    r"ALTER\s+DEFAULT\s+PRIVILEGES\s+FOR\s+ROLE\s+(\w+)\s+IN\s+SCHEMA\s+(\w+)\s+"
    r"REVOKE\s+SELECT\s+ON\s+TABLES\s+FROM\s+(\w+)",
    re.IGNORECASE,
)


def test_the_narrowing_only_removes_default_privileges_that_name_anon() -> None:
    """trend_radar 의 기본권한 수혜자는 trend_radar_reader 이고 그 롤로 trend-radar-dashboard 가
    직접 로그인한다(service/stack/docker-compose.yml:172). 지우면 앞으로 이 스키마에 생기는 표를
    그 화면이 못 읽는다 -- 그리고 멤버십 해제만으로 anon 표류는 이미 멈추므로 지울 이유도 없다.
    한 번 잘못 넣었던 줄이라(#168 확정 라운드) 다시 들어오면 여기서 운다."""
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
    # 계약이 "화이트리스트"만 적고 멤버십·직접 GRANT 를 빠뜨리면 #168 이 다시 생긴다.
    body = CONTRACT.read_text(encoding="utf-8")
    assert "trend_radar_reader" in body
    assert "40-postgrest-tubedepth-grants.sh" in body
    # 비대칭을 적지 않으면 다음 사람이 trend_radar 쪽 기본권한도 지운다.
    assert "rolcanlogin=t" in body
    # 표만 세고 USAGE 를 빠뜨린 것이 적용을 한 번 깨뜨렸다 -- 계약이 그 둘을 함께 적어야 한다.
    assert "USAGE" in body


def test_the_check_query_stays_read_only() -> None:
    # 읽기 전용 세션에서 도는 것이 이 파일의 안전장치다. 쓰는 문장이 섞이면 그 성질이 조용히 깨진다.
    body = re.sub(r"--[^\n]*", "", CHECK_QUERY.read_text(encoding="utf-8"))
    forbidden = re.compile(
        r"\b(GRANT|REVOKE|ALTER|CREATE|DROP|INSERT|UPDATE|DELETE|TRUNCATE|COPY|SET\s+ROLE|NOTIFY|REASSIGN)\b",
        re.IGNORECASE,
    )
    hits = [m.group(0) for m in forbidden.finditer(body)]
    assert not hits, hits
