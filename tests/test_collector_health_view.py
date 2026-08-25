"""`needs.collector_health`: 계약 §공통 운영 뷰의 12컬럼을 commerce + naver 두 팔이 채운다.

그 계약은 contracts/entrypoints.md 의 절이고, youtube 는 그 절의 각주대로 3단계에서
빠져 있다 — 여기서 검사하는 팔은 둘뿐이다.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

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


def _seed_and_create_view(url: str, schema: str) -> None:
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
        conn.exec_driver_sql(sql.replace("needs.", f'"{schema}".').replace("trend_radar.", f'"{schema}".'))
    engine.dispose()


@pytest.fixture
def health_rows(
    needs_schema: str, trend_radar_schema: str, needs_runtime_url: str, _schema_name: str
) -> list[tuple[Any, ...]]:
    """뷰를 읽는 롤은 needs_runtime 이다 — 원천 표에는 직접 닿지 않고 뷰만 읽는다는 것이 설계다."""
    _seed_and_create_view(needs_schema, _schema_name)
    engine = create_engine(needs_runtime_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT {COLUMNS} FROM collector_health ORDER BY collector, dataset NULLS LAST, run_id")
        ).all()
    engine.dispose()
    return [tuple(r) for r in rows]


def test_both_arms_land_in_one_table_with_the_contracts_twelve_columns(health_rows: list[tuple[Any, ...]]):
    assert [(r[0], r[1], r[2]) for r in health_rows] == [
        ("commerce", "rank", str(RUN_A)),
        ("commerce", "review", str(RUN_A)),
        ("commerce", None, str(RUN_B)),  # fetch_log 가 없는 run 은 dataset 을 이름댈 수 없다
        ("commerce", None, str(RUN_D)),  # 소스 전부가 skipped 여도 마찬가지다
        ("commerce", None, str(RUN_E)),
        ("naver", "blog", str(RUN_C)),
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


def test_queued_is_null_on_every_row_because_neither_arm_has_a_queue(health_rows: list[tuple[Any, ...]]):
    assert [r[10] for r in health_rows] == [None] * len(health_rows)


def test_started_and_finished_come_from_the_run_row_not_from_a_neighbour(
    health_rows: list[tuple[Any, ...]],
):
    assert {(r[2], r[3], r[4]) for r in health_rows} == {
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
    "WHERE n.nspname = 'trend_radar' AND c.relname = :t"
)
_MAY_SELECT = "SELECT has_table_privilege(:role, cast(:oid AS oid), 'SELECT')"


def test_needs_runtime_reads_the_view_without_any_direct_grant_on_trend_radar(deployed: Any):
    # 뷰가 소유자 권한으로 도는 것이 여기서는 의도다 — 이 두 단언이 같이 참이어야 그 설계가 성립한다.
    for table in ("run", "fetch_log"):
        oid = deployed.execute(text(_OID), {"t": table}).scalar_one()
        for role, expected in (("needs_owner", True), ("needs_runtime", False)):
            granted = deployed.execute(text(_MAY_SELECT), {"role": role, "oid": oid}).scalar_one()
            assert granted is expected, (role, table)
    runtime_url = os.environ.get("TEST_POSTGRES_RUNTIME_URL") or pytest.skip("run tool/checks/test")
    engine = create_engine(runtime_url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM needs.collector_health")).scalar_one() == 0
    engine.dispose()


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
