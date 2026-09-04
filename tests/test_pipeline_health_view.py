"""`needs.pipeline_health`: folds the logs into one "state now" line per stage (#138).

The two upstream views (collector_health · analysis_health) have tests of their own. What is measured here is
the judgement of this view alone, so the upstream is stood up as **stub tables** -- validating the upstream
output here would assert the same fact in two places and turn an unrelated test red when the upstream
changes.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW = REPO_ROOT / "db" / "views" / "pipeline_health.sql"

# Seconds slept between the fixture insert and the view query. Zero in the suite; the knob is how the
# time-independence of the freshness assertions below is re-proved on demand (#213).
DELAY_ENV = "COSMAI_TEST_FRESHNESS_DELAY"


def ago(**kw: float) -> timedelta:
    """An offset, not a timestamp: the view judges freshness against `now()` at query time, so rows dated
    from a module-level constant are already tens of minutes old by the time a long suite reaches this file,
    and the tightest window here (analyze:all -- a success 40 minutes back against a period of an hour, so
    20 minutes of margin) is crossed for reasons that have nothing to do with the view (#213). The fixture
    resolves every offset against one reading taken milliseconds before the query that reads it back."""
    return timedelta(**kw)


def _rows(reference: datetime, rows: tuple[tuple[Any, ...], ...]) -> list[tuple[Any, ...]]:
    """Every offset in a fixture row becomes a timestamp against the one reference reading."""
    return [tuple(reference - v if isinstance(v, timedelta) else v for v in row) for row in rows]


# With every period at one hour the scale (1x, 2x) reads at a glance. The multiple rule itself is under test,
# so mixing different periods per stage blurs what went wrong.
STAGES = (
    # (stage_key, arm, dataset, interval, enabled)
    ("commerce:ranking", "commerce", "ranking", "1 hour", True),  # ok
    ("commerce:product", "commerce", "product", "1 hour", True),  # stalled + the last run is failed
    ("commerce:new_product", "commerce", "new_product", "1 hour", True),  # ok + the last run is failed
    # A stage that runs on the hour every day but is always partial. Where "did it run" parts from "did it run
    # cleanly" (#154).
    ("commerce:review", "commerce", "review", "1 hour", True),
    # A stage with only runs that yielded to the source lock -- nothing was collected, so it did not run.
    ("commerce:review_stats", "commerce", "review_stats", "1 hour", True),
    ("naver:datalab", "naver", "datalab", "1 hour", True),  # never
    ("youtube:watch", "youtube", "watch", "1 hour", False),  # disabled -- even with a recent success
    ("analyze:all", "analyze", "all", "1 hour", True),
    ("analyze:polarity_missing", "analyze", "polarity_missing", "1 hour", True),
)

# (collector, dataset, started, finished, status, requests, ok, blocked, failed, queued, p90)
COLLECTOR_ROWS = (
    ("commerce", "ranking", ago(minutes=35), ago(minutes=30), "ok", 30, 30, 0, 0, None, 2300),
    # It ran 20 minutes ago and collected 84 of 89. It is not late -- partial is not "it could not run".
    ("commerce", "review", ago(minutes=25), ago(minutes=20), "partial", 89, 84, 0, 5, None, 500),
    # Only yielded runs: the same as never having run.
    ("commerce", "review_stats", ago(minutes=20), ago(minutes=19), "yielded", 0, 0, 0, 0, None, None),
    ("commerce", "product", ago(hours=5), ago(hours=5), "ok", 5, 5, 0, 0, None, 100),
    # A success 5 hours ago and failures since -- freshness has to be stalled and the last run failed.
    ("commerce", "product", ago(minutes=15), ago(minutes=10), "failed", 5, 0, 0, 5, None, 100),
    # Just failed but the success is still inside the period -- the opposite direction of the two values
    # parting.
    ("commerce", "new_product", ago(minutes=20), ago(minutes=18), "ok", 4, 4, 0, 0, None, 90),
    ("commerce", "new_product", ago(minutes=6), ago(minutes=5), "failed", 4, 0, 0, 4, None, 90),
    # An old row with an empty dataset cannot say which stage it is -- it must not land on any stage.
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
    # Runs that are not cron stages. They must not land on any stage.
    ("eval:polarity:rule-v2.2", ago(minutes=2), ago(minutes=2), "ok", "eval:polarity:rule-v2.2"),
    ("trend-quarter:v0.2:선블록", ago(minutes=2), ago(minutes=2), "ok", "trend-quarter:v0.2"),
    # A polarity run without missing= is not an incremental pass.
    ("analyze:polarity:rule-v2.2", ago(minutes=3), ago(minutes=3), "ok", "analyze:polarity:rule-v2.2"),
)

COLUMNS = (
    "stage_key, arm, dataset, enabled, expected_interval, last_success_at, last_run_at,"
    " last_run_status, overdue_by, freshness, requests, ok, blocked, failed, p90_ms"
)


def _build(url: str, schema: str, reference: datetime, view_sql: str | None = None) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".pipeline_stage'
            " (stage_key, arm, dataset, expected_interval, enabled)"
            " VALUES (%s, %s, %s, %s::interval, %s)",
            list(STAGES),
        )
        # The upstream stubs. With the names and columns matching the real ones, this view sees no
        # difference.
        conn.exec_driver_sql(
            f'CREATE TABLE "{schema}".collector_health (collector text, dataset text,'
            " run_id uuid, started_at timestamptz, finished_at timestamptz, status text,"
            " requests int, ok int, blocked int, failed int, queued int, p90_ms int)"
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".collector_health (collector, dataset, started_at, finished_at,'
            " status, requests, ok, blocked, failed, queued, p90_ms)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            _rows(reference, COLLECTOR_ROWS),
        )
        conn.exec_driver_sql(
            f'CREATE TABLE "{schema}".analysis_health (run_id bigint, stage text,'
            " started_at timestamptz, finished_at timestamptz, status text, note text)"
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".analysis_health (stage, started_at, finished_at, status, note)'
            " VALUES (%s, %s, %s, %s, %s)",
            _rows(reference, ANALYSIS_ROWS),
        )
        sql = VIEW.read_text(encoding="utf-8") if view_sql is None else view_sql
        conn.exec_driver_sql(sql.replace("needs.", f'"{schema}".'))
    engine.dispose()


def _read(url: str) -> dict[str, Any]:
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT {COLUMNS} FROM pipeline_health")).mappings().all()
    engine.dispose()
    return {r["stage_key"]: dict(r) for r in rows}


@pytest.fixture
def reference(needs_schema: str) -> datetime:
    """The clock the fixture rows are dated from, read from the database rather than from this process: it is
    the same `now()` the view will compare them against."""
    engine = create_engine(needs_schema)
    with engine.connect() as conn:
        ref = conn.execute(text("SELECT now()")).scalar_one()
    engine.dispose()
    return ref


@pytest.fixture
def health(
    needs_schema: str, needs_runtime_url: str, _schema_name: str, reference: datetime
) -> dict[str, Any]:
    """The role that reads the view is needs_runtime -- the same permission path the screen reads through."""
    _build(needs_schema, _schema_name, reference)
    time.sleep(float(os.environ.get(DELAY_ENV, "0")))
    return _read(needs_runtime_url)


def test_every_declared_stage_gets_exactly_one_row(health: dict[str, Any]):
    assert set(health) == {s[0] for s in STAGES}


# One list, because the positive control at the bottom has to break exactly what these assert.
FRESHNESS = (
    ("commerce:ranking", "ok"),
    ("commerce:review", "ok"),
    ("commerce:review_stats", "never"),
    ("commerce:product", "stalled"),
    ("naver:datalab", "never"),
    ("youtube:watch", "disabled"),
    ("analyze:all", "ok"),
    ("analyze:polarity_missing", "stalled"),
)


@pytest.mark.parametrize(("stage_key", "expected"), FRESHNESS)
def test_freshness_reads_the_last_success_against_the_expected_interval(
    health: dict[str, Any], stage_key: str, expected: str
):
    assert health[stage_key]["freshness"] == expected


def test_disabled_wins_over_a_fresh_success(health: dict[str, Any]):
    # youtube watch succeeded 5 minutes ago but is declared not to run behind the profile -- the declaration
    # wins.
    row = health["youtube:watch"]
    assert row["freshness"] == "disabled"
    assert row["last_success_at"] is not None


def test_never_has_no_overdue_because_the_question_does_not_arise(health: dict[str, Any]):
    # With no success ever, "how late is it" does not stand. Flattened to 0 it means on time.
    row = health["naver:datalab"]
    assert row["last_success_at"] is None
    assert row["overdue_by"] is None
    assert row["last_run_status"] is None


def test_freshness_and_last_run_status_are_two_facts_not_one(health: dict[str, Any]):
    # A success long ago and failures since: both did-not-run (stalled) and failed have to be readable.
    stale = health["commerce:product"]
    assert (stale["freshness"], stale["last_run_status"]) == ("stalled", "failed")
    # The opposite direction: it just failed but the success is still inside the period, so the next round can
    # be waited for.
    fresh = health["commerce:new_product"]
    assert (fresh["freshness"], fresh["last_run_status"]) == ("ok", "failed")


def test_the_run_statistics_come_from_the_last_run_not_the_last_success(health: dict[str, Any]):
    row = health["commerce:product"]
    assert (row["requests"], row["ok"], row["failed"]) == (5, 0, 5)


def test_a_row_with_no_dataset_lands_on_no_stage(health: dict[str, Any], reference: datetime):
    # An old row with an empty dataset (before #101) attaching to a stage would make that stage falsely
    # fresh.
    assert health["commerce:ranking"]["last_run_at"] > reference - timedelta(minutes=31)
    assert health["commerce:ranking"]["requests"] == 30


def test_the_two_analyze_lines_are_told_apart_by_the_note_not_the_stage(health: dict[str, Any]):
    # stage carries the implementation revision and cannot be used as it is. An incremental pass is told apart
    # by missing= in the note.
    assert health["analyze:all"]["freshness"] == "ok"
    incremental = health["analyze:polarity_missing"]
    assert incremental["freshness"] == "stalled"  # a success 3 hours ago, a period of 1 hour
    assert incremental["requests"] is None  # the analysis arm has no external fetch statistics


def test_runs_that_are_not_cron_stages_are_ignored(health: dict[str, Any]):
    # eval:* · trend-quarter:* and polarity without missing= ran 2-3 minutes ago. Had any of them landed on a
    # stage, the freshness of the analyze side would flip to ok.
    assert health["analyze:polarity_missing"]["freshness"] == "stalled"


# The one branch the assertions above are made of: every freshness value other than never and disabled hangs
# off this comparison.
OK_BRANCH = "now() - o.at <= s.expected_interval"


def test_the_freshness_expectations_fail_when_the_view_stops_measuring_the_period(
    needs_schema: str, needs_runtime_url: str, _schema_name: str, reference: datetime
):
    """A positive control (#213): assertions made time-independent must not have become time-blind. With the
    period comparison widened until every enabled stage is inside its window, the two stages that are behind
    theirs have to stop matching FRESHNESS -- and the rest have to keep matching, so what moved is named."""
    view = VIEW.read_text(encoding="utf-8")
    assert view.count(OK_BRANCH) == 1, "the branch this control breaks moved -- point it at the new one"
    _build(
        needs_schema, _schema_name, reference, view.replace(OK_BRANCH, "now() - o.at <= interval '100 years'")
    )
    broken = _read(needs_runtime_url)
    assert [k for k, expected in FRESHNESS if broken[k]["freshness"] != expected] == [
        "commerce:product",
        "analyze:polarity_missing",
    ]
