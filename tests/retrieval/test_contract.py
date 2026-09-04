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


def test_the_index_axis_sentence_credits_the_tokenizer_and_idf_not_lift():
    """fork #59: the contract once said lift removes general terms on the index axis. lift runs only in the
    `terms` report and never touches BM25 scoring; what drops those words is the tokenizer (13) and idf (16),
    the contrast tests/retrieval/test_query_stopwords.py counts. The sentence must say that, and must not
    contradict the query-axis sentence, which says neither lift nor idf is the ground there."""
    section = "\n".join(_search_section())
    assert "never touches" in section and "BM25 scoring" in section
    assert "13" in section and "16" in section and "idf" in section
    assert "lift removes" not in section and "removed by the lift" not in section


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


def test_the_scorecard_column_the_contract_names_is_a_column():
    """계약이 `store` 열을 말하는데 FIELDS 에 없으면 CSV 는 판본을 안 싣는다 -- 종료 코드 항목이
    하위명령마다 있는지 보는 위 테스트와 같은 자리다(#49)."""
    from analysis.retrieval import eval as retrieval_eval

    section = "\n".join(_search_section())
    assert "CSV `store` 열" in section
    # 판본과 경고는 축이 다른 두 열이다 -- 하나로 합치면 정상일 때 판본이 사라진다.
    assert {"store", "note"} <= set(retrieval_eval.FIELDS)


def test_the_scorecard_carries_the_dictionary_column_on_every_engine():
    """사전 판본은 `store` 와 축이 다르다 -- 저장소는 vector·hybrid 만 열지만 정답도 질의도 사전이
    만드므로 bm25 행도 사전 위에 서 있다 (#62)."""
    from analysis.retrieval import eval as retrieval_eval

    section = "\n".join(_search_section())
    assert "CSV `dictionary` 열" in section
    assert {"store", "note", "dictionary"} <= set(retrieval_eval.FIELDS)


def test_the_baseline_splits_what_it_could_retrace_from_what_it_could_not():
    """되짚은 것과 못 되짚은 것을 한 낱말로 뭉치면 다음 사람은 번호표 `v1` 을 DB 가 대는 근거로
    읽는다 -- 그 근거는 없다 (#62, #49 가 벡터 축에서 한 것과 같은 가름)."""
    text = INTERFACES.read_text(encoding="utf-8")
    # 줄바꿈은 서식이라 문장을 끊어 읽지 않는다 -- 재래핑 한 번에 빨개지면 아무도 안 고친다.
    flat = " ".join(text.split())
    assert (
        "version=1 · topics=15 · aliases=73 · fingerprint=5a0cae76311e1408" in flat
    )  # 옛 적재 원본과 남아 있는 DB v2 가 함께 대는 값
    assert "**되짚은 것 — 사전의 내용과 지문.**" in flat
    assert "**되짚을 수 없는 것 — 번호표.**" in flat
    # v1 행이 없다는 사실이 지워지면 그 번호표가 다시 근거처럼 읽힌다.
    assert "v1 행이 없다" in flat


def test_the_baseline_names_the_store_the_vector_lines_stand_on():
    """판본을 안 적으면 다음 재측정이 어느 저장소와의 델타인지 말할 수 없다 -- ydc 가 "1차 → 2차" 로
    라벨한 델타가 실은 "식약처 벡터 없음 → 2차" 였던 자리다(#49)."""
    text = INTERFACES.read_text(encoding="utf-8")
    header = next(line for line in text.splitlines() if line.startswith("## 검색 실측"))
    chunks = re.search(r"청크 ([\d,]+)", header)
    stamped = re.search(r"vectors=(\d+)", text)
    assert chunks and stamped, "표 머리의 청크 수와 저장소 판본 줄 중 하나가 없다"
    # 벡터가 코퍼스를 다 덮지 않은 저장소로 잰 표는 그 사실이 표 안에 있어야 한다.
    assert int(stamped.group(1)) == int(chunks.group(1).replace(",", ""))
    assert "bm25 두 줄은 그 판본 위의 값이 아니다" in text
