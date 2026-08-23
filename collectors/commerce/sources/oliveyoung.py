"""올리브영 (oliveyoung.co.kr).

origin: service/trend-radar/src/trend_radar/sources/oliveyoung.py -- ported for #7, with review_low
generalized from one hardcoded board to `--board <name>` and its RATING_ASC walk generalized from a
fixed page count to "read until a 3-star review appears" (issue #7).

Plain HTTP gets a Cloudflare managed challenge, so this source declares the browser transport. The 100
products on a ranking page are split across 25 `<ul class="cate_prd_list">` blocks of four rather than
one list of 100, so a parser that took the first list would silently collect four. The star rating shown
on that page is a JavaScript template filled in client-side, not a value, so this source does not store
one from it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar
from urllib.parse import urlencode

from selectolax.parser import HTMLParser, Node

from collectors.commerce.contract import Fetch, Payload, Scope, SourcePolicy, Transport, Yield
from collectors.commerce.models import (
    Dataset,
    ProductRecord,
    RankRecord,
    Record,
    ReviewRecord,
    ReviewStatsRecord,
    ReviewSummaryRecord,
    ReviewTopicRecord,
)
from collectors.commerce.registry import register
from collectors.commerce.scrub import author_hash, kst_date, review_text

BEST_LIST = "https://www.oliveyoung.co.kr/store/main/getBestList.do"
SALES_RANKING = "900000100100001"
PRODUCT_LIST = "ul.cate_prd_list > li"
_DIGITS = re.compile(r"[^0-9]")
DETAIL_URL = "https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo={goods}"

# 화장품법(the Cosmetics Act) mandates this exact phrase as the label for the full-ingredients
# disclosure, so it is the one stable handle on an otherwise unlabelled table.
INGREDIENTS_LABEL = "화장품법에 따라 기재해야 하는 모든 성분"

# Reviews live on the mobile host, not behind the challenge, and are POST-only -- a browser navigation
# cannot send a POST, so these requests set Fetch.transport explicitly.
REVIEW_ENDPOINT = "https://m.oliveyoung.co.kr/review/api/v2/reviews/cursor"
# Fifty is the most this endpoint actually returns; above it the response is still SUCCESS with an
# empty list, so raising this silently collects nothing.
REVIEW_PAGE_SIZE = 50
REVIEW_PRODUCTS = 10
# The page cap is carried by cursor, not page number -- the endpoint ignores `page` and only the
# cursor advances, measured 2026-08-19.
REVIEW_PAGES = 3
# RATING_DESC and RATING_ASC are the only two ends this site sorts by; there is no newest-first sort.
REVIEW_SORTS = ("RATING_DESC", "RATING_ASC")
STATS_ENDPOINT = "https://m.oliveyoung.co.kr/review/api/v2/reviews/{goods}/stats"
SUMMARY_ENDPOINT = "https://m.oliveyoung.co.kr/review/api/v1/reviews/{goods}/summary"
SUMMARY_FEATURES = 3

REVIEW_BOARDS: tuple[str, ...] = ("skincare", "makeup", "hair", "masks", "dermo")
REVIEW_PRODUCTS_PER_BOARD = REVIEW_PRODUCTS // len(REVIEW_BOARDS)
REVIEW_STATS_PRODUCTS = 45
REVIEW_STATS_PRODUCTS_PER_BOARD = REVIEW_STATS_PRODUCTS // len(REVIEW_BOARDS)
PRODUCT_PRODUCTS = 85
PRODUCT_PRODUCTS_PER_BOARD = PRODUCT_PRODUCTS // len(REVIEW_BOARDS)

# review_low: one board's top products, RATING_ASC read toward exhaustion of the low-rated end.
# RATING_ASC is every one-star, then every two-star, recency as the tiebreak, so the point where a
# 3-star review first appears is where the low end provably ends -- generalized in #7 from a fixed
# 6-page walk (which risked stopping mid-complaint for a higher-volume product, or over-reading a
# lower-volume one) to a data-driven stop.
#
# `board` is now a caller-chosen name rather than the single constant `suncare` this walk started
# with; the set of names it may choose from is scope.json's review_low.boards (#7), so extending which
# boards can run this dataset is a scope.json edit, not a code change. Must be a name in _BOARDS.
LOW_BOARDS: tuple[str, ...] = ("suncare",)
LOW_PRODUCTS = 10
# Ceiling on RATING_ASC pages per product -- a backstop, not the stop condition (see above). One page
# more than the original fixed 6: SourcePolicy.max_requests_per_run is a rate-policy constant this
# port keeps literal (#7 brief), so the ceiling is the largest value that still leaves >=5 requests of
# retry room in the 100-request budget (1 board + 10 * (7 + 1 desc + 1 stats) = 91).
LOW_ASC_PAGES_MAX = 7
LOW_DESC_PAGES = 1
# Below this many low-rated reviews collected, or once a 3-star review has been reached, the low end
# counts as fully read (product_denominator.low_complete, computed downstream of this collector).
# Recorded in scope.json rather than here because it is a completeness threshold for the *analysis*
# reading review_low's output, not a request-shape number this source's parser consults.
LOW_COMPLETE_THRESHOLD = 150


@dataclass(frozen=True, slots=True)
class _Board:
    name: str
    category: str | None


_BOARDS: tuple[_Board, ...] = (
    _Board("sale", None),
    _Board("skincare", "10000010001"),
    _Board("masks", "10000010009"),
    _Board("cleansing", "10000010010"),
    _Board("suncare", "10000010011"),
    _Board("makeup", "10000010002"),
    _Board("nail", "10000010012"),
    _Board("tools", "10000010006"),
    _Board("dermo", "10000010008"),
    _Board("mens", "10000010007"),
    _Board("fragrance", "10000010005"),
    _Board("hair", "10000010004"),
    _Board("body", "10000010003"),
)
_BOARD_NAMES = frozenset(b.name for b in _BOARDS)


@register
class OliveYoung:
    key: ClassVar[str] = "oliveyoung"
    datasets: ClassVar[frozenset[Dataset]] = frozenset(
        {
            Dataset.RANKING,
            Dataset.PRODUCT,
            Dataset.REVIEW,
            Dataset.REVIEW_LOW,
            Dataset.REVIEW_STATS,
        }
    )
    scope: ClassVar[Scope] = MappingProxyType(
        {
            Dataset.RANKING: MappingProxyType({"boards": len(_BOARDS)}),
            Dataset.PRODUCT: MappingProxyType(
                {"boards": len(REVIEW_BOARDS), "product_products": PRODUCT_PRODUCTS}
            ),
            Dataset.REVIEW: MappingProxyType(
                {
                    "review_boards": len(REVIEW_BOARDS),
                    "review_products": REVIEW_PRODUCTS,
                    "review_pages": REVIEW_PAGES,
                    "review_page_size": REVIEW_PAGE_SIZE,
                    "review_sorts": len(REVIEW_SORTS),
                }
            ),
            Dataset.REVIEW_STATS: MappingProxyType(
                {"review_boards": len(REVIEW_BOARDS), "review_stats_products": REVIEW_STATS_PRODUCTS}
            ),
            Dataset.REVIEW_LOW: MappingProxyType(
                {
                    "low_boards": len(LOW_BOARDS),
                    "low_products": LOW_PRODUCTS,
                    "low_asc_pages_max": LOW_ASC_PAGES_MAX,
                    "low_desc_pages": LOW_DESC_PAGES,
                    "review_page_size": REVIEW_PAGE_SIZE,
                }
            ),
        }
    )
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        min_interval_s=5.0,
        concurrency=1,
        max_requests_per_run=100,
        # The ranking is depth 0, review pages are 1+; the low walk goes LOW_ASC_PAGES_MAX deep on the
        # same policy, so the ceiling is the deeper of the two.
        max_depth=max(REVIEW_PAGES, LOW_ASC_PAGES_MAX),
        timeout_s=120.0,
        max_attempts=2,
        transport=Transport.BROWSER,
    )

    def seeds(self, dataset: Dataset, *, board: str | None = None) -> Sequence[Fetch]:
        if dataset is Dataset.RANKING:
            return tuple(_seed(b, dataset) for b in _BOARDS)
        by_name = {b.name: b for b in _BOARDS}
        if dataset is Dataset.REVIEW_LOW:
            chosen = board or LOW_BOARDS[0]
            if chosen not in LOW_BOARDS:
                raise ValueError(
                    f"review_low board {chosen!r} is not in scope.json's review_low.boards {LOW_BOARDS}"
                )
            if chosen not in _BOARD_NAMES:
                raise ValueError(f"review_low board {chosen!r} is not a board this source walks")
            return (_seed(by_name[chosen], dataset),)
        if dataset not in (Dataset.PRODUCT, Dataset.REVIEW, Dataset.REVIEW_STATS):
            return ()
        return tuple(_seed(by_name[name], dataset) for name in REVIEW_BOARDS)

    def parse(self, payload: Payload) -> Yield:
        kind = payload.fetch.ctx("kind")
        if kind == "reviews":
            return _parse_reviews(payload, source=self.key)
        if kind == "stats":
            return _parse_stats(payload, source=self.key)
        if kind == "summary":
            return _parse_summary(payload, source=self.key)
        if kind == "product":
            return _parse_product(payload, source=self.key)
        return _parse_ranking(payload, source=self.key)


def _seed(board: _Board, dataset: Dataset) -> Fetch:
    url = BEST_LIST
    if board.category is not None:
        query = urlencode({"dispCatNo": SALES_RANKING, "fltDispCatNo": board.category})
        url = f"{BEST_LIST}?{query}"
    return Fetch(
        url=url,
        dataset=dataset,
        wait_for=PRODUCT_LIST,
        context=(
            ("kind", "ranking"),
            ("board", board.name),
            ("category", board.category or board.name),
        ),
    )


def _parse_ranking(payload: Payload, source: str) -> Yield:
    tree = HTMLParser(payload.text())
    board = payload.fetch.ctx("board") or "sale"
    category_key = payload.fetch.ctx("category") or board
    wants_reviews = payload.fetch.dataset is Dataset.REVIEW
    wants_low = payload.fetch.dataset is Dataset.REVIEW_LOW
    wants_stats = payload.fetch.dataset is Dataset.REVIEW_STATS
    wants_products = payload.fetch.dataset is Dataset.PRODUCT

    records: list[Record] = []
    follow: list[Fetch] = []
    rank = 0
    for item in tree.css(PRODUCT_LIST):
        record = _to_record(
            item,
            source=source,
            captured_at=payload.captured_at,
            board=board,
            category_key=category_key,
            rank=rank + 1,
        )
        if record is None:
            continue
        rank += 1
        if wants_reviews or wants_low or wants_stats or wants_products:
            if wants_reviews and rank <= REVIEW_PRODUCTS_PER_BOARD:
                for sort in REVIEW_SORTS:
                    follow.append(_review_fetch(record.product_key, sort))
            if wants_low and rank <= LOW_PRODUCTS:
                key = record.product_key
                follow.append(
                    _review_fetch(key, "RATING_ASC", pages=LOW_ASC_PAGES_MAX, dataset=Dataset.REVIEW_LOW)
                )
                follow.append(
                    _review_fetch(key, "RATING_DESC", pages=LOW_DESC_PAGES, dataset=Dataset.REVIEW_LOW)
                )
                follow.append(_stats_fetch(key, dataset=Dataset.REVIEW_LOW))
            if wants_stats and rank <= REVIEW_STATS_PRODUCTS_PER_BOARD:
                follow.append(_stats_fetch(record.product_key))
                follow.append(_summary_fetch(record.product_key))
            if wants_products and rank <= PRODUCT_PRODUCTS_PER_BOARD:
                follow.append(_product_fetch(record.product_key, record.product_name, record.brand))
            continue
        records.extend(record.records())
    return Yield(records=tuple(records), follow=tuple(follow))


def _review_fetch(
    goods_number: str,
    sort: str,
    *,
    page: int = 0,
    cursor: tuple[object, object] | None = None,
    pages: int = REVIEW_PAGES,
    dataset: Dataset = Dataset.REVIEW,
) -> Fetch:
    body: dict[str, object] = {
        "goodsNumber": goods_number,
        "size": REVIEW_PAGE_SIZE,
        "sortType": sort,
        "reviewType": "ALL",
    }
    if cursor is None:
        body["page"] = 0
    else:
        body["cursorId"], body["cursorScore"] = cursor
    return Fetch(
        url=REVIEW_ENDPOINT,
        dataset=dataset,
        method="POST",
        transport=Transport.HTTP,
        headers=(
            ("Content-Type", "application/json"),
            ("Accept", "application/json"),
            ("Referer", f"https://m.oliveyoung.co.kr/m/goods/getGoodsDetail.do?goodsNo={goods_number}"),
        ),
        body=json.dumps(body).encode(),
        context=(
            ("kind", "reviews"),
            ("product", goods_number),
            ("sort", sort),
            ("page", str(page)),
            ("pages", str(pages)),
        ),
    )


def _stats_fetch(goods_number: str, *, dataset: Dataset = Dataset.REVIEW) -> Fetch:
    return Fetch(
        url=STATS_ENDPOINT.format(goods=goods_number),
        dataset=dataset,
        transport=Transport.HTTP,
        headers=(
            ("Accept", "application/json"),
            ("Referer", f"https://m.oliveyoung.co.kr/m/goods/getGoodsDetail.do?goodsNo={goods_number}"),
        ),
        context=(("kind", "stats"), ("product", goods_number)),
    )


def _summary_fetch(goods_number: str) -> Fetch:
    return Fetch(
        url=SUMMARY_ENDPOINT.format(goods=goods_number),
        dataset=Dataset.REVIEW,
        transport=Transport.HTTP,
        headers=(
            ("Accept", "application/json"),
            ("Referer", f"https://m.oliveyoung.co.kr/m/goods/getGoodsDetail.do?goodsNo={goods_number}"),
        ),
        context=(("kind", "summary"), ("product", goods_number)),
    )


def _product_fetch(product_key: str, name: str, brand: str | None) -> Fetch:
    return Fetch(
        url=DETAIL_URL.format(goods=product_key),
        dataset=Dataset.PRODUCT,
        transport=Transport.BROWSER,
        click_before="text=상품정보 제공고시",
        wait_for=f"text={INGREDIENTS_LABEL}",
        context=(
            ("kind", "product"),
            ("product", product_key),
            ("name", name),
            ("brand", brand or ""),
        ),
        depth=1,
    )


def _parse_product(payload: Payload, source: str) -> Yield:
    tree = HTMLParser(payload.text())
    ingredients: str | None = None
    for row in tree.css("tr"):
        th = row.css_first("th")
        if th is None or th.text(strip=True) != INGREDIENTS_LABEL:
            continue
        td = row.css_first("td")
        if td is not None:
            ingredients = td.text(separator="\n", strip=True) or None
        break

    product_key = payload.fetch.ctx("product") or ""
    record = ProductRecord(
        source=source,
        captured_at=payload.captured_at,  # pyright: ignore[reportArgumentType]
        product_key=product_key,
        name=payload.fetch.ctx("name") or "",
        brand=payload.fetch.ctx("brand") or None,
        ingredients=ingredients,
    )
    return Yield(records=(record,), follow=())


def _parse_summary(payload: Payload, source: str) -> Yield:
    try:
        data = json.loads(payload.body)
    except json.JSONDecodeError:
        return Yield()
    inner = data.get("data") if isinstance(data, dict) else None
    product_key = payload.fetch.ctx("product")
    if not isinstance(inner, dict) or not product_key:
        return Yield()

    records: list[Record] = []
    positive, negative = inner.get("positiveRatio"), inner.get("negativeRatio")
    if positive is not None or negative is not None:
        records.append(
            ReviewStatsRecord(
                source=source,
                captured_at=payload.captured_at,  # pyright: ignore[reportArgumentType]
                product_key=product_key,
                positive_pct=positive,
                negative_pct=negative,
            )
        )
    for index in range(1, SUMMARY_FEATURES + 1):
        title = inner.get(f"feature{index}Title")
        if not title:
            continue
        records.append(
            ReviewSummaryRecord(
                source=source,
                captured_at=payload.captured_at,  # pyright: ignore[reportArgumentType]
                product_key=product_key,
                rank=index,
                title=str(title),
                body=inner.get(f"feature{index}Description") or None,
            )
        )
    return Yield(records=tuple(records))


def _parse_stats(payload: Payload, source: str) -> Yield:
    try:
        data = json.loads(payload.body)
    except json.JSONDecodeError:
        return Yield()
    inner = data.get("data") if isinstance(data, dict) else None
    if not isinstance(inner, dict):
        return Yield()
    product_key = payload.fetch.ctx("product") or inner.get("goodsNumber")
    if not product_key:
        return Yield()

    distribution = inner.get("ratingDistribution")
    distribution = distribution if isinstance(distribution, dict) else {}
    buckets: dict[int, int | None] = {}
    for stat in distribution.get("ratingStatDtos") or []:
        if isinstance(stat, dict) and isinstance(stat.get("rating"), int):
            buckets[stat["rating"]] = stat.get("percentage")

    records: list[Record] = []
    records.append(
        ReviewStatsRecord(
            source=source,
            captured_at=payload.captured_at,  # pyright: ignore[reportArgumentType]
            product_key=str(product_key),
            review_count=inner.get("reviewCount"),
            rating_average=distribution.get("averageRating"),
            pct_5=buckets.get(5),
            pct_4=buckets.get(4),
            pct_3=buckets.get(3),
            pct_2=buckets.get(2),
            pct_1=buckets.get(1),
        )
    )
    records.extend(
        _survey_topics(
            inner.get("satisfactionStats"),
            source=source,
            captured_at=payload.captured_at,
            product_key=str(product_key),
        )
    )
    return Yield(records=tuple(records))


def _survey_topics(
    questions: object, *, source: str, captured_at: Any, product_key: str
) -> list[ReviewTopicRecord]:
    if not isinstance(questions, list):
        return []
    topics: list[ReviewTopicRecord] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        group = question.get("questionName")
        answers = question.get("answerDtos")
        if not isinstance(answers, list):
            continue
        rank = 0
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            code, name = answer.get("answerCode"), answer.get("answerName")
            if not code or not name:
                continue
            rank += 1
            topics.append(
                ReviewTopicRecord(
                    source=source,
                    captured_at=captured_at,
                    product_key=product_key,
                    topic_key=str(code),
                    topic_name=str(name),
                    topic_group=str(group) if group else None,
                    share_pct=answer.get("answerPercentage"),
                    rank=rank,
                )
            )
    return topics


def _parse_reviews(payload: Payload, source: str) -> Yield:
    try:
        data = json.loads(payload.body)
    except json.JSONDecodeError:
        return Yield()
    if not isinstance(data, dict):
        return Yield()
    inner = data.get("data")
    rows = inner.get("goodsReviewList") if isinstance(inner, dict) else None
    if not isinstance(rows, list):
        return Yield()

    product_key = payload.fetch.ctx("product")
    if not product_key:
        return Yield()

    records: list[Record] = []
    saw_three = False
    for row in rows:
        if not isinstance(row, dict) or row.get("reviewId") is None:
            continue
        profile = row.get("profileDto")
        nickname = profile.get("memberNickname") if isinstance(profile, dict) else None
        score = _number(row.get("reviewScore"))
        if score == 3:
            saw_three = True
        records.append(
            ReviewRecord(
                source=source,
                captured_at=payload.captured_at,  # pyright: ignore[reportArgumentType]
                product_key=product_key,
                review_key=str(row["reviewId"]),
                rating=score,
                body=review_text(row.get("content")),
                author_hash=author_hash(nickname),
                written_at=kst_date(row.get("createdDateTime"), "%Y.%m.%d"),
            )
        )
    # The low walk's real stop condition: RATING_ASC has reached a 3-star review, so the low end (1
    # and 2 star) is behind this page and the walk is done -- see LOW_ASC_PAGES_MAX. Scoped to
    # RATING_ASC: RATING_DESC's one page (LOW_DESC_PAGES) already stops on its own page cap, and a
    # DESC page's high ratings must not be read as "the low end ended here".
    stop_at_three = (
        saw_three
        and payload.fetch.dataset is Dataset.REVIEW_LOW
        and payload.fetch.ctx("sort") == "RATING_ASC"
    )
    return Yield(
        records=tuple(records),
        follow=_next_page(payload, inner, product_key, stop_at_three=stop_at_three),
    )


def _next_page(
    payload: Payload, inner: object, product_key: str, *, stop_at_three: bool = False
) -> tuple[Fetch, ...]:
    """The next page of this product's reviews, if the site offers one and nothing has ended the walk.

    Four things end it: the site saying there is no more, the site claiming more but handing back
    nothing to continue from, the page cap the request carries, and -- review_low only -- this page
    already containing a 3-star review.
    """
    if not isinstance(inner, dict):
        return ()
    if stop_at_three:
        return ()
    sort = payload.fetch.ctx("sort")
    page = _int(payload.fetch.ctx("page")) or 0
    pages = _int(payload.fetch.ctx("pages")) or REVIEW_PAGES
    cursor_id = inner.get("nextCursorId")
    cursor_score = inner.get("nextCursorScore")
    if not sort or page + 1 >= pages:
        return ()
    if inner.get("hasNext") is not True or cursor_id is None or cursor_score is None:
        return ()
    return (
        _review_fetch(
            product_key,
            sort,
            page=page + 1,
            cursor=(cursor_id, cursor_score),
            pages=pages,
            dataset=payload.fetch.dataset,
        ),
    )


def _int(value: str | None) -> int | None:
    try:
        return int(value)  # pyright: ignore[reportArgumentType]
    except (TypeError, ValueError):
        return None


def _number(value: object) -> float | None:
    try:
        return float(value)  # pyright: ignore[reportArgumentType]
    except (TypeError, ValueError):
        return None


def _to_record(
    item: Node, *, source: str, captured_at: Any, board: str, category_key: str, rank: int
) -> RankRecord | None:
    thumb = item.css_first("a.prd_thumb")
    goods_no = thumb.attributes.get("data-ref-goodsno") if thumb is not None else None
    if not goods_no:
        return None

    name = _text(item, "p.tx_name")
    if not name:
        return None

    listed = _money(item, "span.tx_org span.tx_num")
    current = _money(item, "span.tx_cur span.tx_num")
    price = current if current is not None else listed

    zzim = item.css_first("button.btn_zzim")
    category = zzim.attributes.get("data-ref-goodscategory") if zzim is not None else None

    return RankRecord(
        source=source,
        captured_at=captured_at,
        board=board,
        category_key=category_key,
        category_name=category or None,
        rank=rank,
        product_key=goods_no,
        product_name=name,
        brand=_text(item, "span.tx_brand"),
        price=price,
        discount_rate=_discount(listed, current),
        review_count=None,
        review_rating=None,
        rank_delta=None,
        is_new=None,
    )


def _discount(listed: int | None, current: int | None) -> int | None:
    if listed is None or current is None or listed <= 0 or current >= listed:
        return None
    return round((1 - current / listed) * 100)


def _text(item: Node, selector: str) -> str | None:
    node = item.css_first(selector)
    if node is None:
        return None
    return node.text(strip=True) or None


def _money(item: Node, selector: str) -> int | None:
    raw = _text(item, selector)
    if raw is None:
        return None
    digits = _DIGITS.sub("", raw)
    return int(digits) if digits else None
