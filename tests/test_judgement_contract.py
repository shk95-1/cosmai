"""Contract test: 판정 표는 집계가 아니라 파생이다 — 024 와 계약 문장이 같은 것을 말한다 (포크 #40).

이 이슈에서 가장 조용히 틀릴 수 있는 자리가 **산출을 어디에 두나**였다. 이름을 `metrics_*` 로 두면
`formats.md` §시간 의 "집계 그레인의 정본" 표에 줄을 더해야 하고, 그러면 분기 그레인의 정본이 둘이
된다. 판정 표가 거기 없어도 되는 이유는 취향이 아니라 성질이다 — 세는 칸이 하나도 없고, 지표 행의
기본키 여덟 칸이 그대로 이 표의 키이자 FK 다. 이 파일이 그 성질을 기계로 붙든다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DDL_DIR = ROOT / "contracts" / "ddl" / "needs"
DDL = DDL_DIR / "024_topic_quarter_judgement.sql"
QUARTER_DDL = DDL_DIR / "022_panel_and_quarter.sql"
FORMATS = ROOT / "contracts" / "formats.md"
INTERFACES = ROOT / "contracts" / "interfaces.md"
INDEX = ROOT / "contracts" / "README.md"
VIEW = ROOT / "db" / "views" / "topic_quarter_judgement_violation.sql"

TABLE = "topic_quarter_judgement"
QUARTER = "metrics_topic_quarter"
GRAIN_HEADER = "| 그레인 | 정본 표 | 행의 시간 칸 |"
# 세는 칸들. 이 중 하나라도 판정 표에 서면 그 순간 이 표는 집계이고, 그레인 표가 답해야 할 질문이 둘이 된다.
COUNTING = ("mentions", "documents", "quarter_mentions", "denom_channels", "channel_count", "persistence")


def _block(path: Path, table: str) -> str:
    found = re.search(
        rf"CREATE TABLE needs\.{table} \((.*?)\n\);", path.read_text(encoding="utf-8"), re.DOTALL
    )
    assert found, f"{path.name} declares no needs.{table}"
    return found.group(1)


def _primary_key(path: Path, table: str) -> list[str]:
    found = re.search(r"PRIMARY KEY \(([^)]*)\)", _block(path, table), re.DOTALL)
    assert found, f"needs.{table} has no PRIMARY KEY"
    return [name.strip() for name in found.group(1).replace("\n", " ").split(",")]


def _columns(path: Path, table: str) -> list[str]:
    names: list[str] = []
    for line in _block(path, table).splitlines():
        stripped = re.sub(r"\s+--.*", "", line).strip().rstrip(",")
        if re.match(r"(PRIMARY KEY|UNIQUE|CHECK|FOREIGN|REFERENCES)\b", stripped, re.IGNORECASE):
            continue
        named = re.match(r"([a-z_]+)\s+(.+)", stripped)
        if named:
            names.append(named.group(1))
    return names


def test_the_key_of_the_judgement_is_the_key_of_the_metric_row():
    """1:1 이 아니라면 판정은 지표의 파생이 아니라 자기 입자를 가진 다른 표다."""
    assert _primary_key(DDL, TABLE) == _primary_key(QUARTER_DDL, QUARTER)


def test_the_foreign_key_covers_that_whole_key():
    """`파생`의 기계적 형태. 여덟 중 하나라도 빠지면 지표 없는 판정이 조용히 설 수 있다."""
    found = re.search(
        r"FOREIGN KEY \((.*?)\)\s*\n\s*REFERENCES needs\.metrics_topic_quarter", _block(DDL, TABLE), re.DOTALL
    )
    assert found, "024 는 지표 행을 가리키는 FK 가 없다"
    assert [name.strip() for name in found.group(1).replace("\n", " ").split(",")] == _primary_key(DDL, TABLE)


def test_the_judgement_table_counts_nothing():
    assert not set(_columns(DDL, TABLE)) & set(COUNTING)


def test_the_table_is_not_named_as_a_metrics_table():
    """이름이 `metrics_` 로 시작하면 `tests/test_panel_quarter_contract.py` 가 그레인 선언을 요구한다."""
    assert not TABLE.startswith("metrics_")


def test_the_grain_table_does_not_list_it_and_says_why():
    lines = FORMATS.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines[lines.index(GRAIN_HEADER) + 2 :]:
        if not line.startswith("|"):
            break
        rows.append(line)
    assert not any(TABLE in row for row in rows)
    body = FORMATS.read_text(encoding="utf-8")
    assert f"needs.{TABLE}" in body, "그레인 표가 왜 이 표를 안 싣는지 formats.md 가 말하지 않는다"


def test_the_contract_calls_the_judgement_a_derivation_not_an_aggregate():
    """문장이 없으면 다음 사람이 같은 질문을 처음부터 다시 연다."""
    assert "집계가 아니라 파생" in INTERFACES.read_text(encoding="utf-8")
    assert "집계가 아니라 파생" in DDL.read_text(encoding="utf-8")


def test_the_index_of_contracts_names_the_new_migration():
    assert "024_topic_quarter_judgement.sql" in INDEX.read_text(encoding="utf-8")


def test_the_violation_view_is_readable_by_the_role_that_runs_the_analysis():
    body = VIEW.read_text(encoding="utf-8")
    assert f"CREATE VIEW needs.{TABLE}_violation" in body
    assert f"GRANT SELECT ON needs.{TABLE}_violation TO needs_runtime" in body
    # 뷰가 묻는 둘. 행 하나 안에서 볼 수 있는 것은 024 의 CHECK 이 지므로 여기 있지 않다.
    assert "'unjudged_cell'" in body and "'gap_pp_disagrees'" in body
