"""근거의 적재: 판정 셀을 찾고, 코퍼스에서 후보를 읽고, 통째로 다시 쓰고, 저장된 행에 되묻는다 (포크 #6).

판정(#40)과 갈리는 자리가 여기다 -- 근거는 **코퍼스를 훑는다.** 그래서 #5 가 실측한 두 함정이 그대로
붙는다: 댓글 질의의 부분 인덱스(`content_type = 'comment'`)와 `idle_in_transaction_session_timeout`
(15초). 이 파일이 그 두 자리와, 근거가 판정 셀 없이는 설 수 없다는 사실을 붙든다.

픽스처 코퍼스를 실제로 반입한다 -- 후보를 손으로 심으면 모집단 CTE 를 공유한다는 계약 문장이 검사되지
않는다. 그 대신 이 파일은 값이 아니라 **모양**을 보고, 값은 골든(`tests/test_evidence_golden.py`)이 진다.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, text

from analysis.evidence import TOP_PER_CELL
from analysis.evidence.pipeline import NoEvidence, build, run
from analysis.judge.pipeline import run as judge_run
from analysis.retrieval import topics as topic_registry
from analysis.trend.pipeline import PANEL_ROLE, SCOPE
from analysis.trend.pipeline import run as quarter_run
from cosmai.cli import main
from db import corpus, seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "trend_sample"
VIEWS = (
    ROOT / "db" / "views" / "metrics_topic_quarter_violation.sql",
    ROOT / "db" / "views" / "topic_quarter_judgement_violation.sql",
    ROOT / "db" / "views" / "topic_quarter_evidence_quote.sql",
    ROOT / "db" / "views" / "topic_quarter_evidence_violation.sql",
)
OWNER = text("SET ROLE needs_owner")
# 한 행만 건드린다 -- 전량 UPDATE 는 기본키에서 먼저 부딪혀 보려던 제약에 닿지 못한다.
ONE_ROW = (
    "UPDATE topic_quarter_evidence SET {set} WHERE ctid = (SELECT ctid FROM topic_quarter_evidence LIMIT 1)"
)


def _install_views(url: str, schema: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            for view in VIEWS:
                conn.exec_driver_sql(view.read_text(encoding="utf-8").replace("needs.", f'"{schema}".'))
    finally:
        engine.dispose()


@pytest.fixture
def judged(needs_schema: str, needs_runtime_url: str, _schema_name: str) -> str:
    """`cosmai trend quarter` 다음 `judge` 까지 간 스키마. 근거가 붙을 셀이 서 있는 상태다."""
    _install_views(needs_schema, _schema_name)
    seed.run_all(needs_runtime_url, only=("panel",))
    where = ["--kind", "aspect", "--version", "1", "--url", needs_runtime_url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0
    with connect(needs_runtime_url) as conn:
        corpus.load(conn, FIXTURE / "corpus")
        quarter_run(conn)
    return needs_runtime_url


def test_a_run_without_judgement_rows_is_blocked_not_failed(judged: str):
    """판정을 아직 안 한 것이라 0행을 조용히 쓰면 안 된다 -- 빈 표 위에서도 불변식은 참이다."""
    with connect(judged) as conn, pytest.raises(NoEvidence):
        build(conn)


def test_the_evidence_lands_on_the_run_the_judgement_already_has(judged: str):
    with connect(judged) as conn:
        verdicts = judge_run(conn)
        outcome = run(conn)
        assert outcome.run_id == verdicts.run_id
        assert outcome.violations == []
        # 셀마다 최대 셋. 후보가 모자란 셀이 있으므로 곱셈은 상한이지 등식이 아니다.
        assert 0 < outcome.written <= outcome.cells * TOP_PER_CELL
        with conn.cursor() as cur:
            cur.execute("SELECT versions->>'evidence' FROM analysis_run WHERE run_id = %s", (outcome.run_id,))
            assert cur.fetchone() == ("rule-v0.1",)


def test_running_twice_rewrites_the_same_rows(judged: str):
    """부분 갱신이면 자리(rank)의 사다리가 조용히 구멍 난다."""
    with connect(judged) as conn:
        judge_run(conn)
        first = run(conn)
        second = run(conn)
        assert (first.run_id, first.written) == (second.run_id, second.written)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM topic_quarter_evidence")
            assert cur.fetchone() == (first.written,)


def test_the_evidence_only_stands_on_a_judged_cell(judged: str):
    """`파생`이 아니라 `포인터`여도 근거는 자기가 받치는 셀 없이 설 수 없다 -- 025 의 첫 FK 다."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.cursor() as cur:
            cur.execute(ONE_ROW.format(set="topic_key = '없는주제'"))
        conn.rollback()


def test_the_quote_must_be_a_document_of_that_snapshot(judged: str):
    """판본 없는 doc_id 는 재수집분의 같은 문서와 갈리지 않는다 -- 두 번째 FK 가 그것을 든다."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.cursor() as cur:
            cur.execute(ONE_ROW.format(set="doc_id = 'youtube_comment:없는문서'"))
        conn.rollback()


def test_the_ddl_refuses_a_quote_from_another_source(judged: str):
    """댓글을 영상 셀의 근거로 다는 일이 행 하나 안에서 막힌다."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
            cur.execute(ONE_ROW.format(set="source = 'youtube_video'"))
        conn.rollback()


def test_the_view_notices_a_ladder_with_a_hole(judged: str):
    """유일키는 자리의 중복만 막는다. 1위가 사라지면 카드는 남은 것을 상위로 읽는다."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM topic_quarter_evidence WHERE rank = 1")
            cur.execute("SELECT DISTINCT violation FROM topic_quarter_evidence_violation")
            assert cur.fetchall() == [("rank_not_dense",)]
        conn.rollback()


def test_the_view_notices_a_quote_that_belongs_to_another_cell(judged: str):
    """FK 는 "그 판본에 그런 문서가 있다"까지만 지킨다 -- 그 문서가 이 주제를 말했는지는 묻지 않는다."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with conn.cursor() as cur:
            # 판정 셀은 있는데 이 문서가 말하지 않은 주제로 한 행을 옮긴다. FK 는 통과하고 뷰만 잡는다.
            cur.execute(
                "SELECT j.topic_key FROM topic_quarter_judgement j"
                " JOIN topic_quarter_evidence e ON e.run_id = j.run_id AND e.quarter = j.quarter"
                "  AND e.source = j.source AND e.rank = 1"
                " WHERE j.topic_key <> e.topic_key"
                "   AND NOT EXISTS (SELECT 1 FROM corpus_mention m"
                "                    WHERE m.snapshot_id = e.snapshot_id AND m.doc_id = e.doc_id"
                "                      AND m.topic_id = j.topic_key)"
                "   AND NOT EXISTS (SELECT 1 FROM topic_quarter_evidence k"
                "                    WHERE k.run_id = e.run_id AND k.quarter = e.quarter"
                "                      AND k.source = e.source AND k.topic_key = j.topic_key)"
                " LIMIT 1"
            )
            found = cur.fetchone()
            assert found is not None
            cur.execute(ONE_ROW.format(set="topic_key = %s"), (found[0],))
            cur.execute("SELECT DISTINCT violation FROM topic_quarter_evidence_violation")
            assert cur.fetchall() == [("quote_outside_the_cell",)]
        conn.rollback()


def test_one_where_on_the_view_reaches_the_quote_from_a_judged_cell(judged: str):
    """이 이슈의 완료 기준 그대로 -- 셀 하나에서 근거 원문까지 사람이 조인을 쓰지 않는다."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trend_type, rank, like_count, matched_term, left(text, 20), parent_video_url "
                "FROM topic_quarter_evidence_quote WHERE topic_key = %s AND quarter = %s ORDER BY rank",
                ("자극_눈시림", "2026Q2"),
            )
            rows = cur.fetchall()
        conn.commit()
    assert rows, "판정된 셀에 근거가 닿지 않는다"
    assert [row[1] for row in rows] == list(range(1, len(rows) + 1))
    for trend_type, _rank, likes, term, body, url in rows:
        assert trend_type and term and body and url
        assert likes >= 0


def test_the_candidate_query_takes_the_partial_index_the_corpus_declares(judged: str):
    """`source = 'youtube_comment'` 하나만 걸면 023 의 부분 인덱스를 못 타고 26만 행을 훑는다
    (#5 운영 실측: 30초 statement_timeout 에 죽는다). 두 술어가 나란히 서 있어야 한다.

    문자열만 보지 않고 **계획을 묻는다** -- #5 가 잡은 것은 술어의 모양이 아니라 운영에서의 timeout 이고,
    술어가 그대로여도 인덱스가 사라지면(023 을 고치면) 이 테스트만 초록으로 남는다. 표본 크기에서도
    계획이 그 인덱스를 고르는지는 아래가 확인한다. 전량(261,317문서)에서도 같은 인덱스를 타고 178ms 다
    (2026-08-26, 계약 §근거 "전량 실측").
    """
    from analysis.evidence import pipeline
    from analysis.trend.pipeline import PANEL_ROLE, TOPIC_FILTER

    assert "c.content_type = 'comment'" in pipeline.CANDIDATES
    assert "c.source = 'youtube_comment'" in pipeline.CANDIDATES
    with connect(judged) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "EXPLAIN " + pipeline.CANDIDATES,
                {
                    "snapshot": 1,
                    "panel_version": 1,
                    "panel_role": PANEL_ROLE,
                    "topic_filter": TOPIC_FILTER,
                },
            )
            plan = "\n".join(line for (line,) in cur.fetchall())
        conn.commit()
    assert "parent_item_id" in plan, plan
    assert "Seq Scan on corpus_document" not in plan, plan


def test_the_population_is_the_one_the_metrics_were_counted_on():
    """다시 적으면 카드가 인용하는 발화와 카드에 적힌 숫자가 다른 분모 위에 선다."""
    from analysis.evidence import pipeline
    from analysis.trend import pipeline as quarter

    assert pipeline.CANDIDATES.startswith(quarter.POPULATION)


def test_the_read_is_closed_before_the_fold(judged: str):
    """15초 함정. 후보를 커서로 들고 접기 시작하면 연결이 끊긴다 -- build 는 커밋한 뒤에 고른다."""
    with connect(judged) as conn:
        judge_run(conn)
        made = build(conn)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    assert made.rows and made.candidates


def test_a_quote_carries_no_copy_of_the_document(judged: str):
    """본문을 베끼면 원문이 두 벌이 되고, 코퍼스가 정본이라는 문장이 그 자리에서 거짓이 된다."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'topic_quarter_evidence' ORDER BY 1"
            )
            columns = {name for (name,) in cur.fetchall()}
        conn.commit()
    assert "text" not in columns and "url" not in columns
    assert {"doc_id", "snapshot_id", "rank", "like_count", "matched_term"} <= columns


def test_the_cli_writes_the_evidence_and_then_renders_the_cards(judged: str, capsys):
    with connect(judged) as conn:
        judge_run(conn)
    assert main(["trend", "evidence", "--url", judged]) == 0
    assert "trend evidence run=" in capsys.readouterr().out
    # 2026Q2 는 이 표본에서 규칙에 걸리는 셀이 있는 분기다 (골든이 그 값을 진다).
    assert main(["trend", "cards", "--quarter", "2026Q2", "--url", judged]) == 0
    printed = capsys.readouterr()
    assert "# R&D Opportunity Card — 2026Q2" in printed.out
    assert "**소비자 발화 (좋아요 상위)**" in printed.out
    # stdout 은 마크다운뿐이다 -- 리다이렉트한 `.md` 안에 note 가 남으면 그 파일은 그대로 문서가 아니다.
    assert "trend cards run=" not in printed.out
    assert "trend cards run=" in printed.err
    # **카드 0건은 실패가 아니다.** 규칙이 다 돌고 나온 답이고, #41 이 §민감도 에서 못 박은 자리와 같다.
    assert main(["trend", "cards", "--quarter", "2024Q1", "--url", judged]) == 0
    assert "cards=0" in capsys.readouterr().err


def test_a_ruled_cell_with_no_quote_left_is_the_one_partial_the_cards_have(judged: str, capsys):
    """잘린 산출은 하나뿐이다 -- 규칙에 걸렸는데 근거 원문이 없어 카드로 서지 못한 셀."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM topic_quarter_evidence WHERE quarter = %s", ("2026Q2",))
        conn.commit()
    assert main(["trend", "cards", "--quarter", "2026Q2", "--url", judged]) == 1
    printed = capsys.readouterr()
    assert "unquoted=" in printed.err and "unquoted_cell 2026Q2" in printed.err
    # 그래도 stdout 은 여전히 마크다운이다(카드 0장짜리 문서).
    assert printed.out.startswith("# R&D Opportunity Card")


def test_a_quarter_outside_the_grid_says_so_instead_of_sending_you_back_to_judge(judged: str, capsys):
    """judge 는 이미 돌았고 그 분기에 모집단 영상이 없을 뿐이다 -- 헛걸음을 시키지 않는다."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
    assert main(["trend", "cards", "--quarter", "2019Q1", "--url", judged]) == 2
    said = capsys.readouterr().out
    assert "not in this run's grid" in said and "2026Q2" in said
    assert "cosmai trend judge" not in said


def test_the_cards_are_blocked_before_the_judgement_exists(judged: str, capsys):
    assert main(["trend", "cards", "--quarter", "2026Q2", "--url", judged]) == 2
    assert "cosmai trend judge" in capsys.readouterr().out


def test_the_scope_and_role_stay_the_ones_the_quarter_used(judged: str):
    with connect(judged) as conn:
        judge_run(conn)
        outcome = run(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT scope, panel_role, content_type FROM topic_quarter_evidence")
            assert cur.fetchall() == [(SCOPE, PANEL_ROLE, "long_form")]
    assert outcome.written > 0
