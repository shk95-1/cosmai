"""Whether `analyze aggregate` and the ranking derivations are idempotent on a real schema
(contracts/entrypoints.md)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from analysis.aggregate import AGGREGATE_VERSION
from analysis.aggregate.pipeline import run
from analysis.aggregate.ranking import run_ranking
from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

VERSION = AGGREGATE_VERSION
CAPTURED_AT = date(2026, 8, 23)
SEEDED_METRICS_NEED = 346
SEEDED_METRICS_WISH = 601
POPULATION = ("slice-p1", "slice-p9")


def _snapshots():
    base = datetime(2026, 8, 20, tzinfo=UTC)
    return [
        ("oliveyoung", "suncare", "c1", product, base + timedelta(hours=h), "화장품 > 선케어 > 선블록",
         rank, "이름", 12000)
        for product, ranks in (("p", [3, 4, 4, 5, 5, 5, 2]), ("q", [9, 9]))
        for h, rank in enumerate(ranks)
    ]  # fmt: skip


def _dump(cur, table: str, columns: str) -> list:
    order = ", ".join(str(i + 1) for i in range(len(columns.split(","))))
    cur.execute(f"SELECT {columns} FROM {table} ORDER BY {order}")  # noqa: S608 - constants of this module
    return cur.fetchall()


def test_ranking_derivations_upsert_the_same_rows_on_a_second_run(
    needs_runtime_url: str, trend_radar_schema: str, database_url_for_tests: str
):
    # The source schema is read-only — the owning role inserts the input, and the derivation has
    # needs_runtime read it with SELECT alone.
    with connect(database_url_for_tests) as source, source.cursor() as cur:
        cur.executemany(
            "INSERT INTO rank_snapshot (source, board, category_key, product_key, captured_at, "
            "category_name, rank, product_name, price) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            _snapshots(),
        )
        cur.executemany(
            "INSERT INTO price_point (source, product_key, captured_at, price) VALUES (%s,%s,%s,%s)",
            [
                ("oliveyoung", "p", datetime(2026, 8, 20, tzinfo=UTC), 12000),
                ("oliveyoung", "p", datetime(2026, 8, 20, 3, tzinfo=UTC), 9000),
            ],
        )
        cur.executemany(
            "INSERT INTO review (source, review_key, captured_at, product_key, rating) "
            "VALUES (%s,%s,%s,%s,%s)",
            [("oliveyoung", f"r{i}", datetime(2026, 8, 20, tzinfo=UTC), "p", 1.0) for i in range(4)],
        )
        cur.execute(
            "INSERT INTO review_stats (source, product_key, captured_at, review_count, pct_1, pct_2) "
            "VALUES ('oliveyoung', 'p', %s, 1000, 3, 2)",
            (datetime(2026, 8, 20, tzinfo=UTC),),
        )
        cur.execute("GRANT SELECT ON rank_snapshot, price_point, review, review_stats TO needs_runtime")
        source.commit()

    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        first = run_ranking(conn, VERSION, CAPTURED_AT, source_schema="")
        daily = _dump(cur, "rank_daily", "product_key, day_kst, n_snapshots, n_present, rank_min")
        events = _dump(cur, "price_event", "product_key, board, t_change, direction, n_pre, n_post24")
        denoms = _dump(cur, "product_denominator", "product_key, category, low_collected, low_complete")

        # The second run goes through the stage entry point — the derivation writes the same rows again
        # inside run() too.
        run(conn, commerce_schema="", captured_at=CAPTURED_AT, extractors=())
        second = run_ranking(conn, VERSION, CAPTURED_AT, source_schema="")
        assert first == second
        assert _dump(cur, "rank_daily", "product_key, day_kst, n_snapshots, n_present, rank_min") == daily
        assert _dump(cur, "price_event", "product_key, board, t_change, direction, n_pre, n_post24") == events
        assert (
            _dump(cur, "product_denominator", "product_key, category, low_collected, low_complete") == denoms
        )
    # 'q' is in only 2 of the 7 snapshots; an absence shows only in present_share (A16).
    assert [(r[0], r[3]) for r in daily] == [("p", 7), ("q", 2)]
    assert [(r[3], r[4]) for r in events] == [("drop", 3)]
    # 사이트가 발행한 경로 그대로다 — leaf 로 자르면 metrics_need.scope 와 만나지 못한다
    # (contracts/formats.md §카테고리 표기, #123).
    assert denoms == [("p", "화장품 > 선케어 > 선블록", 4, True)]


def test_analyze_aggregate_writes_one_run_and_repeats_it(needs_runtime_url: str):
    seed.run_all(needs_runtime_url)
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        run_id = run(conn, extractors=POPULATION)
        cur.execute(
            "SELECT status, finished_at IS NOT NULL, versions->>'aggregate', versions->>'extractor' "
            "FROM analysis_run WHERE run_id = %s",
            (run_id,),
        )
        assert cur.fetchone() == ("ok", True, VERSION, ";".join(POPULATION))
        needs = _dump(cur, "metrics_need", "scope, need_key, month, product_ref, neg, pos")
        wishes = _dump(cur, "metrics_wish", "scope, format, attribute, brand, mentions")

        assert run(conn, extractors=POPULATION) == run_id
        assert _dump(cur, "metrics_need", "scope, need_key, month, product_ref, neg, pos") == needs
        assert _dump(cur, "metrics_wish", "scope, format, attribute, brand, mentions") == wishes
        # The analysis creates a new run — the rows of the seed run are not touched (stage-1 decision 4).
        cur.execute("SELECT count(*) FROM metrics_need WHERE run_id <> %s", (run_id,))
        assert cur.fetchone() == (SEEDED_METRICS_NEED,)
        cur.execute("SELECT count(*) FROM metrics_wish WHERE run_id <> %s", (run_id,))
        assert cur.fetchone() == (SEEDED_METRICS_WISH,)
        cur.execute("SELECT count(*) FROM metrics_need WHERE run_id = %s", (run_id,))
        mine = cur.fetchone()

        # `analyze all` of #5 creates the run itself and hands it over: writes go to that run, and #5 closes
        # the status.
        cur.execute(
            "INSERT INTO analysis_run (status, versions, note) "
            "VALUES ('running', '{}'::jsonb, 'all') RETURNING run_id"
        )
        borrowed = cur.fetchone()
        assert borrowed is not None
        conn.commit()
        assert run(conn, run_id=borrowed[0], extractors=POPULATION) == borrowed[0]
        cur.execute("SELECT status, finished_at FROM analysis_run WHERE run_id = %s", (borrowed[0],))
        assert cur.fetchone() == ("running", None)
        cur.execute("SELECT count(*) FROM metrics_need WHERE run_id = %s", (borrowed[0],))
        assert cur.fetchone() == mine


def test_the_run_aggregates_only_the_population_it_was_given(needs_runtime_url: str):
    seed.run_all(needs_runtime_url)
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        # 시드 need_mention 은 두 슬라이스를 담고 '선블록' 은 양쪽에 다 있다 — 이름을 대지 않으면 거절한다.
        with pytest.raises(ValueError):
            run(conn)
        conn.rollback()
        run_id = run(conn, scope="선블록", extractors=("slice-suncare",))
        # Category total rows are compared with each other — the same run also emits product-axis rows (#41).
        cur.execute(
            "SELECT need_key, neg, pos FROM metrics_need WHERE run_id = %s AND month = '' "
            "AND product_ref = '' ORDER BY need_key",
            (run_id,),
        )
        got = cur.fetchall()
        cur.execute(
            "SELECT need_key, neg, pos FROM metrics_need WHERE month = '' AND product_ref = '' "
            "AND run_id = (SELECT run_id FROM analysis_run WHERE note = 'seed:slice-suncare') "
            "ORDER BY need_key"
        )
        assert got == cur.fetchall()
        # That population is carried onto the product axis as it is — those are the rows screen 3 reads.
        cur.execute("SELECT count(*) FROM metrics_need WHERE run_id = %s AND product_ref <> ''", (run_id,))
        per_product = cur.fetchone()
        assert per_product is not None and per_product[0] > 0
