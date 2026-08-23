"""Enqueueing, and not enqueueing twice -- the fix for issue #8's fan-out bug.

The archived worker (`service/yt-scrapper/src/tubedepth/worker.py::_queue_follow_up`) inserted one Job
per video found in a listing with no check at all for whether that video already had one queued or
running, and `watch` re-ran every listing forced (`refresh=True`) on every hourly pass -- with nothing
draining the queue as fast as it grew, that reached 224,036 duplicate `video.*` jobs in production
(`error_code='cancelled_duplicate'`; see `scope.json`). Every insert in this module goes through
`enqueue`, and `enqueue` is the one place that natural-key dedupe and the queue-depth cap live, so no
caller can route around either.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy import Connection

from collectors.youtube.models import ACTIVE_STATES, MAX_FOLLOWUPS_PER_VIDEO, MAX_QUEUE_DEPTH, JobState
from collectors.youtube.storage.tables import jobs

_ACTIVE = tuple(s.value for s in ACTIVE_STATES)


class EnqueueOutcome(StrEnum):
    ENQUEUED = "enqueued"
    # A queued or running job already names this exact (kind, target, follow_up_kind) -- the
    # idempotent case: re-observing the same video does not cost a second row.
    DUPLICATE = "duplicate"
    # The queue is at MAX_QUEUE_DEPTH; nothing was inserted.
    CAPPED = "capped"


def queue_depth(conn: Connection) -> int:
    """Rows currently holding a queue slot -- queued or running, the two `ACTIVE_STATES`."""
    return conn.execute(
        sa.select(sa.func.count()).select_from(jobs).where(jobs.c.state.in_(_ACTIVE))
    ).scalar_one()


def enqueue(
    conn: Connection,
    *,
    kind: str,
    target: str,
    follow_up_kind: str | None = None,
    refresh: bool = False,
    max_attempts: int = 3,
    now: datetime,
) -> EnqueueOutcome:
    """One job, or a reason none was added. Every insert into `jobs` goes through here."""
    dup_where = (
        jobs.c.follow_up_kind.is_(None) if follow_up_kind is None else jobs.c.follow_up_kind == follow_up_kind
    )
    duplicate = conn.execute(
        sa.select(jobs.c.identifier)
        .where(jobs.c.kind == kind, jobs.c.target == target, dup_where, jobs.c.state.in_(_ACTIVE))
        .limit(1)
    ).first()
    if duplicate is not None:
        return EnqueueOutcome.DUPLICATE
    if queue_depth(conn) >= MAX_QUEUE_DEPTH:
        return EnqueueOutcome.CAPPED
    conn.execute(
        sa.insert(jobs).values(
            identifier=uuid.uuid4().hex,
            kind=kind,
            target=target,
            follow_up_kind=follow_up_kind,
            refresh=refresh,
            state=JobState.QUEUED.value,
            attempt_count=0,
            max_attempts=max_attempts,
            scheduled_at=now,
            created_at=now,
            webhook_attempts=0,
        )
    )
    return EnqueueOutcome.ENQUEUED


@dataclass
class FanOutReport:
    enqueued: int = 0
    duplicate: int = 0
    capped: int = 0
    per_video_capped: int = 0

    @property
    def ok(self) -> bool:
        return self.capped == 0 and self.per_video_capped == 0


def fan_out_follow_up(
    conn: Connection,
    *,
    video_ids: Sequence[str],
    follow_up_kind: str,
    max_attempts: int = 3,
    now: datetime,
) -> FanOutReport:
    """Queue one `follow_up_kind` job per video, bounded by `MAX_FOLLOWUPS_PER_VIDEO` per video and
    `MAX_QUEUE_DEPTH` overall (via `enqueue`). The per-video count is every `video.*` kind currently
    active for that video, not just this one -- a video reachable from two different listings (a
    channel and a search result, say) must not accumulate more active follow-ups than one video ever
    should, regardless of which listings named it.
    """
    report = FanOutReport()
    for video_id in video_ids:
        active_for_video = conn.execute(
            sa.select(sa.func.count())
            .select_from(jobs)
            .where(jobs.c.target == video_id, jobs.c.kind.like("video.%"), jobs.c.state.in_(_ACTIVE))
        ).scalar_one()
        if active_for_video >= MAX_FOLLOWUPS_PER_VIDEO:
            report.per_video_capped += 1
            continue
        outcome = enqueue(conn, kind=follow_up_kind, target=video_id, max_attempts=max_attempts, now=now)
        if outcome is EnqueueOutcome.ENQUEUED:
            report.enqueued += 1
        elif outcome is EnqueueOutcome.DUPLICATE:
            report.duplicate += 1
        else:
            # The queue-wide cap tripped: further videos would only trip it again.
            report.capped += len(video_ids) - (report.enqueued + report.duplicate + report.per_video_capped)
            break
    return report


__all__ = ["EnqueueOutcome", "FanOutReport", "enqueue", "fan_out_follow_up", "queue_depth"]
