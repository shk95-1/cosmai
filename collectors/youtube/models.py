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
]
