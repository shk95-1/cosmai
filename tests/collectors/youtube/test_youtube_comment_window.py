"""The comment re-fetch window (#183): which listed videos get a `video.comments` follow-up.

Without it a daily cadence re-fetches every panel video's comments every day -- against 13,979
videos that is the quota shape the archive measured as 5,098 of 5,679 units, every day, forever.
With it, a video published inside `COMMENT_REFETCH_WINDOW_DAYS` is fanned out every pass (and
`FRESHNESS_SECONDS` then decides whether the job spends a fetch) while an older one is fanned out
once, at first observation, and never again.

The value 30 is provisional and is not what these tests assert: they assert the *shape* against
whatever `scope.json` holds, so the day fork shk95/cosmai-import-ydc#38's timed sample sets the real
number, changing the constant changes no test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from collectors.youtube.cli import FetchSpec, run
from collectors.youtube.models import COMMENT_REFETCH_WINDOW_DAYS
from collectors.youtube.storage.tables import artifacts, jobs

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 9, 13, 3, tzinfo=UTC)
CHANNEL = "UCaaaaaaaaaaaaaaaaaaaaaa"
RECENT = "dQw4w9WgXcQ"
OLD = "9bZkp7q19f0"
UNDATED = "aB3dEf7GhJk"

INSIDE = NOW - timedelta(days=COMMENT_REFETCH_WINDOW_DAYS - 1)
OUTSIDE = NOW - timedelta(days=COMMENT_REFETCH_WINDOW_DAYS + 1)


def _listing_dump() -> dict[str, Any]:
    """A saved listing shape with one video on each side of the window and one with no publication
    instant at all -- which is a real state (an old upload yt-dlp reports nothing for), not a hole
    in the fixture."""
    return {
        "id": "UUaaaaaaaaaaaaaaaaaaaaaa",
        "title": "Uploads",
        "entries": [
            {"id": RECENT, "title": "recent", "channel_id": CHANNEL, "timestamp": int(INSIDE.timestamp())},
            {"id": OLD, "title": "old", "channel_id": CHANNEL, "timestamp": int(OUTSIDE.timestamp())},
            {"id": UNDATED, "title": "undated", "channel_id": CHANNEL},
        ],
        "expected_count": 3,
        "returned_count": 3,
        "truncated": False,
    }


class _ListingFetcher:
    def __init__(self, dump: dict[str, Any]) -> None:
        self._dump = dump

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        return self._dump


def _watch_and_work(url: str, tmp_path: Path, *, now: datetime = NOW, suffix: str = "") -> None:
    watchlist = tmp_path / f"watch{suffix}.txt"
    watchlist.write_text(f"channel+comments {CHANNEL}\n")
    assert (
        run(
            "watch",
            read_roster=False,
            database_url=url,
            watchlist_path=watchlist,
            captured_at=now,
        )
        == 0
    )
    run(
        "work",
        read_roster=False,
        database_url=url,
        fetcher=_ListingFetcher(_listing_dump()),
        payload_root=tmp_path / f"payloads{suffix}",
        captured_at=now,
    )


def _comment_targets(url: str) -> set[str]:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        rows = conn.execute(sa.select(jobs.c.target).where(jobs.c.kind == "video.comments")).scalars()
        out = set(rows)
    engine.dispose()
    return out


def test_a_video_inside_the_window_gets_a_comments_job(tubedepth_schema: str, tmp_path: Path):
    _watch_and_work(tubedepth_schema, tmp_path)
    assert RECENT in _comment_targets(tubedepth_schema)


def test_a_video_older_than_the_window_still_gets_one_at_first_observation(
    tubedepth_schema: str, tmp_path: Path
):
    """The window narrows re-fetching, not collection: a video first seen today is collected today
    whatever its publication date, or a backfill would return nothing."""
    _watch_and_work(tubedepth_schema, tmp_path)
    assert OLD in _comment_targets(tubedepth_schema)
    assert UNDATED in _comment_targets(tubedepth_schema)


def test_an_old_video_already_harvested_is_never_fanned_out_again(tubedepth_schema: str, tmp_path: Path):
    """The second pass, a day later, with the comments artifact of the first already on the row. The
    recent video comes back; the old one does not, and that is the whole quota argument."""
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        for target in (OLD, UNDATED):
            conn.execute(
                sa.insert(artifacts).values(
                    identifier=f"artifact-{target}"[:32],
                    kind="video.comments",
                    target=target,
                    fingerprint=f"video.comments:{target}",
                    digest="d" * 64,
                    byte_count=1,
                    fetched_at=NOW - timedelta(days=1),
                    fresh_until=NOW - timedelta(hours=1),  # stale, so freshness is not what stops it
                    schema_version="1",
                )
            )
    engine.dispose()

    _watch_and_work(tubedepth_schema, tmp_path)
    targets = _comment_targets(tubedepth_schema)
    assert RECENT in targets
    assert OLD not in targets
    assert UNDATED not in targets


def test_an_old_video_whose_artifact_was_pruned_is_still_not_fanned_out_again(
    tubedepth_schema: str, tmp_path: Path
):
    """`artifacts` is pruned at PRUNE_MAX_AGE_DAYS, so an artifact row stops being evidence after a
    month. If "ever" were asked of that table alone, the whole panel would silently re-harvest every
    month -- which is the cost this window exists to avoid. `comments` is the table that survives."""
    from collectors.youtube.storage.tables import comments

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(comments).values(
                video_id=OLD,
                comment_id="UgzOld",
                text="harvested long ago",
                is_hearted_by_uploader=False,
                is_pinned=False,
                first_seen_at=NOW - timedelta(days=200),
                last_seen_at=NOW - timedelta(days=200),
            )
        )
    engine.dispose()

    _watch_and_work(tubedepth_schema, tmp_path)
    assert OLD not in _comment_targets(tubedepth_schema)


def test_the_other_follow_up_kinds_are_not_narrowed_by_the_window(tubedepth_schema: str, tmp_path: Path):
    """The window is about the expensive kind. `video.metadata` is one cheap request and its whole
    point is a new snapshot per pass, so narrowing it would turn the series into a gap."""
    _watch_and_work(tubedepth_schema, tmp_path)
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        metadata_targets = set(
            conn.execute(sa.select(jobs.c.target).where(jobs.c.kind == "video.metadata")).scalars()
        )
    engine.dispose()
    assert metadata_targets == {RECENT, OLD, UNDATED}
