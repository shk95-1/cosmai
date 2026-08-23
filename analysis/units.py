"""원천 행 → TextUnit. 시간 규칙과 카테고리 유도는 contracts/formats.md 가 정본이다."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, LiteralString

import psycopg

from analysis.types import TextUnit

LEAF_SEPARATOR = " > "
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
    """사이트 카테고리는 ' > ' 로 이어진 경로다 — 사전 키는 그 마지막 조각이다."""
    return (category or "").split(LEAF_SEPARATOR)[-1].strip()


@dataclass(frozen=True)
class CategoryRule:
    site: str
    pattern: re.Pattern[str]
    lexicon_category: str


@dataclass(frozen=True)
class CategoryMap:
    """needs.category_map (A18). 사이트 카테고리 leaf → 없으면 제품명 정규식 → 그래도 없으면 없음."""

    exact: Mapping[tuple[str, str], str]
    keywords: tuple[CategoryRule, ...]

    def lexicon_category(self, site: str, category: str | None, product_name: str | None) -> str | None:
        found = leaf(category)
        if found:
            # 표에 없는 leaf 는 항등이다 (formats.md) — 사전이 그 이름으로 카테고리 행을 가질 수 있다.
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
        # 정규식은 서로 겹치므로 priority 오름차순으로 먼저 맞는 것이 이긴다 (SQL 의 ORDER BY).
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
    assert day is not None  # captured_at 은 NOT NULL 이다 (contracts/ddl/current/app.trend_radar.sql)
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
    """댓글 시각은 상대시간 복원이라 해상도가 낮다. 폴백은 수집 시각이고 그때는 일 단위다."""
    published = as_day(published_at)
    day = published or as_day(first_seen_at)
    assert day is not None  # first_seen_at 은 NOT NULL 이다 (contracts/ddl/current/app.tubedepth.sql)
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
