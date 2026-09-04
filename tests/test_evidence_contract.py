"""Contract test: 근거 표는 세지 않고 **가리킨다** — 025 와 계약 문장이 같은 것을 말한다 (포크 #6).

이 이슈에서 조용히 틀릴 수 있는 자리가 둘이었다. 하나는 **본문을 어디에 두나**다: 근거 행에 텍스트를
베끼면 표는 혼자 서지만 그 순간 원문이 두 벌이 되고, 코퍼스가 정본이라는 문장이 거짓이 된다. 다른 하나는
**무엇을 가리키나**다 — 지표 행을 가리키면 근거는 판정과 무관하게 서고, "이 유형의 근거는 무엇인가"에
답하려면 사람이 다시 조인해야 한다. 이 파일이 그 두 자리를 기계로 붙든다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DDL_DIR = ROOT / "contracts" / "ddl" / "needs"
DDL = DDL_DIR / "025_topic_quarter_evidence.sql"
JUDGEMENT_DDL = DDL_DIR / "024_topic_quarter_judgement.sql"
CORPUS_DDL = DDL_DIR / "023_corpus_snapshot.sql"
FORMATS = ROOT / "contracts" / "formats.md"
INTERFACES = ROOT / "contracts" / "interfaces.md"
INDEX = ROOT / "contracts" / "README.md"
ENTRYPOINTS = ROOT / "contracts" / "entrypoints.md"
VERSIONING = ROOT / "contracts" / "versioning.md"
QUOTE_VIEW = ROOT / "db" / "views" / "topic_quarter_evidence_quote.sql"
VIOLATION_VIEW = ROOT / "db" / "views" / "topic_quarter_evidence_violation.sql"

TABLE = "topic_quarter_evidence"
JUDGEMENT = "topic_quarter_judgement"
GRAIN_HEADER = "| 그레인 | 정본 표 | 행의 시간 칸 |"
# 본문·제목처럼 문서의 내용을 베낀 칸. 하나라도 서면 코퍼스가 정본이라는 문장이 그 자리에서 거짓이 된다.
COPIED = ("text", "title", "body", "url", "published_at", "quality_flags", "source_metadata")


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


def test_the_key_is_the_judgement_cell_plus_the_place_in_the_ladder():
    """근거는 셀 하나에 여럿이므로 키가 셀보다 한 칸 길다. 그 한 칸이 자리(rank)여야 사다리가 생긴다."""
    assert _primary_key(DDL, TABLE) == [*_primary_key(JUDGEMENT_DDL, JUDGEMENT), "rank"]


def test_it_points_at_the_judgement_cell_not_at_the_metric_row():
    """지표 행을 가리키면 "이 유형의 근거"라는 물음이 FK 로 서지 않는다."""
    found = re.search(
        rf"FOREIGN KEY \((.*?)\)\s*\n\s*REFERENCES needs\.{JUDGEMENT}\b", _block(DDL, TABLE), re.DOTALL
    )
    assert found, "025 는 판정 셀을 가리키는 FK 가 없다"
    assert [name.strip() for name in found.group(1).replace("\n", " ").split(",")] == _primary_key(
        JUDGEMENT_DDL, JUDGEMENT
    )
    assert "REFERENCES needs.metrics_topic_quarter" not in DDL.read_text(encoding="utf-8")


def test_it_points_at_the_corpus_document_with_the_snapshot_in_hand():
    """판본 없는 doc_id 는 재수집분(#38)의 같은 문서와 갈리지 않는다 — 023 의 유일키가 그래서 두 칸이다."""
    body = _block(DDL, TABLE)
    assert re.search(
        r"FOREIGN KEY \(snapshot_id, doc_id\)\s*REFERENCES needs\.corpus_document \(snapshot_id, doc_id\)",
        body,
    )
    assert "UNIQUE (snapshot_id, doc_id)" in _block(CORPUS_DDL, "corpus_document")


def test_the_evidence_row_copies_no_part_of_the_document():
    assert not set(_columns(DDL, TABLE)) & set(COPIED)


def test_the_source_of_the_quote_is_the_source_of_the_cell():
    """doc_id 는 source 와 source_item_id 를 이은 값이라(023 의 생성 열) 그 규칙이 행 하나 안에서 선다."""
    assert "CHECK (split_part(doc_id, ':', 1) = source)" in _block(DDL, TABLE)


def test_the_cap_per_cell_is_not_frozen_into_the_ddl():
    """상한은 보고서의 손잡이라 바뀐다. DDL 은 추가만이라 CHECK 을 되돌릴 수 없다."""
    assert "CHECK (rank >= 1)" in _block(DDL, TABLE)
    assert not re.search(r"rank\s*(<=|BETWEEN)", _block(DDL, TABLE))
    assert "TOP_PER_CELL = 3" in INTERFACES.read_text(encoding="utf-8")


def test_the_grain_table_does_not_list_it_and_says_why():
    lines = FORMATS.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines[lines.index(GRAIN_HEADER) + 2 :]:
        if not line.startswith("|"):
            break
        rows.append(line)
    assert not any(TABLE in row for row in rows)
    assert f"needs.{TABLE}" in FORMATS.read_text(encoding="utf-8")


def test_the_contract_calls_the_evidence_a_pointer():
    assert "포인터" in INTERFACES.read_text(encoding="utf-8")
    assert "포인터" in DDL.read_text(encoding="utf-8")


def test_the_reach_from_a_cell_to_the_quote_is_one_view():
    """완료 기준 그 자체 — 셀에서 원문까지 사람이 조인을 쓰지 않는다."""
    body = QUOTE_VIEW.read_text(encoding="utf-8")
    assert f"CREATE VIEW needs.{TABLE}_quote" in body
    assert f"GRANT SELECT ON needs.{TABLE}_quote TO needs_runtime" in body
    for table in (f"needs.{JUDGEMENT}", f"needs.{TABLE}", "needs.corpus_document"):
        assert table in body, table
    # 댓글의 url 은 그 댓글이 아니라 부모 영상이다. 이름이 그것을 말하지 않으면 클릭해서 그 발화를
    # 찾을 수 있다고 읽힌다 (계약 §근거).
    assert "AS parent_video_url" in body


def test_the_violation_view_asks_the_two_things_a_row_cannot_answer():
    body = VIOLATION_VIEW.read_text(encoding="utf-8")
    assert f"CREATE VIEW needs.{TABLE}_violation" in body
    assert f"GRANT SELECT ON needs.{TABLE}_violation TO needs_runtime" in body
    assert "'rank_not_dense'" in body and "'quote_outside_the_cell'" in body


def test_the_contracts_name_the_new_migration_and_both_commands():
    assert "025_topic_quarter_evidence.sql" in INDEX.read_text(encoding="utf-8")
    entrypoints = ENTRYPOINTS.read_text(encoding="utf-8")
    assert "cosmai trend evidence" in entrypoints
    assert "cosmai trend cards --quarter" in entrypoints


def test_the_version_key_of_the_run_carries_the_evidence_definition():
    body = VERSIONING.read_text(encoding="utf-8")
    assert "metric, judgement, evidence}" in body
    assert "`evidence` 는" in body
