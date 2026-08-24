"""시드 규모의 rank_snapshot 에서 run_ranking 이 런타임 롤의 시간 제한 안에 드는가 (db/bootstrap.sql)."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta

import pytest

from analysis.aggregate import AGGREGATE_VERSION
from analysis.aggregate.ranking import run_ranking
from db.seed._common import connect

pytestmark = pytest.mark.postgres

# slice-p2-ranking-dynamics/README.md 실측: rank_snapshot 137,429 → rank_daily 17,948 · price_point 116,292.
BOARDS = 13
PRODUCTS = 100
DAYS = 14
PER_DAY = 7
PRICED_PRODUCTS = 1000
PRICE_POINTS = 116
EXPECTED_DAILY = BOARDS * PRODUCTS * DAYS
BASE = datetime(2026, 6, 1, tzinfo=UTC)
VERSION = AGGREGATE_VERSION
# 런타임 롤의 transaction_timeout. 넘기면 배치가 통째로 롤백되므로 이것이 실제 예산이다.
BUDGET_SECONDS = 60


def _load(cur) -> None:
    with cur.copy(
        "COPY rank_snapshot (source, board, category_key, product_key, captured_at, category_name, "
        "rank, product_name, price) FROM STDIN"
    ) as copy:
        for board in range(BOARDS):
            for day in range(DAYS):
                for slot in range(PER_DAY):
                    at = BASE + timedelta(days=day, hours=slot * 2)
                    for product in range(PRODUCTS):
                        copy.write_row(
                            ("oliveyoung", f"b{board}", f"c{board}", f"p{product}", at,
                             "화장품 > 선케어 > 선블록", product + 1, f"이름{product}", 10000)
                        )  # fmt: skip
    with cur.copy("COPY price_point (source, product_key, captured_at, price) FROM STDIN") as copy:
        for product in range(PRICED_PRODUCTS):
            for point in range(PRICE_POINTS):
                changed = product < PRODUCTS and point >= PRICE_POINTS // 2
                copy.write_row(
                    ("oliveyoung", f"p{product}", BASE + timedelta(hours=point * 3),
                     9000 if changed else 10000)
                )  # fmt: skip


def test_seed_scale_ranking_fits_the_runtime_budget(
    needs_runtime_url: str, trend_radar_schema: str, database_url_for_tests: str, capsys
):
    with connect(database_url_for_tests) as source, source.cursor() as cur:
        _load(cur)
        cur.execute("GRANT SELECT ON rank_snapshot, price_point, review, review_stats TO needs_runtime")
        source.commit()

    with connect(needs_runtime_url) as conn:
        started = time.perf_counter()
        counts = run_ranking(conn, VERSION, date(2026, 8, 23), source_schema="")
        elapsed = time.perf_counter() - started

    with capsys.disabled():
        print(f"\nrun_ranking {counts} in {elapsed:.1f}s (budget {BUDGET_SECONDS}s)")
    assert counts["rank_daily"] == EXPECTED_DAILY
    assert counts["price_event"] == BOARDS * PRODUCTS
    assert elapsed < BUDGET_SECONDS
