"""글로우픽 (glowpick.com) -- category rankings, over plain HTTP.

origin: service/trend-radar/src/trend_radar/sources/glowpick.py -- ported for #7, unchanged.

The User-Agent must be honest rather than a copied Chrome string: Glowpick's WAF refuses a request that
claims `Chrome/` without the client hints a real Chrome sends, so an honest agent is what actually gets
served. The page is an app-router document: the data arrives as `self.__next_f.push` flight chunks, and
a ranked product's `productRank` is an object rather than null.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from types import MappingProxyType
from typing import Any, ClassVar

from collectors.commerce.contract import Fetch, Payload, Scope, SourcePolicy, Transport, Yield
from collectors.commerce.models import Dataset, NewProductRecord, RankRecord, Record, ReviewRecord
from collectors.commerce.registry import register
from collectors.commerce.scrub import author_hash, kst_date

CATEGORY_URL = "https://www.glowpick.com/categories/{category}?monthTerm={months}"
PRODUCT_URL = "https://www.glowpick.com/products/{product}"

# The site's own new-product pages, from its sitemap -- not the same twenty products.
NEW_PRODUCT_BOARDS: tuple[tuple[str, str], ...] = (
    ("brand_new", "https://www.glowpick.com/products/brand-new"),
    ("brand_new_monthly", "https://www.glowpick.com/products/brand-new/monthly"),
)

# 24 months is what the site advertises in its own sitemap for every category.
MONTH_TERM = 24

# Twelve of the site's 103 categories, transcribed from sitemap-categories.xml on 2026-08-19 to cover
# each area of the catalogue once without pulling the full 80MB/hour set.
BOARDS: tuple[tuple[str, str], ...] = (
    ("3", "에센스/세럼"),
    ("4", "크림"),
    ("32", "페이셜클렌저"),
    ("37", "시트마스크"),
    ("41", "선크림"),
    ("7", "파운데이션"),
    ("15", "립틴트/라커"),
    ("22", "아이섀도우"),
    ("26", "블러셔"),
    ("60", "샴푸"),
    ("49", "바디로션/크림"),
    ("83", "향수"),
)

_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)', re.DOTALL)
_TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
_TITLE_TAIL = re.compile(r"\s*추천 제품 TOP\s*\d+.*$")
_RANKED = re.compile(r'"productRank":\{')
_REVIEW = re.compile(r'"idreviewcomment":')
_WRITTEN_FORMAT = "%Y-%m-%d %H:%M:%S"


@register
class Glowpick:
    key: ClassVar[str] = "glowpick"
    datasets: ClassVar[frozenset[Dataset]] = frozenset({Dataset.RANKING, Dataset.REVIEW, Dataset.NEW_PRODUCT})
    # RANKING is in here on purpose: `parse()` below never looks at `payload.fetch.dataset`
    # (beyond splitting off NEW_PRODUCT) and calls `_reviews(...)` unconditionally, so a ranking
    # run writes review bodies too. The cron runs ranking hourly and review once a day and the
    # review upsert is DO NOTHING, which makes the hourly ranking run the *first writer* of most
    # rows -- needs' collection_lineage would call 63.5% of this site's reviews "unknown" without it.
    review_body_datasets: ClassVar[frozenset[Dataset]] = frozenset({Dataset.RANKING, Dataset.REVIEW})
    # Both RANKING and REVIEW are the same category page (one request, both datasets), so both carry
    # the same board/window counts -- a review run that recorded none would describe a collection that
    # did not happen.
    _CATEGORY_WALK = MappingProxyType({"boards": len(BOARDS), "month_term_months": MONTH_TERM})
    scope: ClassVar[Scope] = MappingProxyType(
        {
            Dataset.RANKING: _CATEGORY_WALK,
            Dataset.REVIEW: _CATEGORY_WALK,
            Dataset.NEW_PRODUCT: MappingProxyType({"new_product_boards": len(NEW_PRODUCT_BOARDS)}),
        }
    )
    policy: ClassVar[SourcePolicy] = SourcePolicy(
        min_interval_s=5.0,
        concurrency=1,
        max_requests_per_run=20,
        timeout_s=30.0,
        transport=Transport.HTTP,
    )

    def seeds(self, dataset: Dataset, *, board: str | None = None) -> Sequence[Fetch]:
        del board  # unused: this source declares no REVIEW_LOW
        if dataset is Dataset.NEW_PRODUCT:
            return tuple(
                Fetch(url=url, dataset=dataset, context=(("board", board),))
                for board, url in NEW_PRODUCT_BOARDS
            )
        if dataset not in (Dataset.RANKING, Dataset.REVIEW):
            return ()
        return tuple(
            Fetch(
                url=CATEGORY_URL.format(category=key, months=MONTH_TERM),
                dataset=dataset,
                context=(("board", "category"), ("category", key), ("name", name)),
            )
            for key, name in BOARDS
        )

    def parse(self, payload: Payload) -> Yield:
        text = payload.text()
        flight = _flight_payload(text)
        if not flight:
            return Yield()

        if payload.fetch.dataset is Dataset.NEW_PRODUCT:
            return _parse_new_products(flight, source=self.key, captured_at=payload.captured_at)

        category_key = payload.fetch.ctx("category") or ""
        category_name = _category_name(text) or payload.fetch.ctx("name")

        records: list[Record] = []
        for row in _ranked_products(flight):
            record = _to_record(
                row,
                source=self.key,
                captured_at=payload.captured_at,
                category_key=category_key,
                category_name=category_name,
            )
            if record is None:
                continue
            records.append(record)
            records.append(
                record.to_product(
                    volume=_text(row.get("volume")), url=PRODUCT_URL.format(product=record.product_key)
                )
            )
            price = record.to_price()
            if price is not None:
                records.append(price)

        records.extend(_reviews(flight, source=self.key, captured_at=payload.captured_at))
        return Yield(records=tuple(records))


def _parse_new_products(flight: str, *, source: str, captured_at: Any) -> Yield:
    """The brand-new boards. Rank is a recommendation order, not sales position, so it is not stored;
    the review feed this page carries is left alone -- REVIEW already walks it from the category pages."""
    records: list[Record] = []
    for row in _ranked_products(flight):
        product_key = _text(row.get("idProduct"))
        name = _text(row.get("productTitle"))
        if _wrapped_int(row.get("productRank")) is None or not product_key or not name:
            continue
        brand = row.get("brand")
        record = NewProductRecord(
            source=source,
            captured_at=captured_at,
            product_key=product_key,
            name=name,
            brand=_text(brand.get("brandTitle")) if isinstance(brand, dict) else None,
            listed_at=None,
        )
        records.extend(
            record.records(volume=_text(row.get("volume")), url=PRODUCT_URL.format(product=product_key))
        )
    return Yield(records=tuple(records))


def _reviews(flight: str, *, source: str, captured_at: Any) -> Iterator[ReviewRecord]:
    """The recent-review feed the category page carries alongside its ranking -- free, no extra request."""
    for match in _REVIEW.finditer(flight):
        span = _enclosing_object(flight, match.start())
        if span is None:
            continue
        try:
            row = json.loads(span)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue

        review_key = _text(row.get("idreviewcomment"))
        body = _text(row.get("reviewText"))
        product = row.get("product")
        product_key = _text(product.get("idProduct")) if isinstance(product, dict) else None
        if not review_key or not body or not product_key:
            continue

        editor = row.get("editor")
        author = _text(editor.get("idRegister")) if isinstance(editor, dict) else None

        yield ReviewRecord(
            source=source,
            captured_at=captured_at,
            product_key=product_key,
            review_key=review_key,
            rating=_number(row.get("userRating")),
            body=body,
            author_hash=author_hash(author),
            written_at=kst_date(row.get("createDate"), _WRITTEN_FORMAT),
        )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _flight_payload(text: str) -> str:
    """The app-router flight chunks, unescaped and joined -- an object can straddle two chunks."""
    parts: list[str] = []
    for chunk in _FLIGHT.findall(text):
        try:
            parts.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    return "".join(parts)


def _category_name(text: str) -> str | None:
    match = _TITLE.search(text)
    if match is None:
        return None
    return _TITLE_TAIL.sub("", match.group(1)).strip() or None


def _ranked_products(flight: str) -> Iterator[dict[str, Any]]:
    """Found by locating the key and walking out to the enclosing braces, since the array's path
    through the payload changes shape whenever the page gains a section."""
    for match in _RANKED.finditer(flight):
        span = _enclosing_object(flight, match.start())
        if span is None:
            continue
        try:
            row = json.loads(span)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            yield row


def _enclosing_object(text: str, index: int) -> str | None:
    start = index
    depth = 0
    while start > 0:
        start -= 1
        if text[start] == "}":
            depth += 1
        elif text[start] == "{":
            if depth == 0:
                break
            depth -= 1
    else:
        return None

    depth = 0
    for end in range(start, len(text)):
        if text[end] == "{":
            depth += 1
        elif text[end] == "}":
            depth -= 1
            if depth == 0:
                return text[start : end + 1]
    return None


def _to_record(
    row: dict[str, Any], *, source: str, captured_at: Any, category_key: str, category_name: str | None
) -> RankRecord | None:
    rank = _wrapped_int(row.get("productRank"))
    product_key = _text(row.get("idProduct"))
    name = _text(row.get("productTitle"))
    if rank is None or not product_key or not name:
        return None

    brand = row.get("brand")
    return RankRecord(
        source=source,
        captured_at=captured_at,
        board="category",
        category_key=category_key,
        category_name=category_name,
        rank=rank,
        product_key=product_key,
        product_name=name,
        brand=_text(brand.get("brandTitle")) if isinstance(brand, dict) else None,
        price=_positive_int(row.get("price")),
        review_count=_wrapped_int(row.get("reviewCount")),
        review_rating=_rating(row.get("ratingAvg")),
        rank_delta=_wrapped_int(row.get("rankChange")),
        is_new=None,
    )


def _wrapped_int(value: object) -> int | None:
    """Several fields arrive as `{"value": n}` rather than as `n`."""
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _positive_int(value: object) -> int | None:
    number = _wrapped_int(value)
    return number if number and number > 0 else None


def _rating(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return round(float(value), 2) or None


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
