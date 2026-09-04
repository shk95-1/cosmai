"""RuleAggregator 의 수식 — contracts/interfaces.md §수식 과 슬라이스 규칙을 합성 입력으로 고정한다."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from analysis.aggregate import LIKE_CAP, RuleAggregator
from analysis.aggregate.pipeline import scopes_for
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
        # The same rule as wish() — a YouTube timestamp before 2025-09 is restored from relative time, so
        # its month cannot be trusted (formats.md). While this helper was pinned to 'month', the month-axis
        # defect of #129 could not be reproduced.
        observed_at_resolution="month" if month >= "2025-09" else "year",
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
    """Whole-period, category-total rows only — the product axis (#41) varies product_ref and the month axis
    (#129) varies month, so both emit the same need_key again. Without filtering both axes out the dict
    overwrites the total row with them."""
    return {r.need_key: r for r in rows if r.product_ref == "" and r.month == ""}


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


def test_the_aspectless_sentinel_is_excluded_from_the_need_metrics():
    """B8: need_key='' stays in need_mention but drops out of the metrics_need aggregation (formats.md)."""
    mentions = [
        need("밀림", "불만", ref="a/1", product="oy:a", month="2026-01"),
        need("", "불만", ref="b/1", product="oy:b", month="2026-02", rating=1),
        need("", "만족", ref="b/2", product="oy:b", month="2026-02"),
    ]
    rows = RuleAggregator().need_metrics(mentions, [], "선블록")
    assert {r.need_key for r in rows} == {"밀림"}
    # The sentinel does not enter the denominator that counts the whole set either — it is a month and a
    # product no numerator can reach.
    assert (by_key(rows)["밀림"].persist_months_total, by_key(rows)["밀림"].persist_products_total) == (1, 1)
    # The rollup's canonical folding does not bring the sentinel back either.
    rollup = RuleAggregator(canonical={"밀림": "밀림들뜸"}).need_metrics(mentions, [], "all")
    assert {r.need_key for r in rollup} == {"밀림들뜸"}


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
    # 2025-01 is restored from relative time (resolution='year') and is not counted as a month it existed in.
    assert (row.months_present, row.first_month, row.last_month) == (1, "2025-01", "2026-01")
    assert (row.videos, row.channels) == (1, 1)


# --- #38: --scope takes both axes -----------------------------------------------------------------

SOURCE_CATEGORY = "01 > 선케어 > 선블록"


def labelled(category: str | None, lexicon: str) -> NeedMentionRow:
    """A mention whose label axis (lexicon_category) and source axis (category) differ — the very place #38
    went wrong."""
    return replace(need("백탁", "불만"), category=category, lexicon_category=lexicon)


def test_a_lexicon_scope_expands_to_the_source_categories_its_labels_sit_on():
    """실측(run 16): `--scope 선블록` 이 라벨한 13,857행의 원천 카테고리는 '01 > 선케어 > 선블록' 등
    네 갈래였고, aggregate 는 그 축으로 거른다 — 펼치지 않으면 교집합이 비어 0행이 된다."""
    mentions = [labelled(SOURCE_CATEGORY, "선블록"), labelled("쿠션", "쿠션")]
    assert scopes_for("선블록", mentions) == ["01 > 선케어 > 선블록", "선블록"]


def test_a_source_category_scope_stays_the_one_scope_it_names():
    """A run given the old axis as it was runs exactly as it does today — the expansion adds, it does not
    replace."""
    assert scopes_for(SOURCE_CATEGORY, [labelled(SOURCE_CATEGORY, "선블록")]) == [SOURCE_CATEGORY]


def test_a_label_with_no_source_category_expands_to_nothing():
    """A label attached by a product-name regex (name_keyword) has no source category (analysis/units.py) —
    with nothing to expand, that row is counted under no category scope at all. The silence watch (#38,
    option 3) is what says so."""
    assert scopes_for("선블록", [labelled(None, "선블록")]) == ["선블록"]


def test_no_scope_still_writes_every_source_category_and_the_rollup():
    mentions = [labelled(SOURCE_CATEGORY, "선블록"), labelled("쿠션", "쿠션")]
    assert scopes_for(None, mentions) == ["01 > 선케어 > 선블록", "all", "쿠션"]


def products(rows, need_key):
    """Product-axis rows only — the same need_key as the category total row, emitted again with a different
    product_ref (#41)."""
    return {r.product_ref: r for r in rows if r.need_key == need_key and r.product_ref}


def test_the_product_axis_repeats_each_need_key_for_the_products_that_mention_it():
    rows = RuleAggregator().need_metrics(
        [
            need("밀림", "불만", ref="a/1", product="oy:a", month="2026-01"),
            need("밀림", "만족", ref="a/2", product="oy:a", month="2026-01"),
            need("밀림", "불만", ref="b/1", product="oy:b", month="2026-02"),
        ],
        [],
        "선블록",
    )
    # The category total row is unchanged — screen 1 and the golden set look at it.
    assert (by_key(rows)["밀림"].neg, by_key(rows)["밀림"].pos) == (2, 1)
    per = products(rows, "밀림")
    assert sorted(per) == ["oy:a", "oy:b"]
    assert (per["oy:a"].neg, per["oy:a"].pos, per["oy:a"].unresolved) == (1, 1, 0.5)
    assert (per["oy:b"].neg, per["oy:b"].pos, per["oy:b"].unresolved) == (1, 0, 1.0)
    # A product-axis row is the same formula applied again to a population narrowed to that product alone.
    assert (per["oy:a"].persist_months, per["oy:a"].persist_months_total) == (1, 1)
    assert (per["oy:a"].persist_products, per["oy:a"].persist_products_total) == (1, 1)
    # The PK is (run_id, scope, need_key, month, product_ref) — a product-axis row is split by the fourth
    # column.
    assert all(r.month == "" for r in rows if r.product_ref)
    assert len({(r.scope, r.need_key, r.month, r.product_ref) for r in rows}) == len(rows)


def test_a_product_row_measures_the_low_band_against_that_products_own_denominator():
    rows = RuleAggregator().need_metrics(
        [
            need("밀림", "불만", ref="a/1", product="oy:a", rating=1),
            need("밀림", "불만", ref="b/1", product="oy:b", rating=1),
        ],
        [denom("a", low=10, site=1000), denom("b", low=5, site=500)],
        "선블록",
    )
    assert (by_key(rows)["밀림"].low_mentioning, by_key(rows)["밀림"].denom_low) == (2, 15)
    per = products(rows, "밀림")
    assert (per["oy:a"].low_mentioning, per["oy:a"].denom_low, per["oy:a"].denom_site) == (1, 10, 1000)
    assert (per["oy:a"].low_share, per["oy:b"].low_share) == (0.1, 0.2)
    # interfaces.md §수식: 제품 하나짜리 집합에서 그 식은 제품 단위 정의로 그대로 되돌아간다.
    assert (per["oy:a"].population_share_pct, per["oy:b"].population_share_pct) == (1.0, 2.0)


def test_a_mention_that_names_no_product_lands_only_in_the_category_sum():
    """The same place as B6: a mention with no known product makes no row on the product axis."""
    mention = replace(need("밀림", "불만", product=None), source_product_key=None)
    rows = RuleAggregator().need_metrics([mention], [], "선블록")
    # The month axis (#129) does not ask about the product, so that mention is still on its own month's row
    # — only the product axis drops it.
    assert [(r.month, r.product_ref) for r in rows] == [("", ""), ("2026-01", "")]


# --- #129: the month axis -------------------------------------------------------------------------


def months(rows, need_key):
    """Month-axis rows only — the same (scope, need_key) as the category total row, emitted again with a
    different month (#129)."""
    return {r.month: r for r in rows if r.need_key == need_key and r.month and r.product_ref == ""}


def test_the_month_axis_splits_the_category_sum_without_moving_it():
    rows = RuleAggregator().need_metrics(
        [
            need("밀림", "불만", ref="a/1", product="oy:a", month="2026-01"),
            need("밀림", "만족", ref="a/2", product="oy:a", month="2026-01"),
            need("밀림", "불만", ref="b/1", product="oy:b", month="2026-02"),
            need("밀림", "불만", src="yt_comment", ref="v/1", product=None, month="2026-02"),
        ],
        [],
        "선블록",
    )
    per_month = months(rows, "밀림")
    assert sorted(per_month) == ["2026-01", "2026-02"]
    whole = by_key(rows)["밀림"]
    # Completion criterion: the numerators of the monthly rows sum to the whole-period row. A difference
    # means the monthly grouping polluted the total row.
    assert (whole.neg, whole.pos, whole.yt_neg) == (2, 1, 1)
    assert sum(r.neg for r in per_month.values()) == whole.neg
    assert sum(r.pos for r in per_month.values()) == whole.pos
    assert sum(r.yt_neg or 0 for r in per_month.values()) == whole.yt_neg
    # A ratio is measured again inside that month — it is not the total row's value shared out.
    assert (per_month["2026-01"].unresolved, per_month["2026-02"].unresolved) == (0.5, 1.0)


def test_a_month_row_carries_neither_a_denominator_nor_a_persistence_count():
    rows = RuleAggregator().need_metrics(
        [
            need("밀림", "불만", ref="a/1", product="oy:a", month="2026-01", rating=1, strength=0.8),
            need("밀림", "불만", ref="b/1", product="oy:b", month="2026-02", rating=1),
        ],
        [denom("a", low=10, site=1000), denom("b", low=5, site=500)],
        "선블록",
    )
    assert sorted(months(rows, "밀림")) == ["2026-01", "2026-02"]
    row = months(rows, "밀림")["2026-01"]
    # product_denominator is a captured_at snapshot, so 'that month's denominator' does not exist (#129).
    assert (row.low_share, row.population_share_pct, row.low_mentioning) == (None, None, None)
    assert (row.denom_low, row.denom_site) == (None, None)
    # In a one-month population persist_months is always 1 and means nothing. A 0 asserts a fact that does
    # not exist.
    assert (row.persist_months, row.persist_months_total) == (None, None)
    assert (row.persist_products, row.persist_products_total) == (None, None)
    # The whole-period row is unchanged — screen 1 and the golden set look at it.
    assert (by_key(rows)["밀림"].denom_low, by_key(rows)["밀림"].persist_months) == (15, 2)
    # The values measured inside that month are filled in.
    assert (row.strength_mean, row.strength_low_rating_ratio, row.aspect_scope) == (0.8, 1.0, "generic")


def test_the_month_axis_stays_off_the_product_axis_so_no_two_rows_share_a_key():
    rows = RuleAggregator().need_metrics(
        [
            need("밀림", "불만", ref="a/1", product="oy:a", month="2026-01"),
            need("밀림", "불만", ref="b/1", product="oy:b", month="2026-02"),
        ],
        [],
        "선블록",
    )
    assert {r.month for r in rows} == {"", "2026-01", "2026-02"}
    # Scope of #129: product x month changes the order of magnitude of the row count, and whether the screen
    # carries that payload is not yet known.
    assert all(r.month == "" for r in rows if r.product_ref)
    assert len({(r.scope, r.need_key, r.month, r.product_ref) for r in rows}) == len(rows)


def test_the_rollup_folds_synonyms_on_the_month_axis_too():
    rows = RuleAggregator(canonical={"끈적임": "끈적유분"}).need_metrics(
        [
            need("끈적임", "불만", category="크림", ref="a/1", month="2026-01"),
            need("끈적유분", "불만", category="선블록", ref="b/1", month="2026-01"),
        ],
        [],
        "all",
    )
    assert sorted(months(rows, "끈적유분")) == ["2026-01"]
    # If the folding came undone on the month axis, two synonym rows would stay in the same month and the
    # screen would draw one need as two.
    assert months(rows, "끈적유분")["2026-01"].neg == 2
    assert {r.need_key for r in rows} == {"끈적유분"}


def test_a_month_that_cannot_place_its_comments_reports_no_youtube_count_at_all():
    """Measured in production (2026-08-26): 16,621 comments with resolution='year' gather without exception
    into the single <year>-08 cell — because the value is derived backwards from relative time against the
    collection reference month. Counted as they are they raise a seasonal pattern that does not exist, and
    filtered out with a 0 left behind they raise a silence that does not exist."""
    rows = RuleAggregator().need_metrics(
        [
            need("밀림", "불만", ref="a/1", product="oy:a", month="2025-08"),
            need("밀림", "불만", src="yt_comment", ref="v/1", product=None, month="2025-08"),
            need("밀림", "만족", src="yt_comment", ref="v/2", product=None, month="2025-09"),
            need("밀림", "불만", src="yt_comment", ref="v/3", product=None, month="2025-09"),
        ],
        [],
        "선블록",
    )
    per_month = months(rows, "밀림")
    assert sorted(per_month) == ["2025-08", "2025-09"]
    # The boundary: 2025-08 is missing and 2025-09 onwards is a value.
    assert (per_month["2025-08"].yt_neg, per_month["2025-08"].yt_pos) == (None, None)
    assert (per_month["2025-09"].yt_neg, per_month["2025-09"].yt_pos) == (1, 1)
    # The review axis is not filtered — the fallback is at 'day' resolution so the month is always right.
    assert per_month["2025-08"].neg == 1
    # The whole-period row still counts every comment. So the sum of yt_* over the monthly rows is smaller
    # — that is intended.
    whole = by_key(rows)["밀림"]
    assert whole.yt_neg is not None and (whole.yt_neg, whole.yt_pos) == (2, 1)
    assert sum(r.yt_neg or 0 for r in per_month.values()) < whole.yt_neg


def test_one_comment_of_unknown_month_makes_that_whole_month_unknown():
    """What is implemented is the rule, not today's distribution of the data — the meaning has to hold when a
    recollection drops year resolution into a different month, and when it is mixed with comments that can be
    trusted."""
    stale = replace(
        need("백탁", "불만", src="yt_comment", ref="v/9", product=None, month="2026-01"),
        observed_at_resolution="year",
    )
    rows = RuleAggregator().need_metrics(
        [
            need("밀림", "불만", src="yt_comment", ref="v/1", product=None, month="2026-01"),
            need("밀림", "불만", src="yt_comment", ref="v/2", product=None, month="2026-02"),
            stale,
        ],
        [],
        "선블록",
    )
    assert sorted(months(rows, "밀림")) == ["2026-01", "2026-02"]
    # 못 믿을 값은 그 need_key 의 성질이 아니라 그 달 칸의 성질이다 — 같은 달의 '밀림' 도 결측이다.
    assert months(rows, "밀림")["2026-01"].yt_neg is None
    assert months(rows, "백탁")["2026-01"].yt_neg is None
    # Outside that month everything is fine.
    assert months(rows, "밀림")["2026-02"].yt_neg == 1
    # The whole-period row is unchanged.
    assert (by_key(rows)["밀림"].yt_neg, by_key(rows)["백탁"].yt_neg) == (2, 1)
