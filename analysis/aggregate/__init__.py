"""Aggregator 계약의 규칙 구현 (contracts/interfaces.md). 순수 함수: 입력은 Iterable, DB 없음.

집계·랭킹 상수는 slice-p9/aggregate.py 와 slice-p2/{q1_churn,q4_price_rank}.py 를
옮긴 것이다(슬라이스는 import 하지 않는다).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from analysis.types import DenominatorRow, MetricsNeedRow, MetricsWishRow, NeedMentionRow, WishMentionRow

__all__ = [
    "AGGREGATE_VERSION",
    "EXAMPLE_CHARS",
    "LIKE_CAP",
    "LOW_RATING",
    "ROLLUP_SCOPE",
    "RuleAggregator",
    "WISH_SCOPES",
]

# versioning.md 의 두 형식 중 하나여야 한다 — 인스턴스 속성은 tests/test_version_strings.py 의
# VERSIONS 에 한 줄로 들어가지 못하므로 모듈 상수가 정본이고 기본 인자가 그것을 가리킨다.
AGGREGATE_VERSION = "rule-v1.0"

# interfaces.md §수식 A8: 상한은 슬라이스에 없고 계약이 정한다.
LIKE_CAP = 100
# 사람이 후보를 읽을 예시 문장 길이 — slice-p9 aggregate.py 가 자른 폭 그대로 (A7).
EXAMPLE_CHARS = 160
# 사이트 저평점 구간의 경계. strength = 1 - rating/5 이므로 rating<=2 와 strength>=0.6 은 같은 집합이다.
LOW_RATING = 2.0
LOW_STRENGTH = 0.6
ROLLUP_SCOPE = "all"
REVIEW = "review"
COMMENT = "yt_comment"
FORMAT_SEP = ";"
NEGATIVE = "불만"
POSITIVE = "만족"

FORMAT, ATTRIBUTE, BRAND = "format", "attribute", "brand"
# scope → (wish_class, 이 scope 가 세는 축). 교차표와 그 marginal 은 PK 가 겹쳐 한 scope 에 들어가지 못한다.
WISH_SCOPES: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "wish:a": ("a", (FORMAT, ATTRIBUTE, BRAND)),
    "wish:b": ("b", (FORMAT, BRAND)),
    "wish:a:format×attr": ("a", ((FORMAT, ATTRIBUTE),)),  # type: ignore[dict-item]
}


def _first(value: str | None) -> str:
    """format 은 ';' 로 최대 3개가 들어오고 첫 번째가 주 값이다 (A12)."""
    return value.split(FORMAT_SEP, 1)[0] if value else ""


def _ratio(numerator: float, denominator: float) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def _product(mention: NeedMentionRow) -> str:
    return mention.product_ref or mention.source_product_key or ""


class RuleAggregator:
    def __init__(self, version: str = AGGREGATE_VERSION, canonical: Mapping[str, str] | None = None) -> None:
        self.version = version
        # A17: scope='all' 롤업만 needs.need_key.canonical 로 동의어를 접는다.
        self._canonical = canonical or {}

    def need_metrics(
        self, mentions: Iterable[NeedMentionRow], denominators: Iterable[DenominatorRow], scope: str
    ) -> list[MetricsNeedRow]:
        rollup = scope == ROLLUP_SCOPE
        # B8: aspect 를 못 정한 행의 need_key='' 센티널은 집계 전에 빠진다 — 분모까지 세면
        # 어떤 need_key 도 닿을 수 없는 달·제품이 persist_*_total 에 들어간다 (formats.md).
        rows = [m for m in mentions if m.need_key and (rollup or (m.category or "") == scope)]
        denoms = [d for d in denominators if rollup or (d.category or "") == scope]

        def key(need_key: str) -> str:
            return self._canonical.get(need_key, need_key) if rollup else need_key

        reviews = [m for m in rows if m.src == REVIEW]
        comments = [m for m in rows if m.src == COMMENT]
        months_total = len({m.month for m in reviews})
        # B6: 언급 0건 제품은 분모에만 있다. 분모가 없을 때만 언급에서 제품 모집단을 복원한다.
        products_total = (
            len({(d.source, d.product_key) for d in denoms}) or None
            if denoms
            else len({_product(m) for m in reviews if _product(m)}) or None
        )

        complete = [d for d in denoms if d.low_complete]
        # 제품 키는 사이트 안에서만 유일하다 — source 를 떼면 다른 사이트의 같은 키가 섞인다.
        complete_keys = {(d.source, d.product_key) for d in complete}
        # 전수 제품이 하나도 없는 카테고리의 분모는 결측이 아니라 0 이다 — 분모를 준 쪽만 None 을 받는다.
        denom_low = sum(d.low_collected or 0 for d in complete) if denoms else None
        denom_site = sum(d.site_review_count or 0 for d in complete) if denoms else None
        site_low_pct = _ratio(sum(d.site_low_est or 0 for d in complete), denom_site or 0)
        low_rated = [
            m
            for m in rows
            if m.rating is not None
            and m.rating <= LOW_RATING
            and (m.site, m.source_product_key) in complete_keys
        ]

        out: list[MetricsNeedRow] = []
        for need_key in {key(m.need_key) for m in rows}:
            neg = [m for m in reviews if key(m.need_key) == need_key and m.polarity == NEGATIVE]
            pos = [m for m in reviews if key(m.need_key) == need_key and m.polarity == POSITIVE]
            strengths = [m.strength for m in neg if m.strength is not None]
            low_mentioning = (
                len({m.ref for m in low_rated if key(m.need_key) == need_key}) if denoms else None
            )
            low_share = _ratio(low_mentioning, denom_low or 0) if low_mentioning is not None else None
            scopes = [m.aspect_scope for m in rows if key(m.need_key) == need_key and m.aspect_scope]
            out.append(
                MetricsNeedRow(
                    run_id=0,  # 순수 함수는 run 을 모른다 — 기록하는 쪽이 채운다.
                    scope=scope,
                    need_key=need_key,
                    neg=len(neg),
                    pos=len(pos),
                    yt_neg=sum(1 for m in comments if key(m.need_key) == need_key and m.polarity == NEGATIVE)
                    if comments
                    else None,
                    yt_pos=sum(1 for m in comments if key(m.need_key) == need_key and m.polarity == POSITIVE)
                    if comments
                    else None,
                    unresolved=_ratio(len(neg), len(neg) + len(pos)),
                    low_share=low_share,
                    population_share_pct=(
                        100 * low_share * site_low_pct
                        if low_share is not None and site_low_pct is not None
                        else None
                    ),
                    low_mentioning=low_mentioning,
                    denom_low=denom_low,
                    denom_site=denom_site,
                    strength_mean=sum(strengths) / len(strengths) if strengths else None,
                    strength_low_rating_ratio=_ratio(
                        sum(1 for m in neg if m.strength is not None and m.strength >= LOW_STRENGTH),
                        len(neg),
                    ),
                    persist_months=len({m.month for m in neg}),
                    persist_months_total=months_total or None,
                    persist_products=len({_product(m) for m in neg if _product(m)}),
                    persist_products_total=products_total,
                    aspect_scope=scopes[-1] if scopes else None,
                )
            )
        out.sort(key=lambda r: (-r.neg, r.need_key))
        return out

    def wish_metrics(self, wishes: Iterable[WishMentionRow], scope: str) -> list[MetricsWishRow]:
        wish_class, axes = WISH_SCOPES[scope]
        rows = [w for w in wishes if w.wish_class == wish_class]
        out: list[MetricsWishRow] = []
        for axis in axes:
            groups: dict[tuple[str, str, str], list[WishMentionRow]] = {}
            for w in rows:
                cell = _cell(w, axis)
                if any(cell):
                    groups.setdefault(cell, []).append(w)
            out += [_wish_row(scope, cell, group) for cell, group in groups.items()]
        out.sort(key=lambda r: (-r.mentions, -(r.like_sum or 0), r.format, r.attribute, r.brand))
        return out


def _cell(wish: WishMentionRow, axis: str | tuple[str, ...]) -> tuple[str, str, str]:
    values = {FORMAT: _first(wish.format), ATTRIBUTE: _first(wish.attribute), BRAND: wish.brand or ""}
    wanted = axis if isinstance(axis, tuple) else (axis,)
    return tuple(values[name] if name in wanted else "" for name in (FORMAT, ATTRIBUTE, BRAND))  # type: ignore[return-value]


def _wish_row(scope: str, cell: tuple[str, str, str], group: Sequence[WishMentionRow]) -> MetricsWishRow:
    likes = [w.like_count for w in group if w.like_count is not None]
    months = [w.month for w in group]
    # 2025-09 이전 유튜브 시각은 상대시각 복원분(resolution='year')이라 '존재한 월'로 셀 수 없다 (formats.md).
    dated = {w.month for w in group if w.observed_at_resolution == "month"}
    loudest = max(group, key=lambda w: w.like_count or 0)
    return MetricsWishRow(
        run_id=0,
        scope=scope,
        format=cell[0],
        attribute=cell[1],
        brand=cell[2],
        mentions=len(group),
        channels=len({w.channel_id for w in group if w.channel_id}) or None,
        videos=len({w.video_id for w in group if w.video_id}) or None,
        months_present=len(dated),
        first_month=min(months),
        last_month=max(months),
        like_sum=sum(likes) if likes else None,
        like_cap_sum=float(sum(min(v, LIKE_CAP) for v in likes)) if likes else None,
        max_like=max(likes) if likes else None,
        example=loudest.sentence[:EXAMPLE_CHARS],
    )
