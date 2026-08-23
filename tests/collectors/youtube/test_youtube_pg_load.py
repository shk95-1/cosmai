"""End to end against a real Postgres schema built from the DDL (tubedepth_schema): watch enqueues a
listing job with a transcript follow-up, work collects it and fans out per-video follow-ups (capped,
deduped -- queue.py), flatten turns the resulting artifacts into video_snapshots/listing_entries/
comments/transcripts. Proves issue #8's whole path, offline (fixture-backed fetcher, no yt-dlp)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from collectors.youtube.cli import FetchSpec, run
from collectors.youtube.storage.tables import comments, jobs, listing_entries, transcripts, video_snapshots

pytestmark = pytest.mark.postgres

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AT = datetime(2026, 8, 24, 3, tzinfo=UTC)


def _load(*parts: str) -> dict:
    return json.loads((FIXTURES / Path(*parts)).read_text(encoding="utf-8"))


class _FixtureFetcher:
    """No network: hands back a saved dump per job kind, keyed the way the real sources would ask.
    Counts calls per kind -- #8 수정 라운드 2's freshness cache is only provable by counting fetches,
    not just reading rows back."""

    def __init__(self) -> None:
        self._listing = _load("listing", "channel-videos.json")
        self._metadata = _load("video_metadata", "dQw4w9WgXcQ.json")
        self._comments = _load("comments", "dQw4w9WgXcQ-top40.json")
        self._transcript = {
            **_load("transcript", "dQw4w9WgXcQ-en-json3.json"),
            "language": "en",
            "is_automatic": True,
        }
        self.calls: dict[str, int] = {}

    def fetch(self, spec: FetchSpec) -> dict:
        self.calls[spec.kind] = self.calls.get(spec.kind, 0) + 1
        if spec.kind == "channel.videos":
            return self._listing
        if spec.kind == "video.metadata":
            return self._metadata
        if spec.kind == "video.comments":
            return self._comments
        if spec.kind == "video.transcript":
            return self._transcript
        raise AssertionError(f"unexpected fetch: {spec}")


def _write_watchlist(tmp_path: Path) -> Path:
    path = tmp_path / "watch.txt"
    path.write_text("channel+comments UUsome_channel_id\n")
    return path


def test_watch_work_flatten_lands_every_table(tubedepth_schema: str, tmp_path: Path):
    watchlist = _write_watchlist(tmp_path)
    payload_root = tmp_path / "payloads"
    fetcher = _FixtureFetcher()

    # watch: one directive with 3 follow-ups -> 3 listing jobs (one per follow-up kind).
    exit_watch = run("watch", database_url=tubedepth_schema, watchlist_path=watchlist, captured_at=AT)
    assert exit_watch == 0

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar_one() == 3
    engine.dispose()

    # work drains the queue: 3 listing jobs, each fanning out to 2 videos (skip the unavailable entry).
    exit_work = 0
    for _ in range(4):  # 3 listing jobs + follow-ups fanned out from them
        exit_work = run(
            "work", database_url=tubedepth_schema, fetcher=fetcher, payload_root=payload_root, captured_at=AT
        )
        if exit_work != 0:
            break
    assert exit_work == 0

    exit_flatten = run("flatten", database_url=tubedepth_schema, payload_root=payload_root, captured_at=AT)
    assert exit_flatten == 0

    # #8 수정 라운드 2: 3 listing jobs (one per follow-up kind on the one channel+comments line) share
    # one channel -- the freshness cache (models.FRESHNESS) means only the first actually fetches;
    # jobs 2 and 3 reuse that artifact. Before the cache: 3 fetches, 6 listing_entries rows.
    assert fetcher.calls["channel.videos"] == 1
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(listing_entries)).scalar_one() == 2
        assert conn.execute(sa.select(sa.func.count()).select_from(video_snapshots)).scalar_one() == 2
        assert conn.execute(sa.select(sa.func.count()).select_from(comments)).scalar_one() == 4
        assert conn.execute(sa.select(sa.func.count()).select_from(transcripts)).scalar_one() == 2
        new_cols = sa.select(comments.c.published_at_resolution, comments.c.channel_is_brand_owner)
        assert conn.execute(new_cols).first() == (None, None)  # #8: no writer for either yet -- see report
    engine.dispose()


def test_watch_is_a_no_op_on_a_repeated_pass(tubedepth_schema: str, tmp_path: Path):
    watchlist = _write_watchlist(tmp_path)
    run("watch", database_url=tubedepth_schema, watchlist_path=watchlist, captured_at=AT)
    run("watch", database_url=tubedepth_schema, watchlist_path=watchlist, captured_at=AT)

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar_one() == 3
    engine.dispose()
