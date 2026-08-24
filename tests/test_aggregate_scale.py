"""aggregate 한 단계가 런타임 롤의 시간 제한 안에 드는가 (db/bootstrap.sql, tests/test_ranking_scale.py 형).

이 세션은 idle_in_transaction_session_timeout 을 운영값보다 훨씬 짧게 조인다: 메트릭 계산이 트랜잭션
안에서 돌면 세션이 끊겨 실패한다. 계산은 트랜잭션 밖, 쓰기는 배치 커밋이어야 통과한다.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest

from analysis.aggregate import AGGREGATE_VERSION
from analysis.aggregate.pipeline import run
from db.seed._common import connect

pytestmark = pytest.mark.postgres

CATEGORIES = 40
NEED_KEYS = 60
PRODUCTS = 40
MONTHS = 6
MENTIONS = 50_000
EXTRACTOR = "scale-v1"
BASE = date(2026, 3, 1)
# 런타임 롤의 transaction_timeout 이 실제 예산이다. 넘기면 배치가 통째로 롤백된다.
BUDGET_SECONDS = 60
# 운영값은 15s 다 — 계산이 트랜잭션 밖이라는 것을 기계적으로 못 박으려고 더 조인다.
IDLE_LIMIT = "1s"


def _load(cur: psycopg.Cursor[Any]) -> None:
    with cur.copy(
        "COPY need_mention (src, site, ref, source_product_key, category, lexicon_category, need_key, "
        "polarity, strength, rating, observed_at, observed_at_resolution, month, sentence, "
        "extractor_version, polarity_version) FROM STDIN"
    ) as copy:
        for i in range(MENTIONS):
            month = BASE + timedelta(days=31 * (i % MONTHS))
            product = f"p{i % PRODUCTS}"
            # 카테고리와 need_key 를 서로 나눠 떨어지지 않게 뽑는다 — 겹치면 scope 수가 조용히 줄어든다.
            category = f"카테고리{i % CATEGORIES}"
            copy.write_row(
                ("review", "oliveyoung", f"{product}/r{i}", product, category, category,
                 f"니즈{(i // CATEGORIES) % NEED_KEYS}", "불만" if i % 3 else "만족",
                 0.8, 1.0 if i % 3 else 5.0, month, "day", month.strftime("%Y-%m"), f"문장 {i}",
                 EXTRACTOR, EXTRACTOR)
            )  # fmt: skip


def test_seed_scale_aggregate_fits_the_runtime_budget(needs_runtime_url: str, capsys):
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        _load(cur)
        conn.commit()
        # 계산이 트랜잭션 안에 있으면 여기서 세션이 끊긴다 (OperationalError), 통과가 아니라 실패다.
        cur.execute(f"SET idle_in_transaction_session_timeout = '{IDLE_LIMIT}'")
        conn.commit()
        started = time.perf_counter()
        run_id = run(conn, extractors=(EXTRACTOR,))
        elapsed = time.perf_counter() - started
        cur.execute("SELECT count(*) FROM metrics_need WHERE run_id = %s", (run_id,))
        row = cur.fetchone()

    assert row is not None and row[0] > 2000  # 배치(2000) 경계를 실제로 넘긴다
    with capsys.disabled():
        print(
            f"\naggregate {AGGREGATE_VERSION}: {MENTIONS} mentions -> {row[0]} metrics_need "
            f"in {elapsed:.1f}s (budget {BUDGET_SECONDS}s, idle-in-tx limit {IDLE_LIMIT})"
        )
    assert elapsed < BUDGET_SECONDS
