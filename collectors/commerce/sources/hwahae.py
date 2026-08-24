"""화해 (hwahae.co.kr).

origin: service/trend-radar/src/trend_radar/sources/hwahae.py -- ported for #7, unchanged.

The home page's `__NEXT_DATA__` carries four complete ranking boards (trending, category, skin, age) in
one request, each with brand, price, discount, review count/rating and rank movement -- so this source
starts and stops there rather than rendering anything else.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from types import MappingProxyType
from typing import Any, ClassVar

from collectors.commerce.contract import Fetch, Payload, Scope, SourcePolicy, Transport, Yield
from collectors.commerce.models import Dataset, RankRecord, Record, ReviewTopicRecord
from collectors.commerce.registry import register

HOME = "https://www.hwahae.co.kr/"

_NEXT_DATA = re.compile(rb'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)

# board name -> (key holding the rows, key holding that board's theme metadata).
_BOARDS: tuple[tuple[str, str, str | None], ...] = (
    ("trending", "trendingRankingProducts", None),
    ("category", "recommendCategoryRankingProducts", "recommendCategoryThemeDetail"),
    ("skin", "skinRankingProducts", "skinThemeDetailData"),
    ("age", "ageRankingProducts", "ageThemeDetailData"),
)


@register
class Hwahae:
    key: ClassVar[str] = "hwahae"
    datasets: ClassVar[frozenset[Dataset]] = frozenset({Dataset.RANKING})
    scope: ClassVar[Scope] = MappingProxyType({Dataset.RANKING: MappingProxyType({"boards": len(_BOARDS)})})
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        min_interval_s=1.0,
        concurrency=2,
        # Added for #10 (사용자 승인 2026-08-24): with a live transport, a source with no ceiling is
        # a run with no worst case. This one walks RANKING from a single seed and production has
        # measured exactly one request per run, so 20 is twenty times the observed shape rather
        # than a guess -- and 19 x 1.0s of wall clock if it ever ran into it, which is nothing next
        # to the ranking walk it shares an hour with. Same number as glowpick, so no new constant.
        max_requests_per_run=20,
        transport=Transport.HTTP,
    )

    def seeds(self, dataset: Dataset, *, board: str | None = None) -> Sequence[Fetch]:
        del board  # unused: this source declares no REVIEW_LOW
        if dataset is not Dataset.RANKING:
            return ()
        return (Fetch(url=HOME, dataset=dataset),)

    def parse(self, payload: Payload) -> Yield:
        page_props = _page_props(payload.body)
        if page_props is None:
            return Yield()

        records: list[Record] = []
        for board, rows_key, theme_key in _BOARDS:
            rows = _rows(page_props.get(rows_key))
            if not rows:
                continue
            category_key, category_name = _theme(page_props, board, theme_key)
            rank = 0
            for row in rows:
                record = _to_record(
                    row,
                    source=self.key,
                    captured_at=payload.captured_at,
                    board=board,
                    category_key=category_key,
                    category_name=category_name,
                    rank=rank + 1,
                )
                if record is None:
                    continue
                rank += 1
                records.append(record)
                records.append(record.to_product(volume=_package_info(row)))
                price = record.to_price()
                if price is not None:
                    records.append(price)
                records.extend(
                    _topics(
                        row,
                        source=self.key,
                        captured_at=payload.captured_at,
                        product_key=record.product_key,
                    )
                )
        return Yield(records=tuple(records))


def _page_props(body: bytes) -> dict[str, Any] | None:
    match = _NEXT_DATA.search(body)
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    props = data.get("props") if isinstance(data, dict) else None
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    return page_props if isinstance(page_props, dict) else None


def _rows(board: object) -> list[Any]:
    if not isinstance(board, dict):
        return []
    rows = board.get("data")
    return rows if isinstance(rows, list) else []


def _theme(page_props: dict[str, Any], board: str, theme_key: str | None) -> tuple[str, str | None]:
    if theme_key is None:
        return str(page_props.get("trendingThemeId") or board), None
    theme = page_props.get(theme_key)
    if isinstance(theme, dict) and isinstance(theme.get("data"), dict):
        theme = theme["data"]
    if not isinstance(theme, dict):
        return board, None
    return str(theme.get("id") or board), theme.get("name")


def _package_info(row: object) -> str | None:
    """`product.package_info` is the real volume; `goods.capacity` can hold a gift-set description."""
    if not isinstance(row, dict):
        return None
    product = row.get("product")
    if not isinstance(product, dict):
        return None
    value = product.get("package_info")
    return str(value) if value else None


def _topics(row: object, *, source: str, captured_at: Any, product_key: str) -> list[ReviewTopicRecord]:
    if not isinstance(row, dict):
        return []
    product = row.get("product")
    raw = product.get("product_topics") if isinstance(product, dict) else None
    if not isinstance(raw, list):
        return []

    topics: list[ReviewTopicRecord] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        topic = entry.get("review_topic")
        if not isinstance(topic, dict) or topic.get("id") is None:
            continue
        name = topic.get("name")
        if not name:
            continue
        topics.append(
            ReviewTopicRecord(
                source=source,
                captured_at=captured_at,
                product_key=product_key,
                topic_key=str(topic["id"]),
                topic_name=str(name),
                sentence=topic.get("sentence") or None,
                is_positive=entry.get("is_positive"),
                score=entry.get("score"),
                review_count=entry.get("review_count"),
                rank=len(topics) + 1,
            )
        )
    return topics


def _to_record(
    row: object,
    *,
    source: str,
    captured_at: Any,
    board: str,
    category_key: str,
    category_name: str | None,
    rank: int,
) -> RankRecord | None:
    if not isinstance(row, dict):
        return None
    product = row.get("product")
    if not isinstance(product, dict) or product.get("id") is None:
        return None

    goods = row.get("goods") if isinstance(row.get("goods"), dict) else None
    brand = row.get("brand") if isinstance(row.get("brand"), dict) else None

    return RankRecord(
        source=source,
        captured_at=captured_at,
        board=board,
        category_key=category_key,
        category_name=category_name,
        rank=rank,
        product_key=str(product["id"]),
        product_name=str(product.get("name") or ""),
        brand=brand.get("name") if brand else None,
        price=goods.get("price") if goods else product.get("price"),
        discount_rate=goods.get("discount_rate") if goods else None,
        review_count=product.get("review_count"),
        review_rating=product.get("review_rating"),
        rank_delta=row.get("rank_delta"),
        is_new=row.get("is_rank_new"),
    )
