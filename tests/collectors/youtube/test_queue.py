"""Queue-depth and per-video fan-out caps at their exact boundaries (issue #8 review grade B: "리뷰어가
경계값(상한, 상한+1, 중복) 확인"), plus idempotent enqueue -- against a real Postgres schema."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from collectors.youtube import queue
from collectors.youtube.storage.tables import jobs

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 8, 24, 3, tzinfo=UTC)


def _count(conn) -> int:
    return conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar_one()


def test_enqueue_below_the_cap_succeeds(tubedepth_schema: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(queue, "MAX_QUEUE_DEPTH", 2)
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        outcome = queue.enqueue(conn, kind="video.metadata", target="a", now=NOW)
    assert outcome is queue.EnqueueOutcome.ENQUEUED
    engine.dispose()


def test_enqueue_exactly_at_the_cap_still_succeeds(tubedepth_schema: str, monkeypatch: pytest.MonkeyPatch):
    """MAX_QUEUE_DEPTH=2: the second job still fits -- the boundary itself is not yet a refusal."""
    monkeypatch.setattr(queue, "MAX_QUEUE_DEPTH", 2)
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        a = queue.enqueue(conn, kind="video.metadata", target="a", now=NOW)
        b = queue.enqueue(conn, kind="video.metadata", target="b", now=NOW)
        assert a is queue.EnqueueOutcome.ENQUEUED
        assert b is queue.EnqueueOutcome.ENQUEUED
        assert _count(conn) == 2
    engine.dispose()


def test_enqueue_one_past_the_cap_is_refused(tubedepth_schema: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(queue, "MAX_QUEUE_DEPTH", 2)
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        queue.enqueue(conn, kind="video.metadata", target="a", now=NOW)
        queue.enqueue(conn, kind="video.metadata", target="b", now=NOW)
        outcome = queue.enqueue(conn, kind="video.metadata", target="c", now=NOW)
        assert outcome is queue.EnqueueOutcome.CAPPED
        assert _count(conn) == 2  # the refused job never became a row
    engine.dispose()


def test_enqueue_is_idempotent_on_the_natural_key(tubedepth_schema: str):
    """The exact bug this fixes: watch re-observing the same target must not grow the queue."""
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        target, follow_up = "UC1", "video.metadata"
        first = queue.enqueue(conn, kind="channel.videos", target=target, follow_up_kind=follow_up, now=NOW)
        second = queue.enqueue(conn, kind="channel.videos", target=target, follow_up_kind=follow_up, now=NOW)
        assert first is queue.EnqueueOutcome.ENQUEUED
        assert second is queue.EnqueueOutcome.DUPLICATE
        assert _count(conn) == 1
    engine.dispose()


def test_duplicate_check_is_scoped_by_follow_up_kind(tubedepth_schema: str):
    """Two listing jobs for the same target with different follow_up_kind are legitimately two rows --
    the natural key is (kind, target, follow_up_kind), not (kind, target)."""
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        a = queue.enqueue(conn, kind="channel.videos", target="UC1", follow_up_kind="video.metadata", now=NOW)
        b = queue.enqueue(conn, kind="channel.videos", target="UC1", follow_up_kind="video.comments", now=NOW)
        assert a is queue.EnqueueOutcome.ENQUEUED
        assert b is queue.EnqueueOutcome.ENQUEUED
        assert _count(conn) == 2
    engine.dispose()


def test_fan_out_respects_the_per_video_cap(tubedepth_schema: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(queue, "MAX_FOLLOWUPS_PER_VIDEO", 1)
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        first = queue.fan_out_follow_up(conn, video_ids=["v1"], follow_up_kind="video.metadata", now=NOW)
        second = queue.fan_out_follow_up(conn, video_ids=["v1"], follow_up_kind="video.comments", now=NOW)
        assert first.enqueued == 1
        assert second.enqueued == 0
        assert second.per_video_capped == 1
        assert _count(conn) == 1
    engine.dispose()


def test_fan_out_dedupes_across_repeated_sweeps(tubedepth_schema: str):
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        video_ids = ["v1", "v2"]
        first = queue.fan_out_follow_up(conn, video_ids=video_ids, follow_up_kind="video.metadata", now=NOW)
        second = queue.fan_out_follow_up(conn, video_ids=video_ids, follow_up_kind="video.metadata", now=NOW)
        assert first.enqueued == 2
        assert second.enqueued == 0
        assert second.duplicate == 2
        assert _count(conn) == 2
    engine.dispose()
