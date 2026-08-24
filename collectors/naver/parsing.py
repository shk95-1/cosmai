"""Response bodies into records. Origin: apps/addons/normalizer.naver.{trend,blog}'s field-by-field
reading (service/cosmai) -- the two rules that survive here (markup removal, `yyyymmdd` parsing) are
unchanged; the Raw/Normalized envelope and DP-030's fallback-and-continue machinery are not carried
forward (issue #9 judgment (a): no job-queue platform in this repo). A malformed item is skipped, not
substituted with nulls -- there is no `notes.normalize_error` column to record a fallback into, and a
skipped point/post is a natural-key upsert away from being picked up cleanly on the next run.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from collectors.naver.models import BlogPost, DatalabPoint

_TAG = re.compile(r"<[^>]+>")
_POSTDATE = re.compile(r"^(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})$")


def _plain(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return html.unescape(_TAG.sub("", value)).strip()


def _iso_date(value: object) -> date | None:
    """`yyyymmdd` -> `date`, or `None` -- a guessed date is a fact nobody can trace back."""
    if not isinstance(value, str):
        return None
    matched = _POSTDATE.match(value.strip())
    if matched is None:
        return None
    year, month, day = (int(matched.group(name)) for name in ("year", "month", "day"))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_datalab_response(
    body: dict[str, Any], *, category: str, captured_at: datetime
) -> list[DatalabPoint]:
    """`{"results": [{"title", "keywords", "data": [{"period", "ratio"}]}]}` -> one `DatalabPoint`
    per (series, period). `period` is the window's first day (`yyyy-mm-dd` for time_unit=month);
    only the month is kept, per contracts/formats.md's monthly aggregation grain."""
    results = body.get("results")
    if not isinstance(results, list):
        return []
    points: list[DatalabPoint] = []
    for series in results:
        if not isinstance(series, dict):
            continue
        group_key = series.get("title")
        if not isinstance(group_key, str) or not group_key:
            continue
        terms = tuple(str(t) for t in series.get("keywords") or [])
        for point in series.get("data") or []:
            if not isinstance(point, dict):
                continue
            period = point.get("period")
            ratio = point.get("ratio")
            if not isinstance(period, str) or len(period) < 7:
                continue
            if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
                ratio = None
            points.append(
                DatalabPoint(
                    category=category,
                    group_key=group_key,
                    month=period[:7],
                    ratio=float(ratio) if ratio is not None else None,
                    terms=terms,
                    captured_at=captured_at,
                )
            )
    return points


def parse_blog_response(
    body: dict[str, Any],
    *,
    category: str | None,
    group_key: str | None,
    query: str | None,
    captured_at: datetime,
) -> list[BlogPost]:
    """`{"items": [{"title", "link", "description", "bloggername", "postdate"}]}` -> one `BlogPost`
    per item with a usable `link` (the natural key; an item without one carries nothing to upsert
    on and is skipped, matching `_to_raw_item`'s original rule)."""
    items = body.get("items")
    if not isinstance(items, list):
        return []
    posts: list[BlogPost] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        link = entry.get("link")
        if not isinstance(link, str) or not link:
            continue
        published_at = _iso_date(entry.get("postdate"))
        author = entry.get("bloggername")
        posts.append(
            BlogPost(
                post_id=link,
                url=link,
                category=category,
                group_key=group_key,
                query=query,
                title=_plain(entry.get("title")),
                excerpt=_plain(entry.get("description")),
                author=author.strip() if isinstance(author, str) and author.strip() else None,
                published_at=published_at,
                # naver blog dates are always day-precision, and formats.md's own NULL fallback
                # (observed_at = captured_at's date) is day-precision too -- either way this is 'day'.
                # A NULL published_at is told apart by the column itself, not by this field.
                observed_at_resolution="day",
                captured_at=captured_at,
            )
        )
    return posts


def blog_page_is_empty(body: dict[str, Any]) -> bool:
    items = body.get("items")
    return not isinstance(items, list) or not items


def blog_items(body: dict[str, Any]) -> Iterable[dict[str, Any]]:
    items = body.get("items")
    return items if isinstance(items, list) else []


__all__ = ["parse_datalab_response", "parse_blog_response", "blog_page_is_empty", "blog_items"]
