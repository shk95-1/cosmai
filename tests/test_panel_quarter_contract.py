"""Contract test: 패널 모집단과 분기 입자 — 계약 문장과 022 DDL 이 같은 것을 말한다 (포크 #3).

월과 분기가 한 스키마에 공존하는 순간 두 질문이 행마다 따라붙는다: **어느 입자인가**, 그리고
**어느 모집단에 대한 비율인가**. 분모가 다르면 같은 코드가 다른 뜻의 숫자를 오류 없이 낸다. 그래서
정본(어느 표가 어느 입자를 진다) · 명부(패널 역할이 사는 자리) · 분모(행에서 읽히는가)를 문서와
DDL 양쪽에서 맞대어 본다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
DDL_DIR = ROOT / "contracts" / "ddl" / "needs"
DDL = DDL_DIR / "022_panel_and_quarter.sql"
FORMATS = ROOT / "contracts" / "formats.md"
INTERFACES = ROOT / "contracts" / "interfaces.md"
INDEX = ROOT / "contracts" / "README.md"

PANEL = "panel_channel"
QUARTER = "metrics_topic_quarter"
GRAIN_HEADER = "| 그레인 | 정본 표 | 행의 시간 칸 |"
ROLE_HEADER = "| `panel_role` | 뜻 | v1 패널 |"
PANEL_CHANNELS = 43  # ydc 시드 채널 43개 (이슈 #3 본문). 값 적재는 #31.
# 테이블은 needs_owner 소유다 -- migrator 는 SET ROLE 로만 그 자리에 선다 (db/bootstrap.sql).
OWNER = text("SET ROLE needs_owner")


def _ddl() -> str:
    return DDL.read_text(encoding="utf-8")


def _create_block(table: str) -> str:
    found = re.search(rf"CREATE TABLE needs\.{table} \((.*?)\n\);", _ddl(), re.DOTALL)
    assert found, f"022 declares no needs.{table}"
    return found.group(1)


def _columns(table: str) -> dict[str, str]:
    """컬럼 이름 → 선언(타입과 제약)."""
    columns: dict[str, str] = {}
    for line in _create_block(table).splitlines():
        stripped = re.sub(r"\s+--.*", "", line).strip().rstrip(",")
        # 컬럼 이름이 키워드로 시작할 수 있다(`unique_ratio`) -- 제약 줄은 낱말 경계로 가른다.
        if re.match(r"(PRIMARY KEY|UNIQUE|CHECK|FOREIGN)\b", stripped, re.IGNORECASE):
            continue
        named = re.match(r"([a-z_]+)\s+(.+)", stripped)
        if named:
            columns[named.group(1)] = named.group(2)
    return columns


def _primary_key(table: str) -> list[str]:
    found = re.search(r"PRIMARY KEY \(([^)]*)\)", _create_block(table))
    assert found, f"needs.{table} has no PRIMARY KEY"
    return [name.strip() for name in found.group(1).split(",")]


def _markdown_rows(path: Path, header: str) -> list[list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: list[list[str]] = []
    for line in lines[lines.index(header) + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _grain_rows() -> list[tuple[str, str]]:
    """(그레인, 정본 표) — `contracts/formats.md` §시간."""
    return [
        (cells[0], cells[1].strip("`").removeprefix("needs."))
        for cells in _markdown_rows(FORMATS, GRAIN_HEADER)
    ]


def _declared_tables() -> set[str]:
    return {
        table
        for path in DDL_DIR.glob("*.sql")
        for table in re.findall(r"CREATE TABLE needs\.(\w+)", path.read_text(encoding="utf-8"))
    }


def test_the_contract_names_the_owner_of_each_grain():
    """이 태스크에서 가장 쉽게 빠뜨리는 문장이다 -- 같은 개념의 지표가 두 표에 살면, 어느 표가
    어느 입자의 정본인지 계약이 말하지 않는 한 두 표는 조용히 서로의 대체품이 된다."""
    assert _grain_rows() == [("월", "metrics_need"), ("월", "metrics_wish"), ("분기", QUARTER)]


def test_no_table_is_the_owner_of_two_grains():
    tables = [table for _, table in _grain_rows()]
    assert len(tables) == len(set(tables))


def test_every_metrics_table_in_the_ddl_declares_its_grain():
    # 새 집계 표가 그레인을 선언하지 않고 서는 순간 이 질문이 다시 열린다.
    assert {t for t in _declared_tables() if t.startswith("metrics_")} == {t for _, t in _grain_rows()}


def _quarter_formulas() -> set[str]:
    body = INTERFACES.read_text(encoding="utf-8")
    return set(re.findall(rf"^- \*\*(\w+)\*\* \(`{QUARTER}`\)", body, re.MULTILINE))


def test_every_ratio_column_of_the_quarter_table_has_a_formula_in_the_contract():
    """비율·로그값은 정의 없이는 재현되지 않는다. 개수 칸(int)은 세는 대상이 이름에 있지만
    `numeric` 칸은 분자와 분모가 무엇인지 계약이 말해야 한다."""
    ratios = {name for name, declaration in _columns(QUARTER).items() if declaration.startswith("numeric")}
    assert ratios == _quarter_formulas()


def test_the_quarter_row_carries_the_population_it_is_a_ratio_of():
    """행을 보고 분모를 알 수 없으면 이 이슈는 실패다 (이슈 #3 본문)."""
    columns = set(_columns(QUARTER))
    assert {"panel_version", "panel_role", "denom_channels", "documents", "quarter_mentions"} <= columns


def test_the_population_is_part_of_the_key_not_a_footnote():
    # 모집단이 키 밖에 있으면 같은 자리를 두 모집단이 다투고, 나중 것이 앞선 것을 덮는다.
    assert {"panel_version", "panel_role"} <= set(_primary_key(QUARTER))


def _roles_in_ddl(table: str) -> tuple[str, ...]:
    found = re.search(r"panel_role\s+text NOT NULL CHECK \(panel_role IN \(([^)]*)\)\)", _create_block(table))
    assert found, f"needs.{table}.panel_role has no role vocabulary"
    return tuple(value.strip().strip("'") for value in found.group(1).split(","))


@pytest.mark.parametrize("table", [PANEL, QUARTER])
def test_the_roles_the_ddl_accepts_are_the_roles_the_contract_explains(table: str):
    explained = tuple(cells[0].strip("`") for cells in _markdown_rows(FORMATS, ROLE_HEADER))
    assert _roles_in_ddl(table) == explained


def test_the_two_roles_add_up_to_the_seeded_panel():
    """43채널이 분모다. 계약이 그 수를 말하지 않으면 #31 이 무엇을 다 채웠는지 알 자가 없다."""
    counted = [int(cells[2]) for cells in _markdown_rows(FORMATS, ROLE_HEADER)]
    assert sum(counted) == PANEL_CHANNELS
    assert f"{PANEL_CHANNELS}채널" in FORMATS.read_text(encoding="utf-8")


def test_the_ddl_lives_in_this_forks_number_block():
    # upstream 은 006~019, 포크는 020~ (contracts/versioning.md). 021 까지 운영에 적용돼 있다.
    assert DDL.name.startswith("022_")
    assert sorted(p.name for p in DDL_DIR.glob("02*.sql"))[-1] == DDL.name


def test_the_contracts_index_carries_a_row_for_this_ddl():
    rows = [line for line in INDEX.read_text(encoding="utf-8").splitlines() if DDL.name in line]
    assert len(rows) == 1, rows
    checkers = [c.strip("`") for c in re.findall(r"`[^`]+`", rows[0]) if c.strip("`").endswith(".py")]
    assert checkers, rows[0]
    for checker in checkers:
        assert (ROOT / checker).exists(), checker


@pytest.mark.postgres
@pytest.mark.parametrize("table", [PANEL, QUARTER])
def test_the_table_stands_in_the_applied_schema_with_its_declared_columns(
    needs_schema: str, _schema_name: str, table: str
):
    engine = create_engine(needs_schema)
    try:
        applied = {c["name"] for c in inspect(engine).get_columns(table, schema=_schema_name)}
    finally:
        engine.dispose()
    assert set(_columns(table)) == applied


@pytest.mark.postgres
def test_a_role_outside_the_vocabulary_is_refused(needs_schema: str):
    """명부에 없는 채널은 패널 밖이라 분모에 안 들어간다 -- 그러려면 '역할 비슷한 것'이 들어올
    자리가 없어야 한다(사용자 결정 2026-08-26)."""
    engine = create_engine(needs_schema)
    insert = text(
        f"INSERT INTO {PANEL} (channel_id, version, panel_role) VALUES ('UC0', 1, :role)"  # noqa: S608
    )
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)  # 표를 만든 롤. needs_migrator 는 SET ROLE 밖에서 아무 권한이 없다
            conn.execute(insert, {"role": "product"})
        with pytest.raises(Exception, match="panel_role"):
            with engine.begin() as conn:
                conn.execute(OWNER)
                conn.execute(insert, {"role": "beauty"})
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_quarter_column_only_accepts_a_quarter_literal(needs_schema: str):
    """`'2026-07'` 이 들어가면 그 행은 월 행처럼 보이면서 분기 분모를 쓴다 -- 문법이 곧 입자다."""
    engine = create_engine(needs_schema)
    insert = text(
        f"INSERT INTO {QUARTER} (run_id, scope, need_key, quarter, source, content_type, "  # noqa: S608
        "panel_version, panel_role, mentions, documents, quarter_mentions, denom_channels, sample_ok) "
        "VALUES (:run, '선블록', '백탁', :quarter, 'youtube_video', 'long_form', 1, 'product', "
        "1, 10, 5, 34, true)"
    )
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            run = conn.execute(
                text("INSERT INTO analysis_run (versions) VALUES ('{}'::jsonb) RETURNING run_id")
            ).scalar_one()
            conn.execute(insert, {"run": run, "quarter": "2026Q3"})
        with pytest.raises(Exception, match="quarter"):
            with engine.begin() as conn:
                conn.execute(OWNER)
                conn.execute(insert, {"run": run, "quarter": "2026-07"})
    finally:
        engine.dispose()


@pytest.mark.postgres
@pytest.mark.parametrize("table", [PANEL, QUARTER])
@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
def test_the_runtime_role_may_write_the_new_tables(table: str, privilege: str):
    """per-test 스키마는 ALL TABLES 를 통째로 열어 주므로 GRANT 누락이 안 보인다 -- 실제 배포가
    만든 `needs` 스키마에서 본다(`db/migrate.sh`)."""
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(OWNER)  # needs_migrator 는 SET ROLE 밖에서 스키마 needs 에 USAGE 가 없다
            allowed = conn.execute(
                text("select has_table_privilege('needs_runtime', :t, :p)"),
                {"t": f"needs.{table}", "p": privilege},
            ).scalar_one()
    finally:
        engine.dispose()
    assert allowed is True
