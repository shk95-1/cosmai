"""판정의 적재: 그 run 을 찾고, 통째로 다시 쓰고, 저장된 행에 되묻는다 (포크 #40).

지표(#5)와 갈리는 자리 셋을 본다. ① 판정은 코퍼스를 읽지 않는다 -- 입력이 `metrics_topic_quarter`
하나라 이 파이프라인의 질의는 그 표만 만진다. ② 판정 행은 지표 행 없이 설 수 없다(024 의 FK).
③ 지표 행이 아직 없는 것은 실패가 아니라 막힘이다.

DB 를 타지만 코퍼스는 타지 않으므로 지표 행을 손으로 심는다 -- 그래야 격자 하나를 정확히 통제할 수
있고, 골든(`tests/test_judge_golden.py`)이 실제 코퍼스 쪽을 진다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text

from analysis.judge import THIN
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
SNAPSHOT = 7  # 코퍼스를 타지 않으므로 스냅샷 번호는 run 의 note 를 만드는 데만 쓰인다.
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
    """`cosmai trend quarter` 가 낸 것과 같은 모양의 조밀한 격자 하나."""
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


def test_a_run_without_metric_rows_is_blocked_not_failed(graded: str):
    """지표를 아직 안 세운 것이라 0행을 조용히 쓰면 안 된다 -- 빈 표도 불변식은 참이다."""
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
    """부분 갱신이 아니라 통째로 다시 쓰는 것이 지표 행과의 1:1 을 지키는 방법이다."""
    with connect(graded) as conn:
        _plant(conn)
        first = run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        second = run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        assert (first.run_id, first.written) == (second.run_id, second.written)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM topic_quarter_judgement")
            assert cur.fetchone() == (8,)


def test_a_judgement_row_cannot_stand_without_its_metric_row(graded: str):
    """ "파생"의 기계적 형태가 이 FK 다 -- 문장으로만 있으면 지표 없는 판정이 조용히 산다."""
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
    """FK 는 그 반대를 못 지킨다 -- 판정이 일부에서 빠지면 유형 분포가 남은 것들의 분포가 된다."""
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
            # 한 source 만 있는 격자이므로 gap_pp 는 NULL 이어야 한다 -- 값을 넣는 것이 곧 위반이다.
            cur.execute("UPDATE topic_quarter_judgement SET gap_pp = 1.0 WHERE quarter = %s", (QUARTERS[0],))
            cur.execute("SELECT violation, count(*) FROM topic_quarter_judgement_violation GROUP BY 1")
            assert cur.fetchall() == [("gap_pp_disagrees", 2)]
        conn.rollback()


def test_the_ddl_refuses_a_judged_flag_that_disagrees_with_the_type(graded: str):
    """이름만 있고 정의가 없으면 행이 자기 이름과 다른 것을 말한다."""
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
    """점수는 그 집합 안에서 정규화된 눈금이라, 집합 밖의 행에 있으면 다른 눈금이 섞인 것이다."""
    with connect(graded) as conn:
        _plant(conn)
        run(conn, snapshot_id=SNAPSHOT, panel_version=1)
        with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
            cur.execute("UPDATE topic_quarter_judgement SET opportunity_score = 50.0 WHERE NOT judged")
        conn.rollback()


def test_the_pipeline_reads_no_table_but_the_quarter_metrics(graded: str):
    """판정이 코퍼스를 다시 훑으면 그 순간 이 단계는 지표 계산의 사본이 된다 (#5 의 인덱스 함정도 그
    질의에 붙는다). 이 유닛의 SELECT 는 지표 표 하나여야 한다."""
    from analysis.judge import pipeline

    assert "corpus_document" not in pipeline.SELECT_METRICS
    assert "metrics_topic_quarter" in pipeline.SELECT_METRICS
