"""Canonical records `collectors/naver` writes. Origin: apps/addons/collector.naver.{datalab,blog}'s
Raw item shapes (service/cosmai), flattened into the two tables 004_naver.sql declares -- this
collector has no job queue or Raw/Normalized split to preserve (issue #9's judgment (a): that
platform is not carried forward, see contracts/ddl/needs/004_naver.sql's own header)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class Dataset(StrEnum):
    DATALAB = "datalab"
    BLOG = "blog"


@dataclass(frozen=True, slots=True)
class DatalabPoint:
    """One (group, month) cell of a Search Trend series. `ratio` is relative *within the request
    that produced it* (vendor docs: max 100 in the window) -- `terms` travels with every point so a
    later reader can see what request produced the number, not just trust its scale. `request_key`
    is the row-level answer to "which request" (contracts/formats.md §NAVER DataLab), computed by
    `parsing.datalab_request_key` and shared by every point one HTTP call produced."""

    category: str
    group_key: str
    month: str  # 'YYYY-MM'
    ratio: float | None
    terms: tuple[str, ...]
    request_key: str  # contracts/ddl/needs/006_naver_request.sql
    captured_at: datetime

    def natural_key(self) -> tuple[str, str, str]:
        return (self.category, self.group_key, self.month)


@dataclass(frozen=True, slots=True)
class BlogPost:
    """One blog search hit, matching `needs.naver_blog_post` (ref = `post_id`, formats.md)."""

    post_id: str  # the result's `link` -- the API assigns no other id (see original handler.py)
    url: str
    category: str | None
    group_key: str | None
    query: str | None
    title: str
    excerpt: str
    author: str | None
    published_at: date | None
    observed_at_resolution: str  # day | month | year
    captured_at: datetime

    def natural_key(self) -> tuple[str]:
        return (self.post_id,)


__all__ = ["Dataset", "DatalabPoint", "BlogPost"]
