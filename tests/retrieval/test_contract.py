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

DDL = Path(__file__).resolve().parents[2] / "contracts" / "ddl" / "needs" / "020_retrieval_chunk.sql"


def test_the_cli_source_list_matches_the_corpus_adapter():
    # cli.py 가 psycopg 를 안 끌어오려고 값을 다시 적는다. 갈리면 --source 가 조용히 빈 결과를 낸다.
    assert RETRIEVAL_SOURCES == corpus.SOURCES


def test_the_ddl_lives_in_this_branchs_number_block():
    # main 이 00N 을 계속 쓰는 동안 파일명이 겹치면 db/migrate.sh 의 적용 순서가 갈린다.
    assert DDL.name.startswith("020_")


def test_the_ddl_is_additive_only():
    body = re.sub(r"--[^\n]*", "", DDL.read_text(encoding="utf-8"))
    for forbidden in ("DROP ", "ALTER COLUMN", "TRUNCATE", "DELETE FROM", "REVOKE"):
        assert forbidden not in body.upper(), forbidden


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
