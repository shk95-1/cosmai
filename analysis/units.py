"""A source row -> a TextUnit. The time rules and the category derivation are canonical in
contracts/formats.md."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, LiteralString

import psycopg

from analysis.types import TextUnit

LEAF_SEPARATOR = " > "
# contracts/formats.md §카테고리 표기 (#123): 정본은 사이트가 발행한 경로 원문 하나뿐이고, 세 자리가
# 같은 문자열을 쓴다. 값이 md 와 여기 둘 다에 있어야 tests/test_category_canonical.py 가 대조할 수 있다.
CATEGORY_CANONICAL_SOURCE = "trend_radar.rank_snapshot.category_name"
CATEGORY_CANONICAL_COLUMNS = (
    "needs.need_mention.category",
    "needs.product_denominator.category",
    "needs.metrics_need.scope",
)
ANY_SITE = "*"
# formats.md §시간: 상대시간에서 복원한 댓글 시각은 2025-09 이후만 월 단위로 믿는다.
YOUTUBE_MONTH_FROM = date(2025, 9, 1)

CATEGORY_MAP: LiteralString = """
SELECT site, source_category, lexicon_category, method, priority FROM category_map
ORDER BY priority, site, source_category
"""


def month_of(day: date) -> str:
    return day.strftime("%Y-%m")


def comment_resolution(day: date) -> str:
    return "month" if day >= YOUTUBE_MONTH_FROM else "year"


def as_day(value: datetime | date | None) -> date | None:
    return value.date() if isinstance(value, datetime) else value


def leaf(category: str | None) -> str:
    """A site category is a path joined by ' > ' -- the dictionary key is its last piece."""
    return (category or "").split(LEAF_SEPARATOR)[-1].strip()


@dataclass(frozen=True)
class CategoryRule:
    site: str
    pattern: re.Pattern[str]
    lexicon_category: str


@dataclass(frozen=True)
class CategoryMap:
    """needs.category_map (A18). The leaf of the site category -> failing that a product-name regex -> failing
    that, nothing."""

    exact: Mapping[tuple[str, str], str]
    keywords: tuple[CategoryRule, ...]

    def lexicon_category(self, site: str, category: str | None, product_name: str | None) -> str | None:
        found = leaf(category)
        if found:
            # A leaf not in the table is the identity (formats.md) -- the dictionary can hold a category row
            # under that name.
            return self.exact.get((site, found)) or self.exact.get((ANY_SITE, found)) or found
        if not product_name:
            return None
        for rule in self.keywords:
            if rule.site in (site, ANY_SITE) and rule.pattern.search(product_name):
                return rule.lexicon_category
        return None


def load_category_map(conn: psycopg.Connection[Any]) -> CategoryMap:
    with conn.cursor() as cur:
        cur.execute(CATEGORY_MAP)
        rows: Sequence[Sequence[Any]] = cur.fetchall()
    return CategoryMap(
        exact={(r[0], r[1]): r[2] for r in rows if r[3] == "rank_snapshot"},
        # The regexes overlap, so in ascending priority the first match wins (the ORDER BY of the SQL).
        keywords=tuple(CategoryRule(r[0], re.compile(r[1]), r[2]) for r in rows if r[3] == "name_keyword"),
    )


def review_unit(
    *,
    source: str,
    product_key: str,
    review_key: str,
    body: str | None,
    rating: float | None,
    written_at: datetime | date | None,
    captured_at: datetime | date,
    category: str | None,
) -> TextUnit:
    """리뷰는 일 단위다. written_at 이 NULL 이면 captured_at 으로 폴백한다 (formats.md §시간)."""
    day = as_day(written_at) or as_day(captured_at)
    assert day is not None  # captured_at is NOT NULL (contracts/ddl/current/app.trend_radar.sql)
    return TextUnit(
        src="review",
        site=source,
        ref=f"{product_key}/{review_key}",
        text=body or "",
        observed_at=day,
        observed_at_resolution="day",
        rating=rating,
        product_key=product_key,
        category=category,
    )


def comment_unit(
    *,
    video_id: str,
    comment_id: str,
    text: str,
    like_count: int | None,
    published_at: datetime | date | None,
    first_seen_at: datetime | date,
    channel_id: str | None = None,
    view_count: int | None = None,
) -> TextUnit:
    """A comment timestamp is restored from relative time, so its resolution is low. The fallback is the
    collection time and that is at day resolution."""
    published = as_day(published_at)
    day = published or as_day(first_seen_at)
    assert day is not None  # first_seen_at is NOT NULL (contracts/ddl/current/app.tubedepth.sql)
    return TextUnit(
        src="yt_comment",
        site="youtube",
        ref=f"{video_id}/{comment_id}",
        text=text,
        observed_at=day,
        observed_at_resolution=comment_resolution(day) if published else "day",
        like_count=like_count,
        view_count=view_count,
        channel_id=channel_id,
    )
