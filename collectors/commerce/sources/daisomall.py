"""다이소몰 (daisomall.co.kr).

origin: service/trend-radar/src/trend_radar/sources/daisomall.py -- ported for #7, unchanged.

A Nuxt SPA whose ranking rows arrive over a public, unauthenticated XHR endpoint rather than in the
rendered shell, so this source calls it directly. `rank_delta` is left empty: the response's NOW_RANK
minus PRE_RANK was positive for essentially every row in the 2026-08-18 capture, which is not a real
movement -- this project's own hourly snapshots are the honest source for that.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar
from urllib.parse import urlencode

from collectors.commerce.contract import Fetch, Payload, Scope, SourcePolicy, Transport, Yield
from collectors.commerce.models import (
    Dataset,
    NewProductRecord,
    RankRecord,
    Record,
    ReviewAnswerRecord,
    ReviewRecord,
    ReviewTopicRecord,
)
from collectors.commerce.registry import register
from collectors.commerce.scrub import author_hash, kst_date, review_text

SEARCH_BASE = "https://www.daisomall.co.kr/ssn/search"

# 뷰티/위생, from POST https://www.daisomall.co.kr/api/ds/ctgr {"exhCnrId":"C105"} on 2026-08-18.
BEAUTY_CATEGORY_NO = "CTGR_01050"
BEAUTY_CATEGORY_NAME = "뷰티/위생"

# The site's own page size is 30; the endpoint honours 100.
PAGE_SIZE = 100

# Reviews live on the v2 API host; the route the deployed bundle names is stale and 404s.
REVIEW_ENDPOINT = "https://fapi.daisomall.co.kr/pd/pds/revw/selRevwList"
REVIEW_PAGE_SIZE = 100
REVIEW_PRODUCTS = 10
# `currentPage` genuinely advances here, unlike Olive Young's cursor -- the limit is the budget.
REVIEW_PAGES = 2
ATTR_ENDPOINT = "https://fapi.daisomall.co.kr/pd/pds/revw/selRevwAttr"


@dataclass(frozen=True, slots=True)
class _Board:
    name: str
    endpoint: str
    period: str | None  # R rising, D daily, W weekly. The review board has none.


_BOARDS: tuple[_Board, ...] = (
    _Board("sale_rising", "GoodsBestSale", period="R"),
    _Board("sale_daily", "GoodsBestSale", period="D"),
    _Board("sale_weekly", "GoodsBestSale", period="W"),
    _Board("review", "GoodsBestReview", period=None),
)

# 신상, the site's own new-arrivals board. Kept out of _BOARDS: it is ordered by recency, not sales.
_NEW_BOARDS: tuple[_Board, ...] = (
    _Board("new_rising", "GoodsBestNew", period="R"),
    _Board("new_daily", "GoodsBestNew", period="D"),
    _Board("new_weekly", "GoodsBestNew", period="W"),
)

# The review walk starts from one board, not four: the point is which products to ask about.
_REVIEW_BOARDS: tuple[_Board, ...] = (_BOARDS[1],)


@register
class DaisoMall:
    key: ClassVar[str] = "daisomall"
    datasets: ClassVar[frozenset[Dataset]] = frozenset({Dataset.RANKING, Dataset.REVIEW, Dataset.NEW_PRODUCT})
    scope: ClassVar[Scope] = MappingProxyType(
        {
            Dataset.RANKING: MappingProxyType({"boards": len(_BOARDS), "page_size": PAGE_SIZE}),
            Dataset.REVIEW: MappingProxyType(
                {
                    "boards": len(_REVIEW_BOARDS),
                    "page_size": PAGE_SIZE,
                    "review_products": REVIEW_PRODUCTS,
                    "review_pages": REVIEW_PAGES,
                    "review_page_size": REVIEW_PAGE_SIZE,
                }
            ),
            Dataset.NEW_PRODUCT: MappingProxyType(
                {"new_product_boards": len(_NEW_BOARDS), "page_size": PAGE_SIZE}
            ),
        }
    )
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        min_interval_s=30.0,
        concurrency=1,
        max_requests_per_run=40,
        transport=Transport.HTTP,
    )

    def seeds(self, dataset: Dataset, *, board: str | None = None) -> Sequence[Fetch]:
        del board  # unused: this source declares no REVIEW_LOW
        if dataset is Dataset.RANKING:
            return tuple(_seed(board, dataset) for board in _BOARDS)
        if dataset is Dataset.REVIEW:
            return tuple(_seed(board, dataset) for board in _REVIEW_BOARDS)
        if dataset is Dataset.NEW_PRODUCT:
            return tuple(_seed(board, dataset, kind="new") for board in _NEW_BOARDS)
        return ()

    def parse(self, payload: Payload) -> Yield:
        kind = payload.fetch.ctx("kind")
        if kind == "reviews":
            return _parse_reviews(payload, source=self.key)
        if kind == "attrs":
            return _parse_attrs(payload, source=self.key)
        if kind == "new":
            return _parse_new_products(payload, source=self.key)
        return _parse_ranking(payload, source=self.key)


def _parse_ranking(payload: Payload, source: str) -> Yield:
    documents = _documents(payload.body)
    board = payload.fetch.ctx("board") or "sale_daily"
    wants_reviews = payload.fetch.dataset is Dataset.REVIEW

    records: list[Record] = []
    follow: list[Fetch] = []
    rank = 0
    for document in documents:
        record = _to_record(
            document, source=source, captured_at=payload.captured_at, board=board, rank=rank + 1
        )
        if record is None:
            continue
        rank += 1
        if wants_reviews:
            if rank <= REVIEW_PRODUCTS:
                follow.append(_review_fetch(record.product_key))
                follow.append(_attr_fetch(record.product_key))
            continue
        records.extend(record.records())
    return Yield(records=tuple(records), follow=tuple(follow))


def _parse_new_products(payload: Payload, source: str) -> Yield:
    """The 신상 board: what the site itself says is new. No rank, no price -- both belong to the
    sales boards' own meaning."""
    records: list[Record] = []
    for document in _documents(payload.body):
        record = _to_new_product(document, source=source, captured_at=payload.captured_at)
        if record is None:
            continue
        records.extend(record.records())
    return Yield(records=tuple(records))


def _to_new_product(document: object, *, source: str, captured_at: Any) -> NewProductRecord | None:
    if not isinstance(document, dict) or not document.get("pdNo"):
        return None
    return NewProductRecord(
        source=source,
        captured_at=captured_at,
        product_key=str(document["pdNo"]),
        name=str(document.get("pdNm") or ""),
        brand=_text(document.get("brndNm")),
        # SLE_ST_DTM is empty on every measured row; the run's hour is not a listing date.
        listed_at=None,
    )


def _review_fetch(product_key: str, page: int = 1) -> Fetch:
    body = {
        "pdNo": product_key,
        "pageSize": REVIEW_PAGE_SIZE,
        "currentPage": page,
        "filter": "ALL",
        # Newest first; the site's own default ("RCM") returns the same reviews every hour.
        "sortCond": "NEW",
        "useCommonPaging": False,
        "cttsOnlyYn": "N",
        "onldPdNoList": [],
    }
    return Fetch(
        url=REVIEW_ENDPOINT,
        dataset=Dataset.REVIEW,
        method="POST",
        headers=(
            ("Content-Type", "application/json"),
            ("Accept", "application/json"),
            ("Referer", f"https://www.daisomall.co.kr/pd/pdr/SCR_PDR_0001?pdNo={product_key}"),
        ),
        body=json.dumps(body).encode(),
        context=(
            ("kind", "reviews"),
            ("product", product_key),
            ("page", str(page)),
            ("size", str(REVIEW_PAGE_SIZE)),
        ),
    )


def _attr_fetch(product_key: str) -> Fetch:
    return Fetch(
        url=ATTR_ENDPOINT,
        dataset=Dataset.REVIEW,
        method="POST",
        headers=(
            ("Content-Type", "application/json"),
            ("Accept", "application/json"),
            ("Referer", f"https://www.daisomall.co.kr/pd/pdr/SCR_PDR_0001?pdNo={product_key}"),
        ),
        body=json.dumps({"pdNo": product_key}).encode(),
        context=(("kind", "attrs"), ("product", product_key)),
    )


def _parse_attrs(payload: Payload, source: str) -> Yield:
    """The reviewer survey, walked by slot index and stopped at the first empty name; keyed on the
    question code plus the answer's own name so a reordered option keeps its own history."""
    try:
        data = json.loads(payload.body)
    except json.JSONDecodeError:
        return Yield()
    inner = data.get("data") if isinstance(data, dict) else None
    questions = inner.get("pdRevwAttr") if isinstance(inner, dict) else None
    product_key = payload.fetch.ctx("product")
    if not isinstance(questions, list) or not product_key:
        return Yield()

    records: list[Record] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        code = question.get("revwIemCd")
        group = question.get("revwIemNm")
        if not code:
            continue
        rank = 0
        for slot in range(1, 6):
            name = question.get(f"revwAttrNm{slot}")
            if not name:
                continue
            rank += 1
            records.append(
                ReviewTopicRecord(
                    source=source,
                    captured_at=payload.captured_at,  # pyright: ignore[reportArgumentType]
                    product_key=product_key,
                    topic_key=f"{code}:{name}",
                    topic_name=str(name),
                    topic_group=str(group) if group else None,
                    sentence=question.get("qsnCn") or None,
                    share_pct=question.get(f"perc{slot}"),
                    review_count=question.get(f"attr{slot}"),
                    rank=rank,
                )
            )
    return Yield(records=tuple(records))


def _parse_reviews(payload: Payload, source: str) -> Yield:
    try:
        data = json.loads(payload.body)
    except json.JSONDecodeError:
        return Yield()
    if not isinstance(data, dict):
        return Yield()
    inner = data.get("data")
    rows = inner.get("pdRevwList") if isinstance(inner, dict) else None
    if not isinstance(rows, list):
        return Yield()

    fallback_product = payload.fetch.ctx("product")
    records: list[Record] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        review_key = row.get("revwMngNo")
        product_key = row.get("pdNo") or fallback_product
        if review_key is None or not product_key:
            continue
        records.append(
            ReviewRecord(
                source=source,
                captured_at=payload.captured_at,  # pyright: ignore[reportArgumentType]
                product_key=str(product_key),
                review_key=str(review_key),
                rating=_float(row.get("stscVal")),
                body=review_text(row.get("revwCn")),
                author_hash=author_hash(row.get("mbEid")),
                written_at=kst_date(row.get("revwRgDtm")),
            )
        )
        records.extend(
            _answers(
                row.get("attrs"),
                source=source,
                captured_at=payload.captured_at,
                review_key=str(review_key),
                product_key=str(product_key),
            )
        )
    return Yield(records=tuple(records), follow=_next_page(payload, len(rows)))


def _next_page(payload: Payload, rows: int) -> tuple[Fetch, ...]:
    """No total to check against -- totalCnt/dataAllTotal are both zero on every capture -- so a short
    page is the only honest end-of-reviews signal, alongside REVIEW_PAGES capping the budget."""
    product_key = payload.fetch.ctx("product")
    page = _int(payload.fetch.ctx("page")) or 1
    asked = _int(payload.fetch.ctx("size")) or REVIEW_PAGE_SIZE
    if not product_key or rows < asked or page >= REVIEW_PAGES:
        return ()
    return (_review_fetch(product_key, page=page + 1),)


def _answers(
    raw: object, *, source: str, captured_at: Any, review_key: str, product_key: str
) -> list[ReviewAnswerRecord]:
    if not isinstance(raw, list):
        return []
    answers: list[ReviewAnswerRecord] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        code = entry.get("revwIemCd")
        if not code:
            continue
        answers.append(
            ReviewAnswerRecord(
                source=source,
                captured_at=captured_at,
                review_key=review_key,
                product_key=product_key,
                question_key=str(code),
                question_name=entry.get("revwIemNm") or None,
                answer=entry.get("chocAns") or None,
            )
        )
    return answers


def _seed(board: _Board, dataset: Dataset, kind: str = "ranking") -> Fetch:
    params: dict[str, str] = {
        "pageNum": "1",
        "cntPerPage": str(PAGE_SIZE),
        "largeExhCtgrNo": BEAUTY_CATEGORY_NO,
        "isCategory": "0",
    }
    if board.period is not None:
        params["period"] = board.period
    if board.endpoint == "GoodsBestSale":
        params["soldOutYn"] = "N"
    return Fetch(
        url=f"{SEARCH_BASE}/{board.endpoint}?{urlencode(params)}",
        dataset=dataset,
        headers=(
            ("Accept", "application/json"),
            ("Referer", "https://www.daisomall.co.kr/ds/rank/C105"),
        ),
        context=(("kind", kind), ("board", board.name)),
    )


def _documents(body: bytes) -> list[Any]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    result_set = data.get("resultSet")
    results = result_set.get("result") if isinstance(result_set, dict) else None
    if not isinstance(results, list) or not results:
        return []
    first = results[0]
    documents = first.get("resultDocuments") if isinstance(first, dict) else None
    return documents if isinstance(documents, list) else []


def _to_record(
    document: object, *, source: str, captured_at: Any, board: str, rank: int
) -> RankRecord | None:
    if not isinstance(document, dict) or not document.get("pdNo"):
        return None
    return RankRecord(
        source=source,
        captured_at=captured_at,
        board=board,
        category_key=BEAUTY_CATEGORY_NO,
        category_name=BEAUTY_CATEGORY_NAME,
        rank=rank,
        product_key=str(document["pdNo"]),
        product_name=str(document.get("pdNm") or ""),
        brand=_text(document.get("brndNm")),
        price=_int(document.get("pdPrc")),
        review_count=_int(document.get("revwCnt")),
        review_rating=_float(document.get("avgStscVal")),
        rank_delta=None,
        is_new=document.get("newPdYn") == "Y",
    )


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _int(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None
