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
    LAUNCH_ONSET = "launch_onset"


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


@dataclass(frozen=True, slots=True)
class LaunchSeriesPoint:
    """One (product, api, series, month) cell of a launch-onset series --
    `needs.naver_launch_series` (011). Unlike `DatalabPoint` the subject is a canonical
    `product_ref` rather than a lexicon category, and the ratio carries no anchor beside it: the
    request holds one keyword group on purpose, so the series is relative to its own peak, which is
    what the onset rule asks of it."""

    product_ref: str
    api: str  # search_trend | shopping_insight
    series_key: str  # '' for search_trend's single group, the one term for a shopping series
    month: str  # 'YYYY-MM'
    ratio: float | None
    terms: tuple[str, ...]
    request_key: str
    captured_at: datetime

    def natural_key(self) -> tuple[str, str, str, str]:
        return (self.product_ref, self.api, self.series_key, self.month)


@dataclass(frozen=True, slots=True)
class LaunchClaim:
    """One row of `needs.product_launch_evidence` (010) as this collector writes it. The shape is
    `analysis.types.LaunchClaimRow`, restated here rather than imported so the collector does not
    take a dependency on the analysis package it only shares a table with."""

    product_ref: str
    axis: str
    direction: str  # not_before | not_after | at
    claimed_on: date
    claimed_precision: str  # day | month
    source_ref: str
    match_strength: str  # exact | partial
    axis_version: str
    observed_at: datetime
    note: str | None = None


__all__ = ["Dataset", "DatalabPoint", "BlogPost", "LaunchSeriesPoint", "LaunchClaim"]
