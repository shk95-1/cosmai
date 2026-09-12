"""The vocabulary shared across this package: what `--dataset` names, and what a job's `state` holds.

origin: service/yt-scrapper/src/tubedepth/models.py's JobState -- ported for #8. `Dataset` is new here,
matching collectors/commerce/models.py's Dataset: contracts/entrypoints.md names the four
`cosmai collect youtube --dataset` values, tubedepth itself never had a single enum for them because
`watch`/`work`/`flatten`/`prune` were four separate CLI commands, not one entrypoint's argument.
"""

from __future__ import annotations

import json
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

SCOPE_PATH = Path(__file__).resolve().parent / "scope.json"


def _scope() -> dict:
    return json.loads(SCOPE_PATH.read_text(encoding="utf-8"))


_SCOPE = _scope()
# Read from scope.json rather than declared here so the constant and its recorded rationale can never
# drift apart -- a test asserts these equal the file (test_scope_matches_constants.py's commerce
# pattern), but the file is the one a reader actually checks the number and the "why" together in.
MAX_FOLLOWUPS_PER_VIDEO: int = _SCOPE["MAX_FOLLOWUPS_PER_VIDEO"]
MAX_QUEUE_DEPTH: int = _SCOPE["MAX_QUEUE_DEPTH"]
# How long a stored artifact answers a re-observation of the same target without a re-fetch -- #8
# fix round 2: without this, a directive naming N follow-up kinds re-walks the same listing N times
# in one watch pass (measured: 3 fetches, 6 listing_entries rows for one channel+comments line where
# 1 fetch/2 rows is correct), tripling real request volume the moment a live fetcher is plugged in
# (#10) -- directly against the fan-out cap this issue exists to add. A kind with no entry here never
# caches (timedelta(0)): always refetch is the safe default for an unrecognised kind.
FRESHNESS: dict[str, timedelta] = {k: timedelta(seconds=v) for k, v in _SCOPE["FRESHNESS_SECONDS"].items()}

# #183, the live transport's constants -- same rule as the caps above: the number is read from
# scope.json so it and its recorded reason cannot drift apart.
#
# How far back a listed video may have been published and still have its `video.comments` follow-up
# fanned out a second time. Applied where the fan-out is made, which is where the 224k-duplicate
# incident happened -- not in `work`'s collection of a job that already reached the queue, and not in
# the queue's dedupe, where the row exists by then and MAX_QUEUE_DEPTH has already seen it.
COMMENT_REFETCH_WINDOW_DAYS: int = _SCOPE["COMMENT_REFETCH_WINDOW_DAYS"]
#: The oldest publication date one listing walk goes back to (fork shk95/cosmai-import-ydc#38).
LISTING_WINDOW_START: str = _SCOPE["LISTING_WINDOW_START"]
#: The hard stop on one listing walk, whatever LISTING_WINDOW_START would allow.
MAX_LISTING_ITEMS: int = _SCOPE["MAX_LISTING_ITEMS"]
#: How many top-level comment **threads** one video's harvest asks for -- the archive's own
#: ceiling, and a one-way door: the live lineage is collected incrementally, so a later
#: increase leaves early quarters shallow and cannot be backfilled. At exactly this many the
#: harvest is marked truncated.
MAX_COMMENTS_PER_VIDEO: int = _SCOPE["MAX_COMMENTS_PER_VIDEO"]
#: Whether a thread's replies are collected at all. The archive has none, so the default is
#: False and a reply that arrives anyway is dropped rather than stored (transport.py).
COMMENT_INCLUDE_REPLIES: bool = _SCOPE["COMMENT_INCLUDE_REPLIES"]
#: Which route `video.metadata` is fetched over; both are admissible and the row records which ran.
VIDEO_METADATA_ROUTE: str = _SCOPE["VIDEO_METADATA_ROUTE"]
#: The `part` string `videos.list` is asked for -- one quota unit however wide it is.
VIDEO_PARTS: tuple[str, ...] = tuple(_SCOPE["VIDEO_PARTS"])
#: Per-source request budget, pause and timeout, keyed by route name (transport.Route).
ROUTES: dict[str, dict[str, float]] = _SCOPE["ROUTES"]


class Dataset(StrEnum):
    WATCH = "watch"
    WORK = "work"
    FLATTEN = "flatten"
    PRUNE = "prune"


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


# A job is still spending a queue slot in these states -- a natural-key duplicate check and the queue
# depth cap both mean "any row not yet in one of the other three".
ACTIVE_STATES = (JobState.QUEUED, JobState.RUNNING)

__all__ = [
    "Dataset",
    "JobState",
    "ACTIVE_STATES",
    "MAX_FOLLOWUPS_PER_VIDEO",
    "MAX_QUEUE_DEPTH",
    "FRESHNESS",
    "COMMENT_REFETCH_WINDOW_DAYS",
    "LISTING_WINDOW_START",
    "MAX_LISTING_ITEMS",
    "MAX_COMMENTS_PER_VIDEO",
    "COMMENT_INCLUDE_REPLIES",
    "VIDEO_METADATA_ROUTE",
    "VIDEO_PARTS",
    "ROUTES",
]
