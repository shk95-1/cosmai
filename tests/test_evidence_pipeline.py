"""Loading the evidence: it finds the judged cells, reads candidates from the corpus, rewrites wholesale and
asks the stored rows back (fork #6).

This is where it parts from the judgement (#40) -- evidence **scans the corpus**. So the two traps #5
measured come with it: the partial index of the comment query (`content_type = 'comment'`) and
`idle_in_transaction_session_timeout` (15 seconds). This file holds those two places, and the fact that
evidence cannot stand without a judged cell.

The fixture corpus is really imported -- planting candidates by hand would leave the contract sentence about
sharing the population CTE unchecked. In exchange this file looks at the **shape** rather than the values,
and the values are carried by the golden set (`tests/test_evidence_golden.py`).
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
# Only one row is touched -- a full UPDATE hits the primary key first and never reaches the constraint it
# meant to try.
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
    """The schema after `cosmai trend quarter` and then `judge`. The cells the evidence attaches to are
    standing."""
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
    """The judgement simply has not been made yet, so 0 rows must not be written quietly -- the invariants
    are true over an empty table too."""
    with connect(judged) as conn, pytest.raises(NoEvidence):
        build(conn)


def test_the_evidence_lands_on_the_run_the_judgement_already_has(judged: str):
    with connect(judged) as conn:
        verdicts = judge_run(conn)
        outcome = run(conn)
        assert outcome.run_id == verdicts.run_id
        assert outcome.violations == []
        # At most three per cell. Some cells are short of candidates, so the product is a cap, not an
        # equality.
        assert 0 < outcome.written <= outcome.cells * TOP_PER_CELL
        with conn.cursor() as cur:
            cur.execute("SELECT versions->>'evidence' FROM analysis_run WHERE run_id = %s", (outcome.run_id,))
            assert cur.fetchone() == ("rule-v0.1",)


def test_running_twice_rewrites_the_same_rows(judged: str):
    """A partial update puts a quiet hole in the ladder of ranks."""
    with connect(judged) as conn:
        judge_run(conn)
        first = run(conn)
        second = run(conn)
        assert (first.run_id, first.written) == (second.run_id, second.written)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM topic_quarter_evidence")
            assert cur.fetchone() == (first.written,)


def test_the_evidence_only_stands_on_a_judged_cell(judged: str):
    """Pointer rather than derived, evidence still cannot stand without the cell it supports -- that is the
    first FK of 025."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.cursor() as cur:
            cur.execute(ONE_ROW.format(set="topic_key = '없는주제'"))
        conn.rollback()


def test_the_quote_must_be_a_document_of_that_snapshot(judged: str):
    """A doc_id with no version does not part from the same document of a recollection -- the second FK
    carries that."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.cursor() as cur:
            cur.execute(ONE_ROW.format(set="doc_id = 'youtube_comment:없는문서'"))
        conn.rollback()


def test_the_ddl_refuses_a_quote_from_another_source(judged: str):
    """Attaching a comment as evidence for a video cell is blocked inside one row."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
            cur.execute(ONE_ROW.format(set="source = 'youtube_video'"))
        conn.rollback()


def test_the_view_notices_a_ladder_with_a_hole(judged: str):
    """The unique key stops only duplicate ranks. With the first place gone, the card reads what is left as
    the top."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM topic_quarter_evidence WHERE rank = 1")
            cur.execute("SELECT DISTINCT violation FROM topic_quarter_evidence_violation")
            assert cur.fetchall() == [("rank_not_dense",)]
        conn.rollback()


def test_the_view_notices_a_quote_that_belongs_to_another_cell(judged: str):
    """The FK keeps only "that document exists in that version" -- whether that document said this topic is
    not asked."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with conn.cursor() as cur:
            # The judged cell exists but one row is moved to a topic this document did not say. The FK passes
            # and only the view catches it.
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
    """Exactly the completion criterion of this issue -- from one cell to the evidence text, a person writes
    no join.

    That `run_id` is on that one line is contract too (§Evidence): the view does not filter by run, so with
    two runs under one snapshot and roster the same cell comes out twice and `rank` stops being 1..n.
    """
    with connect(judged) as conn:
        judge_run(conn)
        outcome = run(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT trend_type, rank, like_count, matched_term, left(text, 20), parent_video_url "
                "FROM topic_quarter_evidence_quote "
                "WHERE run_id = %s AND topic_key = %s AND quarter = %s ORDER BY rank",
                (outcome.run_id, "자극_눈시림", "2026Q2"),
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

    It asks for **the plan** rather than looking at the string alone -- what #5 caught was the timeout in
    production rather than the shape of the predicate, and with the predicate unchanged but the index gone
    (fix 023) this test alone would stay green. Whether the plan picks that index at fixture size is checked
    below. Over everything (261,317 documents) it rides the same index at 178ms
    (2026-08-26, the contract's §Evidence, "full measurement").
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
    """Written out again, the speech a card quotes and the numbers written on that card stand on different
    denominators."""
    from analysis.evidence import pipeline
    from analysis.trend import pipeline as quarter

    assert pipeline.CANDIDATES.startswith(quarter.POPULATION)


def test_the_read_is_closed_before_the_fold(judged: str):
    """The 15-second trap. Holding the candidates on a cursor and starting to fold them cuts the connection --
    build picks after it commits."""
    with connect(judged) as conn:
        judge_run(conn)
        made = build(conn)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    assert made.rows and made.candidates


def test_a_quote_carries_no_copy_of_the_document(judged: str):
    """Copying the body makes two originals, and the sentence that the corpus is canonical goes false right
    there."""
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
    # 2026Q2 is the quarter with a cell that matches the rule in this sample (the golden set carries that
    # value).
    assert main(["trend", "cards", "--quarter", "2026Q2", "--url", judged]) == 0
    printed = capsys.readouterr()
    assert "# R&D Opportunity Card — 2026Q2" in printed.out
    assert "**소비자 발화 (좋아요 상위)**" in printed.out
    # stdout is markdown alone -- a note left inside the redirected `.md` makes that file not a document.
    assert "trend cards run=" not in printed.out
    assert "trend cards run=" in printed.err
    # **Zero cards is not a failure.** It is the answer after every rule has run, the same place #41 pinned in
    # §Sensitivity.
    assert main(["trend", "cards", "--quarter", "2024Q1", "--url", judged]) == 0
    assert "cards=0" in capsys.readouterr().err


def test_a_ruled_cell_with_no_quote_left_is_the_one_partial_the_cards_have(judged: str, capsys):
    """There is only one truncated output -- a cell that matched the rule but could not stand as a card
    because the evidence text is missing."""
    with connect(judged) as conn:
        judge_run(conn)
        run(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM topic_quarter_evidence WHERE quarter = %s", ("2026Q2",))
        conn.commit()
    assert main(["trend", "cards", "--quarter", "2026Q2", "--url", judged]) == 1
    printed = capsys.readouterr()
    assert "unquoted=" in printed.err and "unquoted_cell 2026Q2" in printed.err
    # stdout is still markdown all the same (a document with 0 cards).
    assert printed.out.startswith("# R&D Opportunity Card")


def test_a_quarter_outside_the_grid_says_so_instead_of_sending_you_back_to_judge(judged: str, capsys):
    """judge has already run and there are simply no population videos in that quarter -- nobody is sent on a
    wasted trip."""
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
