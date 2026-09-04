"""The rule implementation of the Aggregator contract (contracts/interfaces.md). Pure functions: the input is
an Iterable, no DB.

The aggregation and ranking constants are carried over from slice-p9/aggregate.py and
slice-p2/{q1_churn,q4_price_rank}.py (the slices are not imported).
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

# It has to be one of the two formats in versioning.md — an instance attribute cannot go into VERSIONS in
# tests/test_version_strings.py as a single line, so the module constant is canonical and the default
# argument points at it.
AGGREGATE_VERSION = "rule-v1.0"

# interfaces.md §Formulas A8: the cap is not in the slice; the contract sets it.
LIKE_CAP = 100
# The length of the example sentence a person reads a candidate by — the same width slice-p9 aggregate.py
# cut to (A7).
EXAMPLE_CHARS = 160
# The boundary of the site's low-rating band. strength = 1 - rating/5, so rating<=2 and strength>=0.6 are
# the same set.
LOW_RATING = 2.0
LOW_STRENGTH = 0.6
ROLLUP_SCOPE = "all"
REVIEW = "review"
COMMENT = "yt_comment"
FORMAT_SEP = ";"
NEGATIVE = "불만"
POSITIVE = "만족"

FORMAT, ATTRIBUTE, BRAND = "format", "attribute", "brand"
# scope → (wish_class, the axes this scope counts). A cross table and its marginals share a PK and cannot
# live in one scope.
WISH_SCOPES: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "wish:a": ("a", (FORMAT, ATTRIBUTE, BRAND)),
    "wish:b": ("b", (FORMAT, BRAND)),
    "wish:a:format×attr": ("a", ((FORMAT, ATTRIBUTE),)),  # type: ignore[dict-item]
}


def _first(value: str | None) -> str:
    """format takes up to three values separated by ';' and the first one is the main value (A12)."""
    return value.split(FORMAT_SEP, 1)[0] if value else ""


def _ratio(numerator: float, denominator: float) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def _product(mention: NeedMentionRow) -> str:
    return mention.product_ref or mention.source_product_key or ""


class RuleAggregator:
    def __init__(self, version: str = AGGREGATE_VERSION, canonical: Mapping[str, str] | None = None) -> None:
        self.version = version
        # A17: only the scope='all' rollup folds synonyms with needs.need_key.canonical.
        self._canonical = canonical or {}

    def need_metrics(
        self, mentions: Iterable[NeedMentionRow], denominators: Iterable[DenominatorRow], scope: str
    ) -> list[MetricsNeedRow]:
        rollup = scope == ROLLUP_SCOPE
        # B8: the need_key='' sentinel of a row whose aspect could not be decided drops out before
        # aggregation — counting it in the denominator too would put months and products no need_key can
        # reach into persist_*_total (formats.md).
        rows = [m for m in mentions if m.need_key and (rollup or (m.category or "") == scope)]
        denoms = [d for d in denominators if rollup or (d.category or "") == scope]

        def key(need_key: str) -> str:
            return self._canonical.get(need_key, need_key) if rollup else need_key

        out = self._rows(scope, "", rows, denoms, key)
        # Month axis (#129): the same category total measured again from that month's mentions alone. The
        # denominators are not passed on — product_denominator is a captured_at snapshot, so there is no such
        # thing as 'that month's denominator', and dividing a whole-period denominator into a monthly
        # numerator gives a false ratio. denoms=[] drops low_* · denom_* · population_share_pct to NULL
        # through the `if denoms` branch that is already there. The product axis is not multiplied by month
        # as well — the row count changes order of magnitude and whether the screen carries that payload has
        # not been measured yet.
        by_month: dict[str, list[NeedMentionRow]] = {}
        for mention in rows:
            # A mention with an empty month is not a monthly row: its PK collides with the whole-period row
            # and the upsert overwrites the total with that month's figures. month_of() comes from a NOT NULL
            # observed_at so it cannot happen today, but if it did the row would not disappear — a wrong
            # value would stay.
            if mention.month:
                by_month.setdefault(mention.month, []).append(mention)
        for month, group in by_month.items():
            out += self._rows(scope, "", group, [], key, month=month)
        # Product axis (#41): the same formula applied again to a population narrowed to that product alone.
        # The category total row keeps product_ref='', so the PK (run_id, scope, need_key, month,
        # product_ref) does not collide.
        groups: dict[str, list[NeedMentionRow]] = {}
        for mention in rows:
            if product := _product(mention):
                groups.setdefault(product, []).append(mention)
        # A product key is unique only inside a site, and one such pair carries several captured_at (the PK
        # of 001).
        by_key: dict[tuple[str, str | None], list[DenominatorRow]] = {}
        for d in denoms:
            by_key.setdefault((d.source, d.product_key), []).append(d)
        for product, group in groups.items():
            # The denominator is narrowed to that product too — over a one-product set
            # population_share_pct collapses back to the per-product definition (interfaces.md §Formulas).
            keys = {(m.site, m.source_product_key) for m in group}
            mine = [d for k in keys if k in by_key for d in by_key[k]]
            out += self._rows(scope, product, group, mine, key)
        # month and product_ref go into the trailing key as well — several rows of both axes hang off the
        # same (neg, need_key), and leaving that place to insertion order writes the same input in a
        # different order from run to run.
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
        """The per-need_key rows of one population (a whole category, one product, one month). The totals are
        measured inside that population."""
        # #129: persist_* on a monthly row is NULL, not 0. In a one-month population persist_months is always
        # 1 and means nothing, and laying it down as 0 puts a fact that does not exist — "it did not appear
        # that month" — on the screen.
        whole_period = not month
        reviews = [m for m in rows if m.src == REVIEW]
        comments = [m for m in rows if m.src == COMMENT]
        # #129: 상대시간("n년 전")에서 역산한 댓글은 수집 기준월 한 칸에 뭉친다 — 운영 실측 16,621건이
        # 예외 없이 <연도>-08 이었다. 그 달의 yt_* 를 그대로 세면 없는 계절 패턴("매년 8월 스파이크")이
        # 서고, 걸러 내고 0 을 남기면 "그 달에 유튜브 불만이 없었다"는 없는 침묵이 그 자리를 대신한다.
        # So a monthly row counts only the comments whose month can be trusted, and a month with even one
        # untrustworthy comment in it is missing, because how much was left out cannot be known — a number
        # that is not known is not a number. The decision is made over all of that month's comments rather
        # than per need_key: an untrustworthy value is a property of that month's cell, not of that need_key.
        # Reviews (neg/pos) are not filtered — the fallback of a review whose written_at is NULL is at 'day'
        # resolution so its month is always right, and even that fallback has 0 measured occurrences in
        # production (contracts/formats.md · the same precedent in _wish_row).
        datable = comments if whole_period else [m for m in comments if m.observed_at_resolution == "month"]
        # The whole-period row counts every comment, as it does today. So the sum of yt_* over the monthly
        # rows can be smaller than the yt_* of the whole-period row, or NULL — that is intended, not a
        # defect. The completion criterion of #129 is about the neg total, not about yt_*. Read the
        # paragraph above before reverting this as "the totals do not add up".
        yt_known = len(datable) == len(comments)
        months_total = len({m.month for m in reviews})
        # B6: a product with 0 mentions is only in the denominator. The product population is rebuilt from
        # the mentions only when there is no denominator.
        products_total = (
            len({(d.source, d.product_key) for d in denoms}) or None
            if denoms
            else len({_product(m) for m in reviews if _product(m)}) or None
        )

        complete = [d for d in denoms if d.low_complete]
        # A product key is unique only inside a site — drop source and the same key from another site mixes
        # in.
        complete_keys = {(d.source, d.product_key) for d in complete}
        # For a category with no complete product the denominator is 0, not missing — only the side that gave
        # a denominator receives None.
        denom_low = sum(d.low_collected or 0 for d in complete) if denoms else None
        denom_site = sum(d.site_review_count or 0 for d in complete) if denoms else None
        site_low_pct = _ratio(sum(d.site_low_est or 0 for d in complete), denom_site or 0)

        # Split by need_key once — one scope takes in 50,000 rows, so rescanning per key makes it a product.
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
                    run_id=0,  # a pure function knows no run — the side that records it fills it in
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
    # YouTube timestamps before 2025-09 are restored from relative time (resolution='year'), so they cannot
    # be counted as a 'month it existed in' (formats.md).
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
