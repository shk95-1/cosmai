"""`needs.collector_health`: the 12 columns of the contract's §Common operations view are filled by the three
arms commerce + naver + youtube.

그 계약은 contracts/entrypoints.md 의 절이다. youtube 팔은 #77 이 붙였고, 원천이 run 이 아니라
`tubedepth.jobs` 라 한 행이 (dataset, started_at 의 1시간 버킷) 하나다.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW = REPO_ROOT / "db" / "views" / "collector_health.sql"
ENTRYPOINTS_MD = REPO_ROOT / "contracts" / "entrypoints.md"

STARTED = datetime(2026, 8, 23, 1, 0, tzinfo=UTC)
FINISHED = datetime(2026, 8, 23, 1, 30, tzinfo=UTC)
# RUN_B has its own time -- a leak from the run next to it would show
STARTED_B = datetime(2026, 8, 23, 4, 0, tzinfo=UTC)
FINISHED_B = datetime(2026, 8, 23, 4, 5, tzinfo=UTC)
RUN_A = UUID("11111111-1111-4111-8111-111111111111")  # commerce, two fetch_log datasets
RUN_B = UUID("22222222-2222-4222-8222-222222222222")  # commerce, a run with not one fetch_log line
RUN_C = UUID("33333333-3333-4333-8333-333333333333")  # naver
RUN_D = UUID("44444444-4444-4444-8444-444444444444")  # commerce, every source skipped (lock contention)
RUN_E = UUID("55555555-5555-4555-8555-555555555555")  # commerce, one source errored (a real partial)

# (dataset, http status, elapsed_ms, error). 404 lands in none of the three buckets the contract
# counts (2xx / 403,429 / error,5xx) -- it is meant to show up only as the gap against requests.
COMMERCE_FETCHES = (
    ("rank", 200, 10, None),
    ("rank", 200, 20, None),
    ("rank", 200, 30, None),
    ("rank", 403, 40, None),
    ("rank", 429, 50, None),
    ("rank", 500, 60, "server error"),
    ("rank", None, 70, "timeout"),
    ("rank", 404, 80, None),
    ("review", 200, 5, None),
)
# Even with a line where elapsed_ms is NULL mixed in, p90 must be the percentile of the two remaining
# values.
NAVER_FETCHES = (
    ("blog", 200, 100, None),
    ("blog", 429, 200, None),
    ("blog", None, None, "timeout"),
)
COLUMNS = "collector, dataset, run_id, started_at, finished_at, status, requests, ok, blocked, failed, queued, p90_ms"  # noqa: E501

# One row of the youtube arm is one (dataset, hour bucket) -- for a collector with no run, this is the
# view building the "finite bundle of work" that corresponds to commerce's run, out of time instead. The
# four below are those four rows.
# a fully finished bucket: success, blocked, failed and cancelled all mixed in
YT_WATCH = datetime(2026, 8, 23, 1, 0, tzinfo=UTC)
YT_BLOCKED = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)  # a bucket where every failure is quota exhaustion
YT_LEGACY = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)  # a row from before #101/#102: no dataset, no elapsed_ms
YT_QUEUE = datetime(2026, 8, 23, 4, 0, tzinfo=UTC)  # nothing has been claimed yet = a full queue

# (state, error_code, elapsed_ms). One cancelled is deliberate -- it is the share that lands in none of
# the ok/blocked/failed buckets, the same spot 404 sits in on the commerce arm.
YT_WATCH_JOBS = (
    ("succeeded", None, 10),
    ("succeeded", None, 20),
    ("succeeded", None, 30),
    ("failed", "quota", 40),
    ("failed", "rate_limited", 50),
    ("failed", "http_403", 60),
    ("failed", "http_500", 70),
    ("cancelled", None, 80),
)
# The third line is a row from before elapsed_ms existed -- if p90 filled that in with 0, the result
# would be 1800, not 1900.
YT_BLOCKED_JOBS = (("failed", "quota", 1000), ("failed", "quota", 2000), ("failed", "quota", None))
YT_LEGACY_JOBS = (("succeeded", None, None), ("succeeded", None, None))
YT_QUEUE_JOBS = (("queued", None, None), ("queued", None, None))
YT_BUCKETS = (
    (YT_WATCH, "watch", YT_WATCH_JOBS),
    (YT_BLOCKED, "work", YT_BLOCKED_JOBS),
    (YT_LEGACY, None, YT_LEGACY_JOBS),
    (YT_QUEUE, "work", YT_QUEUE_JOBS),
)


def _youtube_job_rows() -> list[tuple[Any, ...]]:
    """A `state='queued'` job has never been claimed so it has no started_at, and an old row is the
    same -- so the bucket has to be `coalesce(started_at, created_at)` for both cases to land at their
    own time."""
    rows: list[tuple[Any, ...]] = []
    for bucket, dataset, jobs in YT_BUCKETS:
        for index, (state, error_code, elapsed_ms) in enumerate(jobs):
            at = bucket + timedelta(minutes=index)
            started = at if elapsed_ms is not None else None
            finished = None if state == "queued" else at + timedelta(minutes=1)
            rows.append(
                (
                    uuid4().hex,
                    "video.metadata",
                    f"v{index}",
                    state,
                    at,
                    at,
                    started,
                    finished,
                    elapsed_ms,
                    error_code,
                    dataset,
                )
            )
    return rows


def _seed_and_create_view(url: str, schema: str, td_schema: str) -> None:
    """In production, db/migrate.sh (f) is what applies this file, and needs_owner is the role at that
    point."""
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".run (id, captured_at, started_at, finished_at, status, sources,'
            " datasets) VALUES (%s, %s, %s, %s, 'ok', 'oliveyoung', 'rank,review'),"
            "        (%s, %s, %s, %s, 'failed', 'oliveyoung', 'rank')",
            (RUN_A, STARTED, STARTED, FINISHED, RUN_B, STARTED_B, STARTED_B, FINISHED_B),
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".fetch_log (run_id, at, source, dataset, url, status, attempt,'
            " elapsed_ms, error) VALUES (%s, %s, 'oliveyoung', %s, 'https://x', %s, 1, %s, %s)",
            [(RUN_A, STARTED, d, s, ms, e) for d, s, ms, e in COMMERCE_FETCHES],
        )
        # RUN_D is a run every source stepped back from because of a lock, and RUN_E is a run where one
        # source genuinely errored -- the engine writes run.status as 'partial' identically for both
        # (collectors/commerce/cli.py), so the view cannot tell them apart unless collector_health looks
        # at run_source.outcome.
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".run (id, captured_at, started_at, finished_at, status, sources,'
            " datasets) VALUES (%s, %s, %s, %s, 'partial', 'oliveyoung,hwahae', 'rank'),"
            "        (%s, %s, %s, %s, 'partial', 'oliveyoung,hwahae', 'rank')",
            (RUN_D, STARTED_B, STARTED_B, FINISHED_B, RUN_E, STARTED_B, STARTED_B, FINISHED_B),
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".run_source (run_id, source, requests, records, retries, deduped,'
            " dropped_over_depth, budget_exhausted, error_count, errors, outcome) VALUES"
            " (%s, 'oliveyoung', 0, 0, 0, 0, 0, false, 0, 'skipped: locked', 'skipped'),"
            " (%s, 'hwahae', 0, 0, 0, 0, 0, false, 0, 'skipped: locked', 'skipped'),"
            " (%s, 'oliveyoung', 3, 0, 0, 0, 0, false, 1, 'boom', 'error'),"
            " (%s, 'hwahae', 3, 3, 0, 0, 0, false, 0, NULL, 'ok')",
            (RUN_D, RUN_D, RUN_E, RUN_E),
        )
        # The source table belongs to collectors/commerce -- in production what opens this SELECT is
        # the needs_owner block of db/grants/needs_runtime_reader.sql.
        conn.exec_driver_sql(
            f'GRANT SELECT ON "{schema}".run, "{schema}".fetch_log, "{schema}".run_source TO needs_owner'
        )
        # tubedepth is a separate schema belonging to collectors/youtube -- the same needs_owner block
        # of db/grants/needs_runtime_reader.sql is also what opens these two lines in production.
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{td_schema}" TO needs_owner')
        conn.exec_driver_sql(f'GRANT SELECT ON "{td_schema}".jobs TO needs_owner')
        conn.exec_driver_sql(
            f'INSERT INTO "{td_schema}".jobs (identifier, kind, target, state, attempt_count,'
            " max_attempts, scheduled_at, created_at, started_at, finished_at, elapsed_ms,"
            " error_code, dataset, webhook_attempts) VALUES (%s, %s, %s, %s, 0, 3, %s, %s, %s, %s,"
            " %s, %s, %s, 0)",
            _youtube_job_rows(),
        )
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".naver_run (id, dataset, captured_at, started_at, finished_at, status)'
            " VALUES (%s, 'blog', %s, %s, %s, 'partial')",
            (RUN_C, STARTED, STARTED, FINISHED),
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".naver_fetch_log (run_id, at, dataset, query, status, attempt,'
            " elapsed_ms, error) VALUES (%s, %s, %s, 'q', %s, 1, %s, %s)",
            [(RUN_C, STARTED, d, s, ms, e) for d, s, ms, e in NAVER_FETCHES],
        )
        sql = VIEW.read_text(encoding="utf-8")
        conn.exec_driver_sql(
            sql.replace("needs.", f'"{schema}".')
            .replace("trend_radar.", f'"{schema}".')
            .replace("tubedepth.", f'"{td_schema}".')
        )
    engine.dispose()


@pytest.fixture
def health_rows(
    needs_schema: str,
    trend_radar_schema: str,
    tubedepth_side_schema: str,
    needs_runtime_url: str,
    _schema_name: str,
) -> list[tuple[Any, ...]]:
    """The role reading the view is needs_runtime -- the design is that it never touches the source
    tables directly and only reads the view."""
    _seed_and_create_view(needs_schema, _schema_name, tubedepth_side_schema)
    engine = create_engine(needs_runtime_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT {COLUMNS} FROM collector_health "
                "ORDER BY collector, dataset NULLS LAST, run_id NULLS LAST, started_at"
            )
        ).all()
    engine.dispose()
    return [tuple(r) for r in rows]


def test_all_three_arms_land_in_one_table_with_the_contracts_twelve_columns(
    health_rows: list[tuple[Any, ...]],
):
    assert [(r[0], r[1], r[2], r[3]) for r in health_rows] == [
        ("commerce", "rank", str(RUN_A), STARTED),
        ("commerce", "review", str(RUN_A), STARTED),
        ("commerce", None, str(RUN_B), STARTED_B),  # a run with no fetch_log has no dataset to name
        ("commerce", None, str(RUN_D), STARTED_B),  # the same even with every source skipped
        ("commerce", None, str(RUN_E), STARTED_B),
        ("naver", "blog", str(RUN_C), STARTED),
        # youtube has no run -- the run_id column is NULL and dataset + time bucket is what splits rows.
        ("youtube", "watch", None, YT_WATCH),
        ("youtube", "work", None, YT_BLOCKED),
        ("youtube", "work", None, YT_QUEUE),
        ("youtube", None, None, YT_LEGACY),  # jobs made before dataset was written down (#102)
    ]
    assert all(len(r) == 12 for r in health_rows)


def test_a_run_with_no_fetch_log_keeps_its_row_and_counts_zero(health_rows: list[tuple[Any, ...]]):
    # If the row vanished, "a run that ran but got nothing" would be invisible in the table entirely.
    matching = [r for r in health_rows if r[2] == str(RUN_B)]
    assert len(matching) == 1, f"fetch_log 없는 run 의 행이 {len(matching)} 개다"
    row = matching[0]
    assert (row[5], row[6], row[7], row[8], row[9]) == ("failed", 0, 0, 0, 0)
    assert row[11] is None  # no measured request, so no percentile either


def test_a_run_where_every_source_yielded_reads_yielded_not_partial(health_rows: list[tuple[Any, ...]]):
    # RUN_D is every source stepping back on its own, with nothing actually failing -- if it carried
    # the same value as RUN_E, where a source genuinely errored, the dashboard would show both the same
    # color as a false alarm.
    yielded = [r for r in health_rows if r[2] == str(RUN_D)]
    partial = [r for r in health_rows if r[2] == str(RUN_E)]
    assert len(yielded) == 1, yielded
    assert len(partial) == 1, partial
    assert yielded[0][5] == "yielded"
    assert partial[0][5] == "partial"


def test_403_and_429_count_as_blocked_and_2xx_as_ok(health_rows: list[tuple[Any, ...]]):
    ranked = [r for r in health_rows if (r[0], r[1]) == ("commerce", "rank")]
    assert len(ranked) == 1, ranked
    requests, ok, blocked, failed = ranked[0][6], ranked[0][7], ranked[0][8], ranked[0][9]
    assert (requests, ok, blocked, failed) == (8, 3, 2, 2)
    # 404 lands in none of the buckets -- the contract's three buckets are only 2xx / 403,429 / error,5xx.
    assert requests - ok - blocked - failed == 1
    naver = [r for r in health_rows if r[0] == "naver"]
    assert len(naver) == 1, naver
    assert (naver[0][6], naver[0][7], naver[0][8], naver[0][9]) == (3, 1, 1, 1)


def test_p90_ms_is_the_real_percentile_not_the_max_or_the_mean(health_rows: list[tuple[Any, ...]]):
    by_key = {(r[0], r[1]): r[11] for r in health_rows}
    # elapsed 10..80 (n=8): 0.9*(8-1)=6.3 -> 70 + 0.3*(80-70) = 73. max is 80, the mean is 45.
    assert by_key[("commerce", "rank")] == 73
    assert by_key[("commerce", "review")] == 5
    # naver has [100, 200] and one NULL: 0.9*(2-1)=0.9 -> 100 + 0.9*100 = 190.
    assert by_key[("naver", "blog")] == 190


def test_queued_is_null_on_the_batch_arms_and_a_count_on_the_one_arm_with_a_queue(
    health_rows: list[tuple[Any, ...]],
):
    # commerce and naver are batch workers a cron calls, so there is no such thing as a queue for them
    # at all -- it must be NULL, not 0, for "the queue is empty" to read differently from "there is no
    # queue" in the table.
    assert {r[10] for r in health_rows if r[0] != "youtube"} == {None}
    assert None not in {r[10] for r in health_rows if r[0] == "youtube"}


def test_youtube_tells_an_empty_queue_from_a_full_one(health_rows: list[tuple[Any, ...]]):
    # Completion bar: an empty-queue bucket must still leave a row with queued=0, or the full bucket's 2
    # has nothing to read as a contrast against.
    by_bucket = {r[3]: r for r in health_rows if r[0] == "youtube"}
    assert [by_bucket[b][10] for b in (YT_WATCH, YT_BLOCKED, YT_LEGACY)] == [0, 0, 0]
    full = by_bucket[YT_QUEUE]
    assert full[10] == 2
    # No one has claimed anything yet, so there is no measured request and no finish time either.
    assert (full[5], full[6], full[7], full[8], full[9], full[11]) == ("running", 0, 0, 0, 0, None)


def test_a_youtube_row_counts_quota_and_rate_limit_as_blocked_not_as_failed(
    health_rows: list[tuple[Any, ...]],
):
    # Quota exhaustion is this collector's real failure mode -- mixed into failed, the table can no
    # longer tell a block from a genuine failure.
    watch = next(r for r in health_rows if (r[0], r[3]) == ("youtube", YT_WATCH))
    # requests is the eight finished jobs. quota, rate_limited and http_403 are blocked (three), one
    # http_500 is failed, and one cancelled lands in neither bucket (the same spot commerce's 404 sits
    # in).
    assert (watch[6], watch[7], watch[8], watch[9]) == (8, 3, 3, 1)
    assert watch[6] - watch[7] - watch[8] - watch[9] == 1
    assert watch[5] == "partial"  # a bucket with both success and failure
    # A bucket where every failure is a block must have status read blocked too.
    blocked = next(r for r in health_rows if (r[0], r[3]) == ("youtube", YT_BLOCKED))
    assert (blocked[5], blocked[6], blocked[7], blocked[8], blocked[9]) == ("blocked", 3, 0, 3, 0)


def test_youtube_p90_skips_the_rows_that_predate_the_elapsed_ms_column(
    health_rows: list[tuple[Any, ...]],
):
    by_bucket = {r[3]: r for r in health_rows if r[0] == "youtube"}
    # elapsed 10..80 (n=8): the same arithmetic as commerce's rank row -- 0.9*(8-1)=6.3 -> 73.
    assert by_bucket[YT_WATCH][11] == 73
    # [1000, 2000] and one NULL: 0.9*(2-1)=0.9 -> 1900. Filling the NULL with 0 would give 1800.
    assert by_bucket[YT_BLOCKED][11] == 1900
    # A bucket with not a single measured value has no percentile either -- the same as a commerce run
    # with no fetch_log.
    assert by_bucket[YT_LEGACY][11] is None


def test_a_youtube_bucket_spans_one_hour_and_ends_when_its_last_job_did(
    health_rows: list[tuple[Any, ...]],
):
    # started_at is the bucket's own start time (corresponding to commerce's run start). A bucket
    # holding only unclaimed jobs must still have this value, or the table loses its time entirely --
    # that row has no real started_at at all.
    by_bucket = {r[3]: r for r in health_rows if r[0] == "youtube"}
    assert set(by_bucket) == {YT_WATCH, YT_BLOCKED, YT_LEGACY, YT_QUEUE}
    assert by_bucket[YT_WATCH][4] == YT_WATCH + timedelta(minutes=len(YT_WATCH_JOBS))
    assert by_bucket[YT_QUEUE][4] is None
    assert by_bucket[YT_LEGACY][5] == "ok"


def test_started_and_finished_come_from_the_run_row_not_from_a_neighbour(
    health_rows: list[tuple[Any, ...]],
):
    # This asserts on the two arms that have a run -- a youtube row has no run_id, its time comes from
    # the bucket.
    assert {(r[2], r[3], r[4]) for r in health_rows if r[2] is not None} == {
        (str(RUN_A), STARTED, FINISHED),
        (str(RUN_B), STARTED_B, FINISHED_B),
        (str(RUN_C), STARTED, FINISHED),
        (str(RUN_D), STARTED_B, FINISHED_B),
        (str(RUN_E), STARTED_B, FINISHED_B),
    }


# --- Deploy path: what db/migrate.sh actually leaves behind (tool/checks/test's throwaway container) ---


@pytest.fixture
def deployed() -> Any:
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET ROLE needs_owner"))
        yield conn
    engine.dispose()  # needs_migrator has CONNECTION LIMIT 2 -- always release, pass or fail.


def test_migrate_sh_leaves_the_view_in_the_needs_schema_for_needs_runtime(deployed: Any):
    """Even with a view file in place, if the deploy never applies it, production has none -- that is
    db/migrate.sh's stage (f)."""
    assert deployed.execute(text("SELECT to_regclass('needs.collector_health')")).scalar_one() is not None
    assert deployed.execute(
        text("SELECT has_table_privilege('needs_runtime', 'needs.collector_health', 'SELECT')")
    ).scalar_one()


def test_the_deployed_view_carries_the_contracts_columns_in_order(deployed: Any):
    contract = _contract_columns(ENTRYPOINTS_MD.read_text(encoding="utf-8"))
    assert len(contract) == 12, contract
    found = deployed.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'needs' AND table_name = 'collector_health' ORDER BY ordinal_position"
        )
    ).all()
    assert [(n, t) for n, t in found] == contract


# Asked by oid, not name -- if a role with no schema USAGE tried to resolve 'trend_radar.run',
# has_table_privilege would end in an exception, not an assertion.
_OID = (
    "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :s AND c.relname = :t"
)
_MAY_SELECT = "SELECT has_table_privilege(:role, cast(:oid AS oid), 'SELECT')"


def test_needs_runtime_reads_the_view_without_any_direct_grant_on_trend_radar(deployed: Any):
    # The view running with owner privilege is deliberate here -- the design only holds if these two
    # assertions are both true together.
    for table in ("run", "fetch_log"):
        oid = deployed.execute(text(_OID), {"s": "trend_radar", "t": table}).scalar_one()
        for role, expected in (("needs_owner", True), ("needs_runtime", False)):
            granted = deployed.execute(text(_MAY_SELECT), {"role": role, "oid": oid}).scalar_one()
            assert granted is expected, (role, table)
    runtime_url = os.environ.get("TEST_POSTGRES_RUNTIME_URL") or pytest.skip("run tool/checks/test")
    engine = create_engine(runtime_url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM needs.collector_health")).scalar_one() == 0
    engine.dispose()


def test_the_youtube_arm_gets_its_grant_on_tubedepth_jobs_from_the_deploy_path(deployed: Any):
    """Even with the view file reading tubedepth.jobs, the view never stands in production without the
    line that opens that SELECT in the deploy -- that line is the needs_owner block of
    db/grants/needs_runtime_reader.sql, and migrate.sh runs it as stage (e), before (f)'s view
    creation."""
    oid = deployed.execute(text(_OID), {"s": "tubedepth", "t": "jobs"}).scalar_one()
    for role, expected in (("needs_owner", True), ("needs_runtime", False)):
        granted = deployed.execute(text(_MAY_SELECT), {"role": role, "oid": oid}).scalar_one()
        assert granted is expected, role


# The contract section's sql fence is the one source of truth for a column's name, order and type.
_PG_TYPE = {"text": "text", "timestamptz": "timestamp with time zone", "int": "integer"}


def _contract_columns(md: str) -> list[tuple[str, str]]:
    block = re.search(r"## Common operations view[^\n]*\n```sql\n(.*?)\n```", md, re.DOTALL)
    assert block, "contracts/entrypoints.md §Common operations view has no sql fence"
    body = re.sub(r"--[^\n]*", "", block.group(1))
    pairs = [p.split() for p in body.replace("\n", " ").split(",") if p.strip()]
    return [(name, _PG_TYPE[kind]) for name, kind in pairs]


def test_the_contract_fence_still_names_twelve_columns_the_view_can_be_checked_against():
    assert _contract_columns(ENTRYPOINTS_MD.read_text(encoding="utf-8"))[:3] == [
        ("collector", "text"),
        ("dataset", "text"),
        ("run_id", "text"),
    ]
