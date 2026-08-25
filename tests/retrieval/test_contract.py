"""020_retrieval_chunk.sql 과 코드가 같은 것을 말하는지. tests/collectors/*/test_*_tables_match_ddl.py
와 같은 방법이되, 이쪽은 SQLAlchemy metadata 가 없으므로 청크 계약(FIELDS)과 DDL 컬럼을 맞댄다."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from analysis.retrieval import corpus
from analysis.retrieval.chunks import FIELDS
from cosmai.cli import RETRIEVAL_SOURCES

ROOT = Path(__file__).resolve().parents[2]
DDL = ROOT / "contracts" / "ddl" / "needs" / "020_retrieval_chunk.sql"
ENTRYPOINTS = ROOT / "contracts" / "entrypoints.md"
INTERFACES = ROOT / "contracts" / "interfaces.md"
INDEX = ROOT / "contracts" / "README.md"
SEARCH_HEADER = "| mode | engine | 질의 | P@10 | MRR@10 | Hit@10 |"


def _search_baseline_rows() -> dict[tuple[str, str], dict[str, str]]:
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    columns = [c.strip() for c in SEARCH_HEADER.strip("|").split("|")]
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for line in lines[lines.index(SEARCH_HEADER) + 2 :]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows[(cells[0], cells[1])] = dict(zip(columns, cells, strict=True))
    return rows


def _search_section() -> list[str]:
    lines = ENTRYPOINTS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## 검색"))
    end = next(i for i, line in enumerate(lines[start + 1 :], start + 1) if line.startswith("## "))
    return lines[start:end]


def _exit_code_bullet() -> str:
    section = _search_section()
    at = next(i for i, line in enumerate(section) if line.startswith("- 종료 코드:"))
    bullet = [section[at]]
    for line in section[at + 1 :]:
        if not line.startswith("  "):  # 이어지는 줄만. 다음 항목은 다시 `- ` 로 시작한다
            break
        bullet.append(line)
    return "\n".join(bullet)


def test_the_cli_source_list_matches_the_corpus_adapter():
    # cli.py 가 psycopg 를 안 끌어오려고 값을 다시 적는다. 갈리면 --source 가 조용히 빈 결과를 낸다.
    assert RETRIEVAL_SOURCES == corpus.SOURCES


def test_the_exit_code_contract_covers_every_retrieval_subcommand():
    """계약이 덮지 않는 하위명령은 종료 코드가 구현에만 있다 -- `eval` 의 "질의 0개 -> 1"(cli.py)과
    `embed` 의 "언제나 0" 이 그랬다(#17 S6)."""
    bullet = _exit_code_bullet()
    for action in ("chunk", "search", "eval", "embed", "terms"):
        assert f"`{action}`" in bullet, action


def test_the_search_baseline_table_carries_every_mode_and_engine():
    """heldout 의 vector P@10 이 #28 단계 4 벡터 채택의 근거인데, 그 여섯 줄이 사는 곳이
    지워질 리뷰 문서뿐이었다 -- 계약이 그 거처다(#17 S10)."""
    from analysis.retrieval import eval as retrieval_eval

    rows = _search_baseline_rows()
    assert set(rows) == {(m, e) for m in retrieval_eval.MODES for e in retrieval_eval.ENGINES}


def test_the_adoption_threshold_is_the_bm25_heldout_floor():
    # heldout 에서 어휘 검색은 구조적으로 0 이다. 그 0 이 벡터가 넘어야 하는 선이라, 둘이 갈리면
    # 채택 판정이 근거를 잃는다.
    rows = _search_baseline_rows()
    floor = float(rows[("heldout", "bm25")]["P@10"])
    assert floor == 0.0
    assert float(rows[("heldout", "vector")]["P@10"]) > floor
    assert "P@10 > .000" in INTERFACES.read_text(encoding="utf-8")


def test_the_ddl_lives_in_this_branchs_number_block():
    # main 이 00N 을 계속 쓰는 동안 파일명이 겹치면 db/migrate.sh 의 적용 순서가 갈린다.
    assert DDL.name.startswith("020_")


def test_the_contracts_index_carries_a_row_for_the_chunk_ddl():
    """`contracts/README.md` 의 표가 계약 파일의 색인이고 셋째 칸이 "무엇이 이것을 검사하는가" 다.
    020 은 그 표에 없어서, 색인만 읽으면 이 유닛의 계약이 아예 없는 것으로 보였다(#18 M18).
    추가만 검사는 여기 두지 않는다 -- tests/test_ddl_additive_only.py 가 이 디렉터리의 파일
    전부를 더 넓은 어휘로 훑으므로, 여기 사본을 두면 좁은 쪽이 통과 도장처럼 읽힌다."""
    rows = [line for line in INDEX.read_text(encoding="utf-8").splitlines() if DDL.name in line]
    assert len(rows) == 1, rows
    checkers = [c.strip("`") for c in re.findall(r"`[^`]+`", rows[0]) if c.strip("`").endswith(".py")]
    assert checkers, rows[0]
    for checker in checkers:
        assert (ROOT / checker).exists(), checker


@pytest.mark.postgres
def test_the_table_carries_the_five_contract_columns(needs_schema: str, _schema_name: str):
    engine = create_engine(needs_schema)
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("retrieval_chunk", schema=_schema_name)}
    finally:
        engine.dispose()
    assert set(FIELDS) <= set(columns)
    # 파생값 둘. text_md5 가 없으면 재실행이 매번 30만 행을 UPDATE 한다.
    assert {"text_md5", "chunked_at"} <= set(columns)
    for name in FIELDS:
        assert columns[name]["nullable"] is False, name


@pytest.mark.postgres
def test_the_runtime_role_can_write_chunks(needs_runtime_url: str):
    # 적재는 needs_runtime 이 한다. GRANT 가 빠지면 첫 운영 실행에서야 드러난다.
    import psycopg
    from sqlalchemy.engine import make_url

    parsed = make_url(needs_runtime_url)
    conn = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
                "VALUES ('s:1#0', 's:1', 's', 0, '백탁', 'x')"
            )
            cur.execute("SELECT count(*) FROM retrieval_chunk")
            row = cur.fetchone()
            assert row is not None and row[0] == 1
        conn.commit()
    finally:
        conn.close()
