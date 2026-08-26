"""Aggregator 계약의 규칙 구현 (contracts/interfaces.md). 순수 함수: 입력은 Iterable, DB 없음.

집계·랭킹 상수는 slice-p9/aggregate.py 와 slice-p2/{q1_churn,q4_price_rank}.py 를
옮긴 것이다(슬라이스는 import 하지 않는다).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

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

        out = self._rows(scope, "", rows, denoms, key)
        # 월 축 (#129): 같은 카테고리 합을 그 달의 언급만으로 다시 잰다. 분모는 넘기지 않는다 —
        # product_denominator 는 captured_at 스냅샷이라 '그 달의 분모' 라는 것이 없고, 전체 기간 분모를
        # 월 분자에 나누면 거짓 비율이 된다. denoms=[] 가 low_*·denom_*·population_share_pct 를 이미
        # 있는 `if denoms` 분기로 NULL 에 떨군다. 제품 축까지 월별로 곱하지는 않는다 — 행 수의 자릿수가
        # 달라지고 그 페이로드를 화면이 감당하는지 아직 재지 않았다.
        by_month: dict[str, list[NeedMentionRow]] = {}
        for mention in rows:
            # month 가 빈 언급은 월 행이 아니라 전체 기간 행과 PK 가 겹쳐, upsert 가 합 행을 그
            # 달치로 덮어쓴다. month_of() 는 NOT NULL 인 observed_at 에서 나오므로 지금은 없는
            # 경우지만, 그때는 행이 사라지는 것이 아니라 틀린 값이 남는다.
            if mention.month:
                by_month.setdefault(mention.month, []).append(mention)
        for month, group in by_month.items():
            out += self._rows(scope, "", group, [], key, month=month)
        # 제품 축 (#41): 같은 식을 그 제품만으로 좁힌 모집단에 다시 적용한다. 카테고리 합 행은
        # product_ref='' 로 남으므로 PK (run_id, scope, need_key, month, product_ref) 가 겹치지 않는다.
        groups: dict[str, list[NeedMentionRow]] = {}
        for mention in rows:
            if product := _product(mention):
                groups.setdefault(product, []).append(mention)
        # 제품 키는 사이트 안에서만 유일하고, 그 쌍 하나에 captured_at 이 여럿 달린다 (001 의 PK).
        by_key: dict[tuple[str, str | None], list[DenominatorRow]] = {}
        for d in denoms:
            by_key.setdefault((d.source, d.product_key), []).append(d)
        for product, group in groups.items():
            # 분모도 그 제품의 것만 남긴다 — 제품 하나짜리 집합에서 population_share_pct 는 제품 단위
            # 정의로 그대로 되돌아간다 (interfaces.md §수식).
            keys = {(m.site, m.source_product_key) for m in group}
            mine = [d for k in keys if k in by_key for d in by_key[k]]
            out += self._rows(scope, product, group, mine, key)
        # month·product_ref 까지 뒤 키로 둔다 — 같은 (neg, need_key) 에 두 축의 행이 여럿 걸리고,
        # 그 자리를 삽입 순서에 맡기면 같은 입력이 run 마다 다른 순서로 쓰인다.
        out.sort(key=lambda r: (-r.neg, r.need_key, r.month, r.product_ref))
        return out

    def _rows(
        self,
        scope: str,
        product_ref: str,
        rows: Sequence[NeedMentionRow],
        denoms: Sequence[DenominatorRow],
        key: Callable[[str], str],
        month: str = "",
    ) -> list[MetricsNeedRow]:
        """한 모집단(카테고리 전체·제품 하나·한 달)의 need_key 별 행. 총계는 그 모집단 안에서 잰다."""
        # #129: 월 행의 persist_* 는 0 이 아니라 NULL 이다. 한 달짜리 모집단에서 persist_months 는
        # 늘 1 이라 뜻이 없는데, 0 으로 눕히면 "그 달에 나타나지 않았다"는 없는 사실이 화면에 선다.
        whole_period = not month
        reviews = [m for m in rows if m.src == REVIEW]
        comments = [m for m in rows if m.src == COMMENT]
        # #129: 상대시간("n년 전")에서 역산한 댓글은 수집 기준월 한 칸에 뭉친다 — 운영 실측 16,621건이
        # 예외 없이 <연도>-08 이었다. 그 달의 yt_* 를 그대로 세면 없는 계절 패턴("매년 8월 스파이크")이
        # 서고, 걸러 내고 0 을 남기면 "그 달에 유튜브 불만이 없었다"는 없는 침묵이 그 자리를 대신한다.
        # 그래서 월 행은 달을 믿을 수 있는 댓글만 세되, 못 믿을 댓글이 하나라도 섞인 달은 얼마나 빠졌는지
        # 알 수 없으므로 결측이다 — 모르는 수는 수가 아니다. 판정은 need_key 별이 아니라 그 달의 댓글
        # 전체로 한다: 못 믿을 값은 그 need_key 의 성질이 아니라 그 달 칸의 성질이다.
        # 리뷰(neg/pos)는 거르지 않는다 — written_at 이 NULL 인 리뷰의 폴백은 'day' 해상도라 달은
        # 언제나 맞고, 그 폴백조차 운영 실측 0건이다 (contracts/formats.md · _wish_row 의 같은 선례).
        datable = comments if whole_period else [m for m in comments if m.observed_at_resolution == "month"]
        # 전체 기간 행은 지금처럼 전 댓글을 센다. 그래서 월 행 yt_* 의 합은 전체 기간 행의 yt_* 보다
        # 작거나 NULL 일 수 있다 — 결함이 아니라 의도다. #129 의 완료 기준은 neg 합에 대한 것이지
        # yt_* 에 대한 것이 아니다. "합이 안 맞는다"고 되돌리기 전에 위 문단을 읽어라.
        yt_known = len(datable) == len(comments)
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

        # need_key 로 한 번만 나눈다 — scope 하나에 5만 행이 들어오므로 키마다 다시 훑으면 곱이 된다.
        by_need: dict[str, list[NeedMentionRow]] = {}
        for mention in rows:
            by_need.setdefault(key(mention.need_key), []).append(mention)

        out: list[MetricsNeedRow] = []
        for need_key, group in by_need.items():
            neg = [m for m in group if m.src == REVIEW and m.polarity == NEGATIVE]
            pos = [m for m in group if m.src == REVIEW and m.polarity == POSITIVE]
            strengths = [m.strength for m in neg if m.strength is not None]
            low_mentioning = (
                len(
                    {
                        m.ref
                        for m in group
                        if m.rating is not None
                        and m.rating <= LOW_RATING
                        and (m.site, m.source_product_key) in complete_keys
                    }
                )
                if denoms
                else None
            )
            low_share = _ratio(low_mentioning, denom_low or 0) if low_mentioning is not None else None
            scopes = [m.aspect_scope for m in group if m.aspect_scope]
            out.append(
                MetricsNeedRow(
                    run_id=0,  # 순수 함수는 run 을 모른다 — 기록하는 쪽이 채운다.
                    scope=scope,
                    need_key=need_key,
                    month=month,
                    product_ref=product_ref,
                    neg=len(neg),
                    pos=len(pos),
                    yt_neg=sum(1 for m in datable if key(m.need_key) == need_key and m.polarity == NEGATIVE)
                    if datable and yt_known
                    else None,
                    yt_pos=sum(1 for m in datable if key(m.need_key) == need_key and m.polarity == POSITIVE)
                    if datable and yt_known
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
                    persist_months=len({m.month for m in neg}) if whole_period else None,
                    persist_months_total=(months_total or None) if whole_period else None,
                    persist_products=(
                        len({_product(m) for m in neg if _product(m)}) if whole_period else None
                    ),
                    persist_products_total=products_total if whole_period else None,
                    aspect_scope=scopes[-1] if scopes else None,
                )
            )
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
