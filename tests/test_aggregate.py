"""RuleAggregator 의 수식 — contracts/interfaces.md §수식 과 슬라이스 규칙을 합성 입력으로 고정한다."""

from __future__ import annotations

from datetime import date

from analysis.aggregate import LIKE_CAP, RuleAggregator
from analysis.types import DenominatorRow, NeedMentionRow, WishMentionRow


def need(
    need_key: str,
    polarity: str,
    *,
    src: str = "review",
    category: str = "선블록",
    ref: str = "p/1",
    product: str | None = "oy:p",
    month: str = "2026-01",
    rating: float | None = None,
    strength: float | None = None,
    scope: str | None = "generic",
) -> NeedMentionRow:
    return NeedMentionRow(
        src=src,
        site="oliveyoung",
        ref=ref,
        product_ref=product,
        source_product_key=ref.split("/", 1)[0],
        category=category,
        lexicon_category=category,
        need_key=need_key,
        aspect_scope=scope,
        polarity=polarity,
        strength=strength,
        rating=rating,
        observed_at=date(2026, 1, 1),
        observed_at_resolution="month",
        month=month,
        sentence=f"{need_key}-{ref}-{polarity}",
        kind=None,
        marker=None,
        polarity_reason=None,
        extractor_version="t",
        polarity_version="t",
    )


def denom(
    product_key: str, *, category: str = "선블록", low: int = 10, site: int = 1000, complete: bool = True
) -> DenominatorRow:
    return DenominatorRow(
        source="oliveyoung",
        product_key=product_key,
        captured_at=date(2026, 8, 23),
        category=category,
        site_review_count=site,
        low_collected=low,
        low_complete=complete,
        site_low_est=site // 10,
    )


def wish(
    wish_class: str,
    *,
    ref: str = "v/c",
    video: str = "v",
    channel: str | None = "ch",
    month: str = "2026-01",
    fmt: str | None = None,
    attribute: str | None = None,
    brand: str | None = None,
    like: int | None = 0,
    sentence: str = "s",
) -> WishMentionRow:
    return WishMentionRow(
        src="yt_comment",
        ref=ref,
        video_id=video,
        channel_id=channel,
        channel_is_brand_owner=None,
        product_ref=None,
        observed_at=date.fromisoformat(f"{month}-01"),
        observed_at_resolution="month" if month >= "2025-09" else "year",
        month=month,
        wish_class=wish_class,
        brand=brand,
        format=fmt,
        attribute=attribute,
        marker=None,
        sentence=sentence,
        like_count=like,
        extractor_version="t",
    )


def by_key(rows):
    return {r.need_key: r for r in rows}


def test_reviews_and_comments_are_counted_on_separate_axes():
    rows = by_key(
        RuleAggregator().need_metrics(
            [
                need("밀림", "불만"),
                need("밀림", "만족", ref="p/2"),
                need("밀림", "불만", src="yt_comment", ref="v/1", product=None),
            ],
            [],
            "선블록",
        )
    )
    assert (rows["밀림"].neg, rows["밀림"].pos) == (1, 1)
    assert (rows["밀림"].yt_neg, rows["밀림"].yt_pos) == (1, 0)
    assert rows["밀림"].unresolved == 0.5


def test_persistence_is_a_share_of_the_whole_scope_not_of_the_need_key():
    rows = by_key(
        RuleAggregator().need_metrics(
            [
                need("밀림", "불만", ref="a/1", product="oy:a", month="2026-01"),
                need("밀림", "불만", ref="a/2", product="oy:a", month="2026-02"),
                need("백탁", "불만", ref="b/1", product="oy:b", month="2026-03"),
            ],
            [],
            "선블록",
        )
    )
    assert (rows["밀림"].persist_months, rows["밀림"].persist_months_total) == (2, 3)
    assert (rows["밀림"].persist_products, rows["밀림"].persist_products_total) == (1, 2)


def test_the_denominator_supplies_the_product_universe_including_silent_products():
    rows = by_key(
        RuleAggregator().need_metrics(
            [need("밀림", "불만", ref="a/1", product=None)],
            [denom("a"), denom("b"), denom("c", category="크림")],
            "선블록",
        )
    )
    assert (rows["밀림"].persist_products, rows["밀림"].persist_products_total) == (1, 2)


def test_low_share_counts_low_rated_reviews_on_complete_products_whatever_the_polarity():
    rows = by_key(
        RuleAggregator().need_metrics(
            [
                need("밀림", "불만", ref="a/1", rating=1),
                need("밀림", "중립", ref="a/2", rating=2),
                need("밀림", "불만", ref="a/3", rating=5),
                need("밀림", "불만", ref="b/1", rating=1),
            ],
            [denom("a", low=10, site=1000), denom("b", low=7, site=500, complete=False)],
            "선블록",
        )
    )
    row = rows["밀림"]
    assert (row.low_mentioning, row.denom_low, row.denom_site) == (2, 10, 1000)
    assert row.low_share == 0.2
    # interfaces.md: 100 * (low_mentioning/denom_low) * site_low_pct, site_low_pct = 100/1000.
    assert row.population_share_pct == 2.0


def test_the_all_rollup_folds_synonyms_onto_the_canonical_need_key():
    rows = by_key(
        RuleAggregator(canonical={"끈적임": "끈적유분"}).need_metrics(
            [
                need("끈적임", "불만", category="크림", ref="a/1"),
                need("끈적유분", "불만", category="선블록", ref="b/1"),
            ],
            [],
            "all",
        )
    )
    assert rows["끈적유분"].neg == 2


def test_wish_metrics_splits_the_cross_tab_from_its_margins():
    wishes = [
        wish("a", ref="v/1", fmt="쿠션", attribute="지속력", like=5),
        wish("a", ref="v/2", fmt="쿠션", attribute=None, like=200),
        wish("b", ref="v/3", brand="구달", like=1),
    ]
    marginal = {(r.format, r.attribute, r.brand): r for r in RuleAggregator().wish_metrics(wishes, "wish:a")}
    cross = {(r.format, r.attribute): r for r in RuleAggregator().wish_metrics(wishes, "wish:a:format×attr")}
    assert marginal[("쿠션", "", "")].mentions == 2
    assert marginal[("", "지속력", "")].mentions == 1
    assert cross[("쿠션", "지속력")].mentions == 1
    assert cross[("쿠션", "")].mentions == 1
    assert [r.brand for r in RuleAggregator().wish_metrics(wishes, "wish:b")] == ["구달"]


def test_like_cap_bounds_one_loud_comment():
    rows = RuleAggregator().wish_metrics(
        [
            wish("a", ref="v/1", fmt="쿠션", like=500, sentence="큰 것"),
            wish("a", ref="v/2", fmt="쿠션", like=3, month="2025-01"),
        ],
        "wish:a",
    )
    row = next(r for r in rows if r.format == "쿠션")
    assert (row.like_sum, row.like_cap_sum, row.max_like) == (503, LIKE_CAP + 3, 500)
    assert row.example == "큰 것"
    # 2025-01 은 상대시각 복원분(resolution='year')이라 존재 월로 세지 않는다.
    assert (row.months_present, row.first_month, row.last_month) == (1, "2025-01", "2026-01")
    assert (row.videos, row.channels) == (1, 1)
