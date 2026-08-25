"""Contract test: 패널 모집단과 분기 입자 — 계약 문장과 022 DDL 이 같은 것을 말한다 (포크 #3).

월과 분기가 한 스키마에 공존하는 순간 두 질문이 행마다 따라붙는다: **어느 입자인가**, 그리고
**어느 모집단에 대한 비율인가**. 분모가 다르면 같은 코드가 다른 뜻의 숫자를 오류 없이 낸다. 그래서
정본(어느 표가 어느 입자를 진다) · 명부(패널 역할이 사는 자리) · 분모(행에서 읽히는가)를 문서와
DDL 양쪽에서 맞대어 본다.
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
DDL_DIR = ROOT / "contracts" / "ddl" / "needs"
DDL = DDL_DIR / "022_panel_and_quarter.sql"
FORMATS = ROOT / "contracts" / "formats.md"
INTERFACES = ROOT / "contracts" / "interfaces.md"
INDEX = ROOT / "contracts" / "README.md"

ROSTER = "panel_roster"
PANEL = "panel_channel"
QUARTER = "metrics_topic_quarter"
VIEW = ROOT / "db" / "views" / "metrics_topic_quarter_violation.sql"
TOPIC_AXIS = ROOT / "analysis" / "retrieval" / "dict" / "topics_v1.csv"
NEED_REGISTRY = ROOT / "eval" / "lexicon" / "need_key_v1.csv"
PANEL_SEED = ROOT / "eval" / "panel" / "channels_v1.csv"
GRAIN_HEADER = "| 그레인 | 정본 표 | 행의 시간 칸 |"
ROLE_HEADER = "| `panel_role` | 뜻 | v1 패널 |"
PANEL_CSV_HEADER = "| CSV 열 | → `needs.panel_channel` |"
PANEL_CHANNELS = 43  # ydc 시드 채널 43개 (이슈 #3 본문). 값 적재는 tests/test_panel_seed.py (#31).
# 주제 축의 레지스트리. 이 문장이 계약 두 곳에 그대로 있어야 한다 (아래 두 테스트).
REGISTRY = "aspect_lexicon(ruleset='retrieval-topic')"
NOT_THE_REGISTRY = "needs.need_key 가 아니다"
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
@pytest.mark.parametrize("table", [ROSTER, PANEL, QUARTER])
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
            conn.execute(text(f"INSERT INTO {ROSTER} (version) VALUES (1)"))  # noqa: S608
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
        f"INSERT INTO {QUARTER} (run_id, scope, topic_key, quarter, source, content_type, "  # noqa: S608
        "panel_version, panel_role, mentions, documents, quarter_mentions, denom_channels, sample_ok) "
        "VALUES (:run, '선블록', '백탁', :quarter, 'youtube_video', 'long_form', 1, 'product', "
        "5, 10, 5, 34, true)"
    )
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            conn.execute(text(f"INSERT INTO {ROSTER} (version) VALUES (1)"))  # noqa: S608
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
@pytest.mark.parametrize("table", [ROSTER, PANEL, QUARTER])
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


# ---------- 주제 축은 need 축이 아니다 (리뷰 B5) ----------
def _csv_column(path: Path, column: str) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [row[column] for row in csv.DictReader(handle)]


def test_the_topic_axis_and_the_need_key_registry_are_not_the_same_vocabulary():
    """계약이 '어휘가 같다'고 적으면 `USING (need_key)` 조인이 주제 하나를 돌려주고 나머지를 조용히
    떨어뜨린다. 두 축을 여기서 직접 맞대어, 그 문장이 다시 쓰이지 못하게 한다."""
    topics = set(_csv_column(TOPIC_AXIS, "aspect"))  # retrieval-topic 축 (15주제)
    registry = set(_csv_column(NEED_REGISTRY, "need_key"))  # needs.need_key 레지스트리 (38키)
    assert len(topics) > 10 and len(registry) > 30, (len(topics), len(registry))
    assert topics & registry == {"백탁"}, sorted(topics & registry)


def test_the_quarter_table_names_its_topic_column_topic_key():
    """`need_key` 라고 부르면 FK 도 없이 조인 가능한 것처럼 읽힌다 -- 이름이 축을 말해야 한다."""
    columns = _columns(QUARTER)
    assert "topic_key" in columns
    assert "need_key" not in columns
    assert "topic_key" in _primary_key(QUARTER)


@pytest.mark.parametrize("path", [DDL, INTERFACES])
def test_the_contract_names_the_registry_the_topic_key_comes_from(path: Path):
    body = path.read_text(encoding="utf-8")
    assert REGISTRY in body, f"{path.name} 이 주제 축의 레지스트리를 말하지 않는다"
    assert NOT_THE_REGISTRY in body, f"{path.name} 이 need 축과의 구분을 말하지 않는다"


# ---------- 저장 자리수 (리뷰 M1) ----------
def _numeric_scales() -> dict[str, int]:
    scales: dict[str, int] = {}
    for name, declaration in _columns(QUARTER).items():
        found = re.match(r"numeric\((\d+),\s*(\d+)\)", declaration)
        if declaration.startswith("numeric"):
            assert found, f"{name} 은 맨 numeric 이라 저장이 자리수를 지키지 않는다: {declaration}"
            scales[name] = int(found.group(2))
    return scales


def test_the_stored_digits_are_the_digits_the_contract_pins():
    """판정 임계값(ydc `judge.py` 의 `TAU`·`DIFFUSION_TAU`)은 반올림된 값 위에서 맞춰진 수다 --
    자리수가 곧 그 게이트의 해상도라, 계약과 DDL 이 같은 자리수를 들고 있어야 한다."""
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    pinned = next(line for line in lines if line.strip().startswith("자리수:"))
    assert _numeric_scales() == {name: int(digits) for name, digits in re.findall(r"`(\w+)` (\d+)", pinned)}


# ---------- 키 안의 닫힌 어휘 (리뷰 M2·L3) ----------
def _vocabulary_in_ddl(table: str, column: str) -> tuple[str, ...]:
    found = re.search(rf"{column}\s+text NOT NULL CHECK \({column} IN \(([^)]*)\)\)", _create_block(table))
    assert found, f"needs.{table}.{column} 은 키 안인데 어휘가 열려 있다"
    return tuple(value.strip().strip("'") for value in found.group(1).split(","))


@pytest.mark.parametrize("column", ["source", "content_type"])
def test_the_closed_vocabularies_in_the_key_are_the_ones_the_contract_explains(column: str):
    """오타 하나(`youtube_videos`)가 키 안에서 조용히 별도 그룹을 연다 -- `formats.md` 가 불가능하다고
    말하는 바로 그 일이다. 타입 주석과 CHECK 이 같은 어휘여야 한다."""
    comment = re.search(rf"^    {column}: str  # ([^—]+)", INTERFACES.read_text(encoding="utf-8"), re.M)
    assert comment, f"interfaces.md 의 dataclass 가 {column} 의 어휘를 말하지 않는다"
    assert _vocabulary_in_ddl(QUARTER, column) == tuple(v.strip() for v in comment.group(1).split("|"))


def test_the_sample_gate_is_one_number_in_the_ddl_and_the_contract():
    """`sample_ok` 은 NOT NULL 인데 계약 어디에도 정의가 없었다. 표본 게이트는 velocity 의 조건과
    같은 수이므로, 그 수가 두 자리에서 어긋나면 행이 자기 이름과 다른 것을 말한다."""
    found = re.search(r"CHECK \(sample_ok = \(mentions >= (\d+)\)\)", _create_block(QUARTER))
    assert found, "sample_ok 의 정의가 DDL 에 없다"
    assert f"`mentions >= {found.group(1)}`" in INTERFACES.read_text(encoding="utf-8")


# ---------- 모집단 포인터가 가리킬 부모 (리뷰 M3) ----------
def test_the_population_pointer_has_a_parent_it_can_point_at():
    """`panel_channel` 의 PK 는 `(version, channel_id)` 라 `version` 만으로는 FK 를 걸 수 없었다.
    한 줄짜리 부모가 있어야 '옛 행이 무엇을 분모로 삼았는지 남는다'가 강제되는 문장이 된다."""
    assert _primary_key(ROSTER) == ["version"]
    assert "REFERENCES needs.panel_roster" in _columns(PANEL)["version"]
    assert "REFERENCES needs.panel_roster" in _columns(QUARTER)["panel_version"]


# ---------- 패널 CSV 스펙과 실제 파일 (리뷰 M4) ----------
def test_the_panel_csv_spec_lists_the_columns_the_seed_file_actually_has():
    """#31 이 이 파일을 적재한다. 스펙이 6열인데 파일이 11열이면 적재기가 무엇을 버렸는지 알 자가 없다."""
    header = PANEL_SEED.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    spec = [cells[0].strip("`") for cells in _markdown_rows(FORMATS, PANEL_CSV_HEADER)]
    assert spec == header


# ---------- 행 집합의 두 불변식 (리뷰 B2·B4) ----------
# (주제, 분기, mentions, quarter_mentions). trend_use 주제 2개 x 분기 2개의 조밀한 격자 -- 언급 0 셀도
# 행이다. quarter_mentions 는 그 분기 행들의 mentions 합이다.
GRID = [
    ("백탁", "2025Q1", 6, 10), ("발림성", "2025Q1", 4, 10),
    ("백탁", "2025Q2", 0, 5), ("발림성", "2025Q2", 5, 5),
]  # fmt: skip
# trend_use=false 주제가 섞이면 그 분기의 합이 분모를 넘는다 -- 격자는 직사각형 그대로라 개수로는 안 보인다.
OFF_AXIS = [("추천_재구매", "2025Q1", 3, 10), ("추천_재구매", "2025Q2", 4, 5)]

ROW = text(
    f"INSERT INTO {QUARTER} (run_id, scope, topic_key, quarter, source, content_type, "  # noqa: S608
    "panel_version, panel_role, mentions, documents, quarter_mentions, denom_channels, sample_ok) "
    "VALUES (:run, 'all', :topic, :quarter, 'youtube_video', 'long_form', 1, 'product', "
    ":mentions, 20, :qm, 34, :ok)"
)


def _seed(conn: Connection, rows: list[tuple[str, str, int, int]], run: int) -> None:
    for topic, quarter, mentions, quarter_mentions in rows:
        conn.execute(
            ROW,
            {
                "run": run,
                "topic": topic,
                "quarter": quarter,
                "mentions": mentions,
                "qm": quarter_mentions,
                "ok": mentions >= 5,
            },  # fmt: skip
        )


def _violations(conn: Connection) -> set[tuple[str, str | None]]:
    rows = conn.execute(text(f"SELECT violation, quarter FROM {QUARTER}_violation"))  # noqa: S608
    return {(r[0], r[1]) for r in rows}


@pytest.mark.postgres
def test_the_view_catches_a_sparse_grid_and_a_denominator_that_does_not_close(
    needs_schema: str, _schema_name: str
):
    """계약 문장만으로는 #5 가 0 셀을 지우거나 `trend_use` 밖 주제를 섞어도 아무도 못 잡는다.
    두 불변식은 저장된 행 위에서 SQL 로 되물을 수 있는 것이고, 이 뷰가 그 질문이다."""
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            conn.exec_driver_sql(VIEW.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
            conn.execute(text(f"INSERT INTO {ROSTER} (version) VALUES (1)"))  # noqa: S608
            run = conn.execute(
                text("INSERT INTO analysis_run (versions) VALUES ('{}'::jsonb) RETURNING run_id")
            ).scalar_one()

            _seed(conn, GRID, run)
            assert _violations(conn) == set()  # 조밀한 격자 + 닫힌 분모 = 아무 말도 없다

            _seed(conn, OFF_AXIS, run)
            assert _violations(conn) == {
                ("quarter_mentions_not_closed", "2025Q1"),
                ("quarter_mentions_not_closed", "2025Q2"),
            }

            conn.execute(text(f"DELETE FROM {QUARTER} WHERE topic_key = '추천_재구매'"))  # noqa: S608
            conn.execute(  # 언급 0 셀을 지우면 persistence 의 기준선이 함께 올라간다
                text(f"DELETE FROM {QUARTER} WHERE topic_key = '백탁' AND quarter = '2025Q2'")  # noqa: S608
            )
            assert _violations(conn) == {("sparse_grid", None)}
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_roster_version_a_metric_row_points_at_cannot_be_deleted(needs_schema: str):
    """`formats.md` 는 '패널이 바뀐 뒤에도 옛 행이 무엇을 분모로 삼았는지 남는다'고 말한다.
    명부 행은 지워도 되지만 판본은 남아야 그 문장이 참이다 -- needs_runtime 이 DELETE 를 갖고 있다."""
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            conn.execute(text(f"INSERT INTO {ROSTER} (version) VALUES (1)"))  # noqa: S608
            conn.execute(
                text(
                    f"INSERT INTO {PANEL} (channel_id, version, panel_role) "  # noqa: S608
                    "VALUES ('UC0', 1, 'product')"
                )
            )
            run = conn.execute(
                text("INSERT INTO analysis_run (versions) VALUES ('{}'::jsonb) RETURNING run_id")
            ).scalar_one()
            _seed(conn, [("백탁", "2025Q1", 6, 6)], run)
            conn.execute(text(f"DELETE FROM {PANEL} WHERE version = 1"))  # noqa: S608
        with pytest.raises(Exception, match="panel_roster"):
            with engine.begin() as conn:
                conn.execute(OWNER)
                conn.execute(text(f"DELETE FROM {ROSTER} WHERE version = 1"))  # noqa: S608
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_migrate_sh_leaves_the_invariant_view_in_the_needs_schema_for_needs_runtime():
    """뷰가 배포되지 않으면 #5 가 물어볼 자리가 없다 -- 실제 배포가 만든 `needs` 에서 본다."""
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(OWNER)
            allowed = conn.execute(
                text("select has_table_privilege('needs_runtime', :v, 'SELECT')"),
                {"v": f"needs.{QUARTER}_violation"},
            ).scalar_one()
    finally:
        engine.dispose()
    assert allowed is True
