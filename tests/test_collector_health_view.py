"""`needs.collector_health`: 계약 §공통 운영 뷰의 12컬럼을 commerce + naver + youtube 세 팔이 채운다.

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
STARTED_B = datetime(2026, 8, 23, 4, 0, tzinfo=UTC)  # RUN_B 는 제 시각을 갖는다 -- 옆 run 것이 새면 보인다
FINISHED_B = datetime(2026, 8, 23, 4, 5, tzinfo=UTC)
RUN_A = UUID("11111111-1111-4111-8111-111111111111")  # commerce, fetch_log 두 dataset
RUN_B = UUID("22222222-2222-4222-8222-222222222222")  # commerce, fetch_log 가 한 줄도 없는 run
RUN_C = UUID("33333333-3333-4333-8333-333333333333")  # naver
RUN_D = UUID("44444444-4444-4444-8444-444444444444")  # commerce, every source skipped (lock contention)
RUN_E = UUID("55555555-5555-4555-8555-555555555555")  # commerce, one source errored (a real partial)

# (dataset, http status, elapsed_ms, error). 404 는 계약이 세는 세 통(2xx / 403·429 / error·5xx)
# 어디에도 들어가지 않는다 — requests 와의 차이로만 드러나는 것이 의도다.
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
# elapsed_ms 가 NULL 인 줄이 섞여도 p90 은 남은 두 값의 백분위여야 한다.
NAVER_FETCHES = (
    ("blog", 200, 100, None),
    ("blog", 429, 200, None),
    ("blog", None, None, "timeout"),
)
COLUMNS = "collector, dataset, run_id, started_at, finished_at, status, requests, ok, blocked, failed, queued, p90_ms"  # noqa: E501

# youtube 팔의 한 행은 (dataset, 시간 버킷) 하나다 -- run 이 없는 수집기에서 commerce 의 run 에 해당하는
# "유한한 일감 묶음" 을 뷰가 시간으로 만든다. 아래 넷이 그 네 행이다.
YT_WATCH = datetime(2026, 8, 23, 1, 0, tzinfo=UTC)  # 다 끝난 버킷: 성공·차단·실패·취소가 섞였다
YT_BLOCKED = datetime(2026, 8, 23, 2, 0, tzinfo=UTC)  # 실패가 전부 쿼터 소진인 버킷
YT_LEGACY = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)  # #101·#102 이전 행: dataset 도 elapsed_ms 도 없다
YT_QUEUE = datetime(2026, 8, 23, 4, 0, tzinfo=UTC)  # 아직 하나도 claim 되지 않은 버킷 = 찬 큐

# (state, error_code, elapsed_ms). cancelled 하나는 일부러 있다 -- ok·blocked·failed 세 통 어디에도
# 들어가지 않는 몫이고, commerce 팔에서 404 가 앉는 자리와 같다.
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
# 셋째 줄은 elapsed_ms 를 갖기 전의 행이다 -- p90 이 그것을 0 으로 채우면 1900 아닌 1800 이 나온다.
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
    """`state='queued'` 인 job 은 claim 된 적이 없어 started_at 이 없고, 옛 행도 마찬가지다 --
    그래서 버킷은 `coalesce(started_at, created_at)` 이라야 두 경우가 다 제 시각에 앉는다."""
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
    """운영에서 이 파일을 적용하는 것은 db/migrate.sh (f) 이고 그때의 롤이 needs_owner 다."""
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
        # RUN_D 는 소스 전부가 잠금에 밀려 물러난 run 이고 RUN_E 는 소스 하나가 실제로 에러 난 run 이다 --
        # 둘 다 run.status 는 엔진이 똑같이 'partial' 로 쓴다 (collectors/commerce/cli.py), 그래서
        # collector_health 가 run_source.outcome 을 보지 않으면 뷰에서 구분이 안 된다.
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
        # 원천 표는 collectors/commerce 소유다 — 운영에서 이 SELECT 를 여는 것이
        # db/grants/needs_runtime_reader.sql 의 needs_owner 블록이다.
        conn.exec_driver_sql(
            f'GRANT SELECT ON "{schema}".run, "{schema}".fetch_log, "{schema}".run_source TO needs_owner'
        )
        # tubedepth 는 collectors/youtube 소유의 별도 스키마다 -- 운영에서 이 두 줄을 여는 것도 같은
        # db/grants/needs_runtime_reader.sql 의 needs_owner 블록이다.
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
    """뷰를 읽는 롤은 needs_runtime 이다 — 원천 표에는 직접 닿지 않고 뷰만 읽는다는 것이 설계다."""
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
        ("commerce", None, str(RUN_B), STARTED_B),  # fetch_log 가 없는 run 은 dataset 을 이름댈 수 없다
        ("commerce", None, str(RUN_D), STARTED_B),  # 소스 전부가 skipped 여도 마찬가지다
        ("commerce", None, str(RUN_E), STARTED_B),
        ("naver", "blog", str(RUN_C), STARTED),
        # youtube 는 run 이 없다 -- run_id 자리가 NULL 이고 행을 가르는 것이 dataset + 시간 버킷이다.
        ("youtube", "watch", None, YT_WATCH),
        ("youtube", "work", None, YT_BLOCKED),
        ("youtube", "work", None, YT_QUEUE),
        ("youtube", None, None, YT_LEGACY),  # dataset 을 적기 전(#102)에 만들어진 job 들
    ]
    assert all(len(r) == 12 for r in health_rows)


def test_a_run_with_no_fetch_log_keeps_its_row_and_counts_zero(health_rows: list[tuple[Any, ...]]):
    # 행이 사라지면 "돌았는데 아무것도 못 받은 run" 이 표에서 통째로 안 보인다.
    matching = [r for r in health_rows if r[2] == str(RUN_B)]
    assert len(matching) == 1, f"fetch_log 없는 run 의 행이 {len(matching)} 개다"
    row = matching[0]
    assert (row[5], row[6], row[7], row[8], row[9]) == ("failed", 0, 0, 0, 0)
    assert row[11] is None  # 잰 요청이 없으니 백분위도 없다


def test_a_run_where_every_source_yielded_reads_yielded_not_partial(health_rows: list[tuple[Any, ...]]):
    # RUN_D 는 소스 전부가 스스로 물러났을 뿐 아무것도 실패하지 않았다 -- 소스 하나가 실제로 에러 난
    # RUN_E 와 같은 값이면 대시보드에서 둘이 같은 색으로 보여 거짓 경보가 된다.
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
    # 404 는 어느 통에도 없다 — 계약의 세 통은 2xx / 403·429 / error·5xx 뿐이다.
    assert requests - ok - blocked - failed == 1
    naver = [r for r in health_rows if r[0] == "naver"]
    assert len(naver) == 1, naver
    assert (naver[0][6], naver[0][7], naver[0][8], naver[0][9]) == (3, 1, 1, 1)


def test_p90_ms_is_the_real_percentile_not_the_max_or_the_mean(health_rows: list[tuple[Any, ...]]):
    by_key = {(r[0], r[1]): r[11] for r in health_rows}
    # elapsed 10..80 (n=8): 0.9*(8-1)=6.3 → 70 + 0.3*(80-70) = 73. max 는 80, 평균은 45 다.
    assert by_key[("commerce", "rank")] == 73
    assert by_key[("commerce", "review")] == 5
    # naver 는 [100, 200] 과 NULL 하나: 0.9*(2-1)=0.9 → 100 + 0.9*100 = 190.
    assert by_key[("naver", "blog")] == 190


def test_queued_is_null_on_the_batch_arms_and_a_count_on_the_one_arm_with_a_queue(
    health_rows: list[tuple[Any, ...]],
):
    # commerce·naver 는 크론이 부르는 배치 워커라 큐가 아예 없다 -- 0 이 아니라 NULL 이어야 "큐가 비었다"
    # 와 "큐라는 것이 없다" 가 표에서 갈린다.
    assert {r[10] for r in health_rows if r[0] != "youtube"} == {None}
    assert None not in {r[10] for r in health_rows if r[0] == "youtube"}


def test_youtube_tells_an_empty_queue_from_a_full_one(health_rows: list[tuple[Any, ...]]):
    # 완료 기준: 큐가 빈 버킷도 행으로 남고 queued=0 이라야, 큐가 찬 버킷의 2 가 무엇에 대비되는지 읽힌다.
    by_bucket = {r[3]: r for r in health_rows if r[0] == "youtube"}
    assert [by_bucket[b][10] for b in (YT_WATCH, YT_BLOCKED, YT_LEGACY)] == [0, 0, 0]
    full = by_bucket[YT_QUEUE]
    assert full[10] == 2
    # 아직 아무도 claim 하지 않았으니 잰 요청이 하나도 없고, 끝난 시각도 없다.
    assert (full[5], full[6], full[7], full[8], full[9], full[11]) == ("running", 0, 0, 0, 0, None)


def test_a_youtube_row_counts_quota_and_rate_limit_as_blocked_not_as_failed(
    health_rows: list[tuple[Any, ...]],
):
    # 쿼터 소진은 이 수집기의 실제 고장 모드다 -- failed 에 섞이면 차단인지 진짜 고장인지 표에서 안 갈린다.
    watch = next(r for r in health_rows if (r[0], r[3]) == ("youtube", YT_WATCH))
    # requests 는 끝난 job 여덟. quota·rate_limited·http_403 셋이 blocked, http_500 하나가 failed,
    # cancelled 하나는 어느 통에도 없다 (commerce 의 404 자리).
    assert (watch[6], watch[7], watch[8], watch[9]) == (8, 3, 3, 1)
    assert watch[6] - watch[7] - watch[8] - watch[9] == 1
    assert watch[5] == "partial"  # 성공도 실패도 있는 버킷
    # 실패가 전부 차단인 버킷은 status 까지 blocked 여야 한다.
    blocked = next(r for r in health_rows if (r[0], r[3]) == ("youtube", YT_BLOCKED))
    assert (blocked[5], blocked[6], blocked[7], blocked[8], blocked[9]) == ("blocked", 3, 0, 3, 0)


def test_youtube_p90_skips_the_rows_that_predate_the_elapsed_ms_column(
    health_rows: list[tuple[Any, ...]],
):
    by_bucket = {r[3]: r for r in health_rows if r[0] == "youtube"}
    # elapsed 10..80 (n=8): commerce 의 rank 행과 같은 산수 -- 0.9*(8-1)=6.3 → 73.
    assert by_bucket[YT_WATCH][11] == 73
    # [1000, 2000] 과 NULL 하나: 0.9*(2-1)=0.9 → 1900. NULL 을 0 으로 채웠다면 1800 이 나온다.
    assert by_bucket[YT_BLOCKED][11] == 1900
    # 잰 값이 하나도 없는 버킷은 백분위도 없다 -- fetch_log 가 없는 commerce run 과 같다.
    assert by_bucket[YT_LEGACY][11] is None


def test_a_youtube_bucket_spans_one_hour_and_ends_when_its_last_job_did(
    health_rows: list[tuple[Any, ...]],
):
    # started_at 은 버킷의 시작 시각이다 (commerce 의 run 시작에 해당). 아직 claim 되지 않은 job 만 든
    # 버킷도 이 값이 있어야 표에서 시각을 잃지 않는다 -- 그 행에는 실제 started_at 이 하나도 없다.
    by_bucket = {r[3]: r for r in health_rows if r[0] == "youtube"}
    assert set(by_bucket) == {YT_WATCH, YT_BLOCKED, YT_LEGACY, YT_QUEUE}
    assert by_bucket[YT_WATCH][4] == YT_WATCH + timedelta(minutes=len(YT_WATCH_JOBS))
    assert by_bucket[YT_QUEUE][4] is None
    assert by_bucket[YT_LEGACY][5] == "ok"


def test_started_and_finished_come_from_the_run_row_not_from_a_neighbour(
    health_rows: list[tuple[Any, ...]],
):
    # run 을 가진 두 팔에 대한 단언이다 -- youtube 행은 run_id 가 없고 시각이 버킷에서 온다.
    assert {(r[2], r[3], r[4]) for r in health_rows if r[2] is not None} == {
        (str(RUN_A), STARTED, FINISHED),
        (str(RUN_B), STARTED_B, FINISHED_B),
        (str(RUN_C), STARTED, FINISHED),
        (str(RUN_D), STARTED_B, FINISHED_B),
        (str(RUN_E), STARTED_B, FINISHED_B),
    }


# --- 배포 경로: db/migrate.sh 가 실제로 남기는 것 (tool/checks/test 의 throwaway 컨테이너) ---


@pytest.fixture
def deployed() -> Any:
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET ROLE needs_owner"))
        yield conn
    engine.dispose()  # needs_migrator 는 CONNECTION LIMIT 2 다 — 통과든 실패든 놓아준다.


def test_migrate_sh_leaves_the_view_in_the_needs_schema_for_needs_runtime(deployed: Any):
    """뷰 파일이 있어도 배포가 적용하지 않으면 운영에는 없는 것이다 — db/migrate.sh 의 (f) 단계."""
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


# 이름이 아니라 oid 로 묻는다 — 스키마 USAGE 가 없는 롤이 'trend_radar.run' 을 풀려고 하면
# has_table_privilege 가 단언이 아니라 예외로 끝난다.
_OID = (
    "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :s AND c.relname = :t"
)
_MAY_SELECT = "SELECT has_table_privilege(:role, cast(:oid AS oid), 'SELECT')"


def test_needs_runtime_reads_the_view_without_any_direct_grant_on_trend_radar(deployed: Any):
    # 뷰가 소유자 권한으로 도는 것이 여기서는 의도다 — 이 두 단언이 같이 참이어야 그 설계가 성립한다.
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
    """뷰 파일이 tubedepth.jobs 를 읽어도 그 SELECT 를 여는 줄이 배포에 없으면 운영에서 뷰가 서지
    않는다 — db/grants/needs_runtime_reader.sql 의 needs_owner 블록이 그 줄이고 migrate.sh 는
    (e) 로 그것을 (f) 의 뷰 생성보다 먼저 돌린다."""
    oid = deployed.execute(text(_OID), {"s": "tubedepth", "t": "jobs"}).scalar_one()
    for role, expected in (("needs_owner", True), ("needs_runtime", False)):
        granted = deployed.execute(text(_MAY_SELECT), {"role": role, "oid": oid}).scalar_one()
        assert granted is expected, role


# 계약 절의 sql 펜스가 컬럼의 이름·순서·타입에 대한 유일한 출처다.
_PG_TYPE = {"text": "text", "timestamptz": "timestamp with time zone", "int": "integer"}


def _contract_columns(md: str) -> list[tuple[str, str]]:
    block = re.search(r"## 공통 운영 뷰[^\n]*\n```sql\n(.*?)\n```", md, re.DOTALL)
    assert block, "contracts/entrypoints.md §공통 운영 뷰 에 sql 펜스가 없다"
    body = re.sub(r"--[^\n]*", "", block.group(1))
    pairs = [p.split() for p in body.replace("\n", " ").split(",") if p.strip()]
    return [(name, _PG_TYPE[kind]) for name, kind in pairs]


def test_the_contract_fence_still_names_twelve_columns_the_view_can_be_checked_against():
    assert _contract_columns(ENTRYPOINTS_MD.read_text(encoding="utf-8"))[:3] == [
        ("collector", "text"),
        ("dataset", "text"),
        ("run_id", "text"),
    ]
