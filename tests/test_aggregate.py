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
        # wish() 와 같은 규칙 — 2025-09 이전 유튜브 시각은 상대시간 복원분이라 달을 믿을 수 없다
        # (formats.md). 이 헬퍼가 'month' 로 박혀 있던 동안은 #129 의 월 축 결함을 재현할 수 없었다.
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
    """전체 기간 · 카테고리 합 행만 — 제품 축(#41)은 product_ref 를, 월 축(#129)은 month 를 달리해
    같은 need_key 를 다시 낸다. 두 축을 걸러내지 않으면 dict 가 합 행을 그것들로 덮어쓴다."""
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
    """B8: need_key='' 는 need_mention 에 남지만 metrics_need 집계에서 빠진다 (formats.md)."""
    mentions = [
        need("밀림", "불만", ref="a/1", product="oy:a", month="2026-01"),
        need("", "불만", ref="b/1", product="oy:b", month="2026-02", rating=1),
        need("", "만족", ref="b/2", product="oy:b", month="2026-02"),
    ]
    rows = RuleAggregator().need_metrics(mentions, [], "선블록")
    assert {r.need_key for r in rows} == {"밀림"}
    # 센티널은 집합 전체를 세는 분모에도 들어가지 않는다 — 어떤 분자도 닿지 못하는 달·제품이다.
    assert (by_key(rows)["밀림"].persist_months_total, by_key(rows)["밀림"].persist_products_total) == (1, 1)
    # 롤업의 canonical 접기도 센티널을 되살리지 않는다.
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
    # 2025-01 은 상대시각 복원분(resolution='year')이라 존재 월로 세지 않는다.
    assert (row.months_present, row.first_month, row.last_month) == (1, "2025-01", "2026-01")
    assert (row.videos, row.channels) == (1, 1)


# --- #38: --scope 는 두 축을 다 받는다 -------------------------------------------------------------

SOURCE_CATEGORY = "01 > 선케어 > 선블록"


def labelled(category: str | None, lexicon: str) -> NeedMentionRow:
    """라벨 축(lexicon_category)과 원천 축(category)이 다른 언급 — #38 이 어긋난 그 자리다."""
    return replace(need("백탁", "불만"), category=category, lexicon_category=lexicon)


def test_a_lexicon_scope_expands_to_the_source_categories_its_labels_sit_on():
    """실측(run 16): `--scope 선블록` 이 라벨한 13,857행의 원천 카테고리는 '01 > 선케어 > 선블록' 등
    네 갈래였고, aggregate 는 그 축으로 거른다 — 펼치지 않으면 교집합이 비어 0행이 된다."""
    mentions = [labelled(SOURCE_CATEGORY, "선블록"), labelled("쿠션", "쿠션")]
    assert scopes_for("선블록", mentions) == ["01 > 선케어 > 선블록", "선블록"]


def test_a_source_category_scope_stays_the_one_scope_it_names():
    """옛 축을 그대로 준 실행은 지금과 똑같이 돈다 — 펼침은 추가이지 대체가 아니다."""
    assert scopes_for(SOURCE_CATEGORY, [labelled(SOURCE_CATEGORY, "선블록")]) == [SOURCE_CATEGORY]


def test_a_label_with_no_source_category_expands_to_nothing():
    """제품명 정규식(name_keyword)으로 붙은 라벨은 원천 카테고리가 없다 (analysis/units.py) — 펼칠
    값이 없으니 그 행은 어떤 카테고리 scope 로도 세어지지 않는다. 침묵 감시(#38 택3)가 그것을 말한다."""
    assert scopes_for("선블록", [labelled(None, "선블록")]) == ["선블록"]


def test_no_scope_still_writes_every_source_category_and_the_rollup():
    mentions = [labelled(SOURCE_CATEGORY, "선블록"), labelled("쿠션", "쿠션")]
    assert scopes_for(None, mentions) == ["01 > 선케어 > 선블록", "all", "쿠션"]


def products(rows, need_key):
    """제품 축 행만 — 카테고리 합 행과 같은 need_key 를 product_ref 만 달리해 다시 낸다 (#41)."""
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
    # 카테고리 합 행은 그대로다 — 화면 1 과 골든이 그것을 본다.
    assert (by_key(rows)["밀림"].neg, by_key(rows)["밀림"].pos) == (2, 1)
    per = products(rows, "밀림")
    assert sorted(per) == ["oy:a", "oy:b"]
    assert (per["oy:a"].neg, per["oy:a"].pos, per["oy:a"].unresolved) == (1, 1, 0.5)
    assert (per["oy:b"].neg, per["oy:b"].pos, per["oy:b"].unresolved) == (1, 0, 1.0)
    # 제품 축 행은 그 제품만으로 좁힌 모집단에 같은 식을 다시 적용한 것이다.
    assert (per["oy:a"].persist_months, per["oy:a"].persist_months_total) == (1, 1)
    assert (per["oy:a"].persist_products, per["oy:a"].persist_products_total) == (1, 1)
    # PK 는 (run_id, scope, need_key, month, product_ref) 다 — 제품 축 행은 네 번째 칸으로 갈린다.
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
    """B6 과 같은 자리: 제품을 모르는 언급은 제품 축에 행을 만들지 못한다."""
    mention = replace(need("밀림", "불만", product=None), source_product_key=None)
    rows = RuleAggregator().need_metrics([mention], [], "선블록")
    # 월 축(#129)은 제품을 묻지 않으므로 그 언급도 자기 달의 행에는 실린다 — 빠지는 것은 제품 축뿐이다.
    assert [(r.month, r.product_ref) for r in rows] == [("", ""), ("2026-01", "")]


# --- #129: 월 축 ---------------------------------------------------------------------------------


def months(rows, need_key):
    """월 축 행만 — 카테고리 합 행과 같은 (scope, need_key) 를 month 만 달리해 다시 낸다 (#129)."""
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
    # 완료 기준: 월 행의 분자 합이 전체 기간 행과 같다. 달라지면 월 그룹핑이 합 행을 오염시킨 것이다.
    assert (whole.neg, whole.pos, whole.yt_neg) == (2, 1, 1)
    assert sum(r.neg for r in per_month.values()) == whole.neg
    assert sum(r.pos for r in per_month.values()) == whole.pos
    assert sum(r.yt_neg or 0 for r in per_month.values()) == whole.yt_neg
    # 비율은 그 달 안에서 다시 잰다 — 합 행의 값을 나눠 가진 것이 아니다.
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
    # product_denominator 는 captured_at 스냅샷이라 '그 달의 분모' 가 존재하지 않는다 (#129).
    assert (row.low_share, row.population_share_pct, row.low_mentioning) == (None, None, None)
    assert (row.denom_low, row.denom_site) == (None, None)
    # 월 하나짜리 모집단에서 persist_months 는 늘 1 이라 뜻이 없다. 0 이면 없는 사실을 주장한다.
    assert (row.persist_months, row.persist_months_total) == (None, None)
    assert (row.persist_products, row.persist_products_total) == (None, None)
    # 전체 기간 행은 그대로다 — 화면 1 과 골든이 그것을 본다.
    assert (by_key(rows)["밀림"].denom_low, by_key(rows)["밀림"].persist_months) == (15, 2)
    # 그 달 안에서 재는 값은 채운다.
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
    # #129 범위: 제품 × 월 은 행 수의 자릿수를 바꾸고 그 페이로드를 화면이 감당하는지 아직 모른다.
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
    # 접기가 월 축에서 풀리면 같은 달에 동의어 두 행이 남아 화면이 한 need 를 둘로 그린다.
    assert months(rows, "끈적유분")["2026-01"].neg == 2
    assert {r.need_key for r in rows} == {"끈적유분"}


def test_a_month_that_cannot_place_its_comments_reports_no_youtube_count_at_all():
    """운영 실측(2026-08-26): resolution='year' 댓글 16,621건이 예외 없이 <연도>-08 한 칸에 뭉쳐
    있다 — 상대시간을 수집 기준월에서 역산한 값이기 때문이다. 그대로 세면 없는 계절 패턴이 서고,
    걸러 내고 0 을 남기면 없는 침묵이 선다."""
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
    # 경계: 2025-08 은 결측, 2025-09 부터가 값이다.
    assert (per_month["2025-08"].yt_neg, per_month["2025-08"].yt_pos) == (None, None)
    assert (per_month["2025-09"].yt_neg, per_month["2025-09"].yt_pos) == (1, 1)
    # 리뷰 축은 거르지 않는다 — 폴백이 'day' 해상도라 달은 언제나 맞다.
    assert per_month["2025-08"].neg == 1
    # 전체 기간 행은 여전히 전 댓글을 센다. 그래서 월 행 yt_* 의 합이 그보다 작다 — 의도다.
    whole = by_key(rows)["밀림"]
    assert whole.yt_neg is not None and (whole.yt_neg, whole.yt_pos) == (2, 1)
    assert sum(r.yt_neg or 0 for r in per_month.values()) < whole.yt_neg


def test_one_comment_of_unknown_month_makes_that_whole_month_unknown():
    """구현하는 것은 규칙이지 지금의 데이터 분포가 아니다 — 재수집으로 year 해상도가 다른 달에
    떨어져도, 믿을 수 있는 댓글과 섞여도 뜻이 유지돼야 한다."""
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
    # 그 달 밖은 멀쩡하다.
    assert months(rows, "밀림")["2026-02"].yt_neg == 1
    # 전체 기간 행은 불변이다.
    assert (by_key(rows)["밀림"].yt_neg, by_key(rows)["백탁"].yt_neg) == (2, 1)
