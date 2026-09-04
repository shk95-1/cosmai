"""Whether one aggregate stage fits inside the runtime role's time limits (db/bootstrap.sql, in the form of
tests/test_ranking_scale.py).

This session tightens idle_in_transaction_session_timeout far below the production value: if the metric
computation runs inside a transaction the session is cut and the test fails. It passes only when the
computation is outside the transaction and the writes are batch commits.
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
# The runtime role's transaction_timeout is the real budget. Going past it rolls the whole batch back.
BUDGET_SECONDS = 60
# The production value is 15s — it is tightened further to pin down mechanically that the computation is
# outside the transaction.
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
            # Categories and need_keys are drawn so they do not divide each other — an overlap quietly lowers
            # the scope count.
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
        # If the computation is inside the transaction the session is cut here (OperationalError); that is a
        # failure, not a pass.
        cur.execute(f"SET idle_in_transaction_session_timeout = '{IDLE_LIMIT}'")
        conn.commit()
        started = time.perf_counter()
        run_id = run(conn, extractors=(EXTRACTOR,))
        elapsed = time.perf_counter() - started
        cur.execute("SELECT count(*) FROM metrics_need WHERE run_id = %s", (run_id,))
        row = cur.fetchone()

    assert row is not None and row[0] > 2000  # the batch (2000) boundary is really passed
    with capsys.disabled():
        print(
            f"\naggregate {AGGREGATE_VERSION}: {MENTIONS} mentions -> {row[0]} metrics_need "
            f"in {elapsed:.1f}s (budget {BUDGET_SECONDS}s, idle-in-tx limit {IDLE_LIMIT})"
        )
    assert elapsed < BUDGET_SECONDS
