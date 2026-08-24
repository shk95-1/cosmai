"""rank_daily · price_event · product_denominator 의 파생 규칙 (slice-p2 / slice-p1 수식)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from analysis.aggregate import AGGREGATE_VERSION
from analysis.aggregate.ranking import (
    ABSENT_RANK,
    LOW_COMPLETE_THRESHOLD,
    PricePoint,
    RankSnapshot,
    ReviewRow,
    ReviewStatsRow,
    denominators,
    price_events,
    rank_daily,
    scope_threshold,
)

VERSION = AGGREGATE_VERSION


def snap(hour: int, rank: int, *, product: str = "p", board: str = "b", price: int | None = None):
    return RankSnapshot(
        source="oliveyoung",
        board=board,
        category_key="c",
        product_key=product,
        captured_at=datetime(2026, 8, 20, tzinfo=UTC) + timedelta(hours=hour),
        rank=rank,
        price=price,
        category_name="화장품 > 선케어 > 선블록",
    )


def test_the_day_boundary_is_kst_not_utc():
    rows = rank_daily([snap(14, 3), snap(15, 5)], VERSION)
    assert sorted(r.day_kst for r in rows) == [date(2026, 8, 20), date(2026, 8, 21)]


def test_a_day_row_carries_both_the_board_snapshots_and_the_product_appearances():
    rows = rank_daily([snap(0, 3, price=1000), snap(1, 7, price=1000), snap(1, 9, product="q")], VERSION)
    mine = next(r for r in rows if r.product_key == "p")
    assert (mine.n_snapshots, mine.n_present, mine.present_share) == (2, 2, 1.0)
    assert (mine.rank_mean, mine.rank_min, mine.rank_max, mine.price_mode) == (5.0, 3, 7, 1000)
    other = next(r for r in rows if r.product_key == "q")
    assert (other.n_snapshots, other.n_present, other.present_share) == (2, 1, 0.5)


def test_a_price_change_is_measured_against_the_ranks_around_it():
    snapshots = [snap(h, 10) for h in range(0, 6)] + [snap(h, 4) for h in range(6, 30)]
    prices = [
        PricePoint("oliveyoung", "p", datetime(2026, 8, 20, tzinfo=UTC), 10000),
        PricePoint("oliveyoung", "p", datetime(2026, 8, 20, 6, tzinfo=UTC), 8000),
    ]
    (event,) = price_events(prices, snapshots, VERSION)
    assert (event.price_before, event.price_after, event.direction) == (10000, 8000, "drop")
    assert (event.pct, event.rank_pre6, event.rank_post6) == (-20.0, 10.0, 4.0)
    assert (event.n_pre, event.n_post24) == (6, 24)


def test_a_product_missing_from_a_board_snapshot_is_ranked_at_the_penalty():
    snapshots = [snap(h, 10) for h in range(0, 6)] + [snap(h, 4, product="q") for h in range(6, 12)]
    prices = [
        PricePoint("oliveyoung", "p", datetime(2026, 8, 20, tzinfo=UTC), 10000),
        PricePoint("oliveyoung", "p", datetime(2026, 8, 20, 6, tzinfo=UTC), 8000),
    ]
    (event,) = price_events(prices, snapshots, VERSION)
    assert event.rank_post6 == float(ABSENT_RANK)


def test_the_low_complete_threshold_is_the_collector_sample_ceiling():
    assert LOW_COMPLETE_THRESHOLD == scope_threshold()


def test_low_reviews_are_complete_when_a_three_star_turns_up_in_the_ascending_sample():
    reviews = [ReviewRow("oliveyoung", "p", f"r{i}", 1.0) for i in range(LOW_COMPLETE_THRESHOLD)]
    reviews += [ReviewRow("oliveyoung", "q", f"s{i}", 1.0) for i in range(LOW_COMPLETE_THRESHOLD)]
    reviews += [ReviewRow("oliveyoung", "q", "s3", 3.0)]
    stats = [ReviewStatsRow("oliveyoung", "p", 1000, 3, 2), ReviewStatsRow("oliveyoung", "q", 500, 0, 0)]
    rows = {
        r.product_key: r
        for r in denominators(
            reviews,
            stats,
            {("oliveyoung", "p"): "화장품 > 선케어 > 선블록"},
            date(2026, 8, 23),
            VERSION,
        )
    }
    assert (rows["p"].low_complete, rows["q"].low_complete) == (False, True)
    assert (rows["p"].category, rows["q"].category) == ("선블록", None)
    assert (rows["p"].low_collected, rows["p"].site_review_count, rows["p"].site_low_est) == (150, 1000, 50)
