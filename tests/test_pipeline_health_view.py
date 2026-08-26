"""`needs.pipeline_health`: 로그를 단계별 '지금 상태' 한 줄로 접는다 (#138).

상류 두 뷰(collector_health·analysis_health)는 자기 테스트를 따로 갖는다. 여기서 재는 것은 이
뷰의 판정뿐이라 상류를 **스텁 표**로 세운다 -- 상류의 산출을 여기서 다시 검증하면 같은 사실을
두 자리에서 주장하게 되고, 상류가 바뀔 때 무관한 테스트가 빨개진다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW = REPO_ROOT / "db" / "views" / "pipeline_health.sql"

NOW = datetime.now(UTC)


def ago(**kw: float) -> datetime:
    return NOW - timedelta(**kw)


# 주기를 모두 1시간으로 두면 눈금(1배·2배)이 한눈에 읽힌다. 배수 규칙 자체가 시험 대상이라
# 단계마다 다른 주기를 섞으면 무엇이 틀렸는지 흐려진다.
STAGES = (
    # (stage_key, arm, dataset, interval, enabled)
    ("commerce:ranking", "commerce", "ranking", "1 hour", True),  # ok
    ("commerce:review", "commerce", "review", "1 hour", True),  # late
    ("commerce:product", "commerce", "product", "1 hour", True),  # stalled + 마지막 run 은 failed
    ("commerce:new_product", "commerce", "new_product", "1 hour", True),  # ok + 마지막 run 은 failed
    ("naver:datalab", "naver", "datalab", "1 hour", True),  # never
    ("youtube:watch", "youtube", "watch", "1 hour", False),  # disabled — 최신 성공이 있어도
    ("analyze:all", "analyze", "all", "1 hour", True),
    ("analyze:polarity_missing", "analyze", "polarity_missing", "1 hour", True),
)

# (collector, dataset, started, finished, status, requests, ok, blocked, failed, queued, p90)
COLLECTOR_ROWS = (
    ("commerce", "ranking", ago(minutes=35), ago(minutes=30), "ok", 30, 30, 0, 0, None, 2300),
    ("commerce", "review", ago(minutes=95), ago(minutes=90), "ok", 10, 10, 0, 0, None, 500),
    ("commerce", "product", ago(hours=5), ago(hours=5), "ok", 5, 5, 0, 0, None, 100),
    # 5시간 전에 성공한 뒤로 실패만 -- freshness 는 stalled, 마지막 run 은 failed 여야 한다.
    ("commerce", "product", ago(minutes=15), ago(minutes=10), "failed", 5, 0, 0, 5, None, 100),
    # 방금 실패했지만 성공이 아직 주기 안이다 -- 두 값이 갈리는 반대 방향.
    ("commerce", "new_product", ago(minutes=20), ago(minutes=18), "ok", 4, 4, 0, 0, None, 90),
    ("commerce", "new_product", ago(minutes=6), ago(minutes=5), "failed", 4, 0, 0, 4, None, 90),
    # dataset 이 빈 옛 행은 어느 단계인지 말하지 못한다 -- 어느 단계에도 얹히면 안 된다.
    ("commerce", "", ago(minutes=1), ago(minutes=1), "ok", 0, 0, 0, 0, None, None),
    ("youtube", "watch", ago(minutes=5), ago(minutes=5), "ok", 1, 1, 0, 0, 0, 50),
)

# (stage, started, finished, status, note)
ANALYSIS_ROWS = (
    ("analyze:all", ago(minutes=40), ago(minutes=39), "ok", "analyze:all product_ref=190"),
    (
        "analyze:polarity:llm-ollama-gemma4:latest-fs2",
        ago(hours=4),
        ago(hours=3),
        "ok",
        "analyze:polarity:llm-ollama-gemma4 missing=1 replaced=0",
    ),
    # 크론 단계가 아닌 run 들. 어느 단계에도 얹히면 안 된다.
    ("eval:polarity:rule-v2.2", ago(minutes=2), ago(minutes=2), "ok", "eval:polarity:rule-v2.2"),
    ("trend-quarter:v0.2:선블록", ago(minutes=2), ago(minutes=2), "ok", "trend-quarter:v0.2"),
    # missing= 이 없는 polarity run 은 증분 패스가 아니다.
    ("analyze:polarity:rule-v2.2", ago(minutes=3), ago(minutes=3), "ok", "analyze:polarity:rule-v2.2"),
)

COLUMNS = (
    "stage_key, arm, dataset, enabled, expected_interval, last_success_at, last_run_at,"
    " last_run_status, overdue_by, freshness, requests, ok, blocked, failed, p90_ms"
)


def _build(url: str, schema: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".pipeline_stage'
            " (stage_key, arm, dataset, expected_interval, enabled)"
            " VALUES (%s, %s, %s, %s::interval, %s)",
            list(STAGES),
        )
        # 상류 스텁. 이름과 컬럼만 진짜와 같으면 이 뷰에게는 구분이 없다.
        conn.exec_driver_sql(
            f'CREATE TABLE "{schema}".collector_health (collector text, dataset text,'
            " run_id uuid, started_at timestamptz, finished_at timestamptz, status text,"
            " requests int, ok int, blocked int, failed int, queued int, p90_ms int)"
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".collector_health (collector, dataset, started_at, finished_at,'
            " status, requests, ok, blocked, failed, queued, p90_ms)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            list(COLLECTOR_ROWS),
        )
        conn.exec_driver_sql(
            f'CREATE TABLE "{schema}".analysis_health (run_id bigint, stage text,'
            " started_at timestamptz, finished_at timestamptz, status text, note text)"
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".analysis_health (stage, started_at, finished_at, status, note)'
            " VALUES (%s, %s, %s, %s, %s)",
            list(ANALYSIS_ROWS),
        )
        conn.exec_driver_sql(VIEW.read_text(encoding="utf-8").replace("needs.", f'"{schema}".'))
    engine.dispose()


@pytest.fixture
def health(needs_schema: str, needs_runtime_url: str, _schema_name: str) -> dict[str, Any]:
    """뷰를 읽는 롤은 needs_runtime 이다 -- 화면이 PostgREST 로 읽는 것과 같은 권한 경로."""
    _build(needs_schema, _schema_name)
    engine = create_engine(needs_runtime_url)
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT {COLUMNS} FROM pipeline_health")).mappings().all()
    engine.dispose()
    return {r["stage_key"]: dict(r) for r in rows}


def test_every_declared_stage_gets_exactly_one_row(health: dict[str, Any]):
    assert set(health) == {s[0] for s in STAGES}


@pytest.mark.parametrize(
    ("stage_key", "expected"),
    [
        ("commerce:ranking", "ok"),
        ("commerce:review", "late"),
        ("commerce:product", "stalled"),
        ("naver:datalab", "never"),
        ("youtube:watch", "disabled"),
    ],
)
def test_freshness_reads_the_last_success_against_the_expected_interval(
    health: dict[str, Any], stage_key: str, expected: str
):
    assert health[stage_key]["freshness"] == expected


def test_disabled_wins_over_a_fresh_success(health: dict[str, Any]):
    # youtube watch 는 5분 전에 성공했지만 profile 뒤라 안 도는 것이 선언이다 -- 선언이 이긴다.
    row = health["youtube:watch"]
    assert row["freshness"] == "disabled"
    assert row["last_success_at"] is not None


def test_never_has_no_overdue_because_the_question_does_not_arise(health: dict[str, Any]):
    # 성공한 적이 없으면 "얼마나 늦었나" 가 성립하지 않는다. 0 으로 눕히면 정시라는 뜻이 된다.
    row = health["naver:datalab"]
    assert row["last_success_at"] is None
    assert row["overdue_by"] is None
    assert row["last_run_status"] is None


def test_freshness_and_last_run_status_are_two_facts_not_one(health: dict[str, Any]):
    # 오래 전에 성공하고 그 뒤로 실패만: 안 돌았다(stalled) + 실패했다(failed) 둘 다 읽혀야 한다.
    stale = health["commerce:product"]
    assert (stale["freshness"], stale["last_run_status"]) == ("stalled", "failed")
    # 반대 방향: 방금 실패했지만 성공이 아직 주기 안이라 다음 회차를 기다려도 된다.
    fresh = health["commerce:new_product"]
    assert (fresh["freshness"], fresh["last_run_status"]) == ("ok", "failed")


def test_the_run_statistics_come_from_the_last_run_not_the_last_success(health: dict[str, Any]):
    row = health["commerce:product"]
    assert (row["requests"], row["ok"], row["failed"]) == (5, 0, 5)


def test_a_row_with_no_dataset_lands_on_no_stage(health: dict[str, Any]):
    # dataset 이 빈 옛 행(#101 이전)이 어느 단계에 붙으면 그 단계가 거짓으로 신선해진다.
    assert health["commerce:ranking"]["last_run_at"] > ago(minutes=31)
    assert health["commerce:ranking"]["requests"] == 30


def test_the_two_analyze_lines_are_told_apart_by_the_note_not_the_stage(health: dict[str, Any]):
    # stage 는 구현 판본을 달고 있어 그대로 못 쓴다. 증분 패스는 note 의 missing= 으로 갈린다.
    assert health["analyze:all"]["freshness"] == "ok"
    incremental = health["analyze:polarity_missing"]
    assert incremental["freshness"] == "stalled"  # 3시간 전 성공, 주기 1시간
    assert incremental["requests"] is None  # 분석 팔에는 외부 fetch 통계가 없다


def test_runs_that_are_not_cron_stages_are_ignored(health: dict[str, Any]):
    # eval:* · trend-quarter:* · missing= 없는 polarity 는 2~3분 전에 돌았다. 그것이 어느 단계에
    # 얹혔다면 analyze 쪽 freshness 가 ok 로 뒤집힌다.
    assert health["analyze:polarity_missing"]["freshness"] == "stalled"
