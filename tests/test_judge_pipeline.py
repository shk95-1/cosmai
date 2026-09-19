"""Loading the judgement: it finds that run, rewrites it wholesale and asks the stored rows back (fork #40).

Three places where it parts from the metrics (#5) are looked at. 1. The judgement does not read the corpus --
its one input is `metrics_topic_quarter`, so this pipeline's queries touch only that table. 2. A judgement
row cannot stand without a metric row (the FK of 024). 3. A metric row not being there yet is blocked rather
than a failure.

It goes through the DB but not the corpus, so metric rows are planted by hand -- that way one grid can be
controlled exactly, and the golden set (`tests/test_judge_golden.py`) carries the real-corpus side.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text

from analysis.judge import RUNNING, THIN
from analysis.judge.pipeline import NoJudgement, build, run
from analysis.trend.pipeline import INSERT as INSERT_METRIC
from analysis.trend.pipeline import OPEN_RUN, PANEL_ROLE, SCOPE, note_of
from analysis.trend.pipeline import _values as metric_values
from analysis.types import MetricsTopicQuarterRow
from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
VIEWS = (
    ROOT / "db" / "views" / "metrics_topic_quarter_violation.sql",
    ROOT / "db" / "views" / "topic_quarter_judgement_violation.sql",
)
SNAPSHOT = 7  # the corpus is not touched, so the snapshot number only builds the run's note.
QUARTERS = ("2024Q1", "2024Q2", "2024Q3", "2024Q4")
TOPICS = ("백탁", "발림성")
OWNER = text("SET ROLE needs_owner")


def _metric(topic: str, quarter: str, run_id: int, **kw: Any) -> MetricsTopicQuarterRow:
    base: dict[str, Any] = dict(
        run_id=run_id, scope=SCOPE, topic_key=topic, quarter=quarter, source="youtube_video",
        content_type="long_form", panel_version=1, panel_role=PANEL_ROLE,
        mentions=20, documents=100, quarter_mentions=40, denom_channels=10,
        composition=0.5, velocity_yoy=0.0, persistence=1.0, persist_quarters=4,
        window_quarters=4, unique_ratio=1.0, channel_count=8, channel_diffusion=0.5, sample_ok=True,
    )  # fmt: skip
    base.update(kw)
    return MetricsTopicQuarterRow(**base)


@pytest.fixture
def graded(needs_schema: str, needs_runtime_url: str, _schema_name: str) -> str:
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            for view in VIEWS:
                conn.exec_driver_sql(view.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
    finally:
        engine.dispose()
    seed.run_all(needs_runtime_url, only=("panel",))
    return needs_runtime_url


def _plant(conn: psycopg.Connection[Any]) -> int:
    """One dense grid of the same shape `cosmai trend quarter` emits."""
    with conn.cursor() as cur:
        cur.execute(OPEN_RUN, ('{"metric": "v0.2"}', note_of(SCOPE, SNAPSHOT, 1)))
        found = cur.fetchone()
        assert found is not None
        run_id = int(found[0])
        cur.executemany(
            INSERT_METRIC,
            [metric_values(_metric(t, q, run_id)) for t in TOPICS for q in QUARTERS],
        )
    conn.commit()
    return run_id


def test_the_quarter_in_progress_comes_from_the_cutoff_the_run_recorded(graded: str):
    """A run three days into 2025Q1 whose last quarter with rows is 2024Q4: that quarter is complete and is
    judged, not labelled in progress (fork #96)."""
    with connect(graded) as conn:
        run_id = _plant(conn)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE analysis_run SET versions = versions || %s::jsonb WHERE run_id = %s",
                ('{"cutoff": "2025-01-03T00:00:00+00:00"}', run_id),
            )
        conn.commit()
        cutoff = {row.trend_type for row in build(conn, snapshot_id=SNAPSHOT, panel_version=1).rows}
        with conn.cursor() as cur:
            cur.execute("UPDATE analysis_run SET versions = versions - 'cutoff' WHERE run_id = %s", (run_id,))
        conn.commit()
        without = {
            row.quarter: row.trend_type for row in build(conn, snapshot_id=SNAPSHOT, panel_version=1).rows
        }
    assert RUNNING not in cutoff
    assert without[QUARTERS[-1]] == RUNNING


def test_a_run_without_metric_rows_is_blocked_not_failed(graded: str):
    """The metrics are simply not counted yet, so 0 rows must not be written quietly -- the invariants are
    true of an empty table too."""
    with connect(graded) as conn, pytest.raises(NoJudgement):
        build(conn, snapshot_id=SNAPSHOT, panel_version=1)


def test_the_judgement_lands_on_the_run_the_metrics_already_have(graded: str):
    with connect(graded) as conn:
        run_id = _plant(conn)
        outcome = run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        assert (outcome.run_id, outcome.written, outcome.violations) == (run_id, 8, [])
        with conn.cursor() as cur:
            cur.execute("SELECT versions->>'judgement' FROM analysis_run WHERE run_id = %s", (run_id,))
            assert cur.fetchone() == ("v0.2",)


def test_running_twice_rewrites_the_same_rows(graded: str):
    """Rewriting wholesale rather than updating in part is how the 1:1 with the metric rows is kept."""
    with connect(graded) as conn:
        _plant(conn)
        first = run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        second = run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        assert (first.run_id, first.written) == (second.run_id, second.written)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM topic_quarter_judgement")
            assert cur.fetchone() == (8,)


def test_clear_does_not_touch_a_sibling_content_type_under_the_same_run(graded: str):
    """Without content_type in CLEAR's delete key, a short_form clear would also take the run's
    long_form judgement (#200) -- a sibling row is planted by hand (cloned from a real row so the FK
    to metrics_topic_quarter is already satisfied) to ask whether it survives a rerun."""
    with connect(graded) as conn:
        run_id = _plant(conn)
        run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO metrics_topic_quarter"
                " (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role,"
                "  mentions, documents, quarter_mentions, denom_channels, composition, velocity_yoy,"
                "  persistence, persist_quarters, window_quarters, unique_ratio, channel_count,"
                "  channel_diffusion, sample_ok)"
                " SELECT run_id, scope, topic_key, quarter, source, 'short_form', panel_version,"
                "  panel_role, mentions, documents, quarter_mentions, denom_channels, composition,"
                "  velocity_yoy, persistence, persist_quarters, window_quarters, unique_ratio,"
                "  channel_count, channel_diffusion, sample_ok"
                " FROM metrics_topic_quarter WHERE run_id = %s LIMIT 1",
                (run_id,),
            )
            cur.execute(
                "INSERT INTO topic_quarter_judgement"
                " (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role,"
                "  trend_type, judged, evidence_strength, opportunity_score, gap_pp, hold_reason,"
                "  single_source)"
                " SELECT run_id, scope, topic_key, quarter, source, 'short_form', panel_version,"
                "  panel_role, trend_type, judged, evidence_strength, opportunity_score, gap_pp,"
                "  hold_reason, single_source"
                " FROM topic_quarter_judgement WHERE run_id = %s LIMIT 1",
                (run_id,),
            )
        conn.commit()
        run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM topic_quarter_judgement"
                " WHERE run_id = %s AND content_type = 'short_form'",
                (run_id,),
            )
            assert cur.fetchone() == (1,)


def test_a_judgement_row_cannot_stand_without_its_metric_row(graded: str):
    """This FK is the mechanical form of "derived" -- as a sentence alone, a judgement with no metric lives
    quietly."""
    with connect(graded) as conn:
        _plant(conn)
        run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.cursor() as cur:
            cur.execute(
                "UPDATE topic_quarter_judgement SET topic_key = '없는주제' "
                "WHERE quarter = %s AND topic_key = %s",
                (QUARTERS[0], TOPICS[0]),
            )
        conn.rollback()


def test_the_view_notices_a_metric_row_nobody_judged(graded: str):
    """The FK cannot keep the converse -- if the judgement is missing on some, the type distribution becomes
    the distribution of what is left."""
    with connect(graded) as conn:
        _plant(conn)
        run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM topic_quarter_judgement WHERE quarter = %s", (QUARTERS[0],))
            cur.execute("SELECT violation, count(*) FROM topic_quarter_judgement_violation GROUP BY 1")
            assert cur.fetchall() == [("unjudged_cell", 2)]
        conn.rollback()


def test_the_view_notices_two_source_rows_that_disagree_about_the_gap(graded: str):
    with connect(graded) as conn:
        _plant(conn)
        run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        with conn.cursor() as cur:
            # It is a grid with one source, so gap_pp has to be NULL -- putting a value in is the violation.
            cur.execute("UPDATE topic_quarter_judgement SET gap_pp = 1.0 WHERE quarter = %s", (QUARTERS[0],))
            cur.execute("SELECT violation, count(*) FROM topic_quarter_judgement_violation GROUP BY 1")
            assert cur.fetchall() == [("gap_pp_disagrees", 2)]
        conn.rollback()


def test_the_ddl_refuses_a_judged_flag_that_disagrees_with_the_type(graded: str):
    """A name with no definition makes a row say something other than its own name."""
    with connect(graded) as conn:
        _plant(conn)
        run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
            cur.execute(
                "UPDATE topic_quarter_judgement SET trend_type = %s WHERE quarter = %s",
                (THIN, QUARTERS[1]),
            )
        conn.rollback()


def test_the_ddl_refuses_a_score_on_a_cell_that_was_not_judged(graded: str):
    """The score is a scale normalized inside that set, so on a row outside the set it is two scales mixed."""
    with connect(graded) as conn:
        _plant(conn)
        run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
            cur.execute("UPDATE topic_quarter_judgement SET opportunity_score = 50.0 WHERE NOT judged")
        conn.rollback()


def test_the_pipeline_reads_no_table_but_the_quarter_metrics(graded: str):
    """The moment the judgement rescans the corpus, this stage becomes a copy of the metric computation (and
    the index trap of #5 comes with that query). The SELECTs of this unit have to be the metrics table
    alone."""
    from analysis.judge import pipeline

    assert "corpus_document" not in pipeline.SELECT_METRICS
    assert "metrics_topic_quarter" in pipeline.SELECT_METRICS
