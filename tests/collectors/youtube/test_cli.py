"""Exit codes (contracts/entrypoints.md 종료 코드): 0 ok, 1 partial, 2 blocked -- and that
`cosmai collect youtube` actually reaches this module."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from collectors.youtube import queue
from collectors.youtube.cli import run

pytestmark = pytest.mark.postgres

AT = datetime(2026, 8, 24, 3, tzinfo=UTC)


def test_unknown_dataset_is_blocked(tubedepth_schema: str):
    assert run("bogus", database_url=tubedepth_schema) == 2


def test_watch_with_no_watchlist_file_is_blocked(tubedepth_schema: str, tmp_path: Path):
    assert run("watch", database_url=tubedepth_schema, watchlist_path=tmp_path / "missing.txt") == 2


def test_watch_reports_partial_when_the_queue_is_capped(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(queue, "MAX_QUEUE_DEPTH", 1)
    watchlist = tmp_path / "watch.txt"
    # channel+comments queues 3 listing jobs; the cap (1) is hit on the second.
    watchlist.write_text("channel+comments UUsome_channel_id\n")
    assert run("watch", database_url=tubedepth_schema, watchlist_path=watchlist, captured_at=AT) == 1

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        from collectors.youtube.storage.tables import jobs

        assert conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar_one() == 1
    engine.dispose()


def test_watch_stamps_dataset_on_the_job_it_creates(tubedepth_schema: str, tmp_path: Path):
    """#102: `jobs.kind` (video.metadata 계열) is not the entrypoints.md dataset vocabulary
    (watch|work|flatten|prune) -- collector_health's youtube arm needs a column in that vocabulary."""
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("video dQw4w9WgXcQ\n")
    assert run("watch", database_url=tubedepth_schema, watchlist_path=watchlist, captured_at=AT) == 0

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        from collectors.youtube.storage.tables import jobs

        row = conn.execute(sa.select(jobs.c.dataset)).one()
    engine.dispose()
    assert row.dataset == "watch"


def test_a_follow_up_job_fanned_out_during_work_inherits_watch(tubedepth_schema: str, tmp_path: Path):
    """The follow-up job that `work` fans out from a listing job's `follow_up_kind` was still started
    by `watch` -- only `watch` ever sets `follow_up_kind`, so the fan-out has no other dataset to name."""
    from collectors.youtube.cli import FetchSpec
    from collectors.youtube.storage.tables import jobs

    listing_dump = {
        "id": "UUsome_channel_id",
        "title": "Uploads",
        "entries": [{"id": "dQw4w9WgXcQ", "title": "t", "channel_id": "UUsome_channel_id"}],
    }

    class _ListingFetcher:
        def fetch(self, spec: FetchSpec) -> dict:
            return listing_dump

    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("channel @beauty_channel\n")
    assert run("watch", database_url=tubedepth_schema, watchlist_path=watchlist, captured_at=AT) == 0
    work_exit = run(
        "work",
        database_url=tubedepth_schema,
        fetcher=_ListingFetcher(),
        payload_root=tmp_path / "p",
        captured_at=AT,
    )
    assert work_exit == 0

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        follow_up = conn.execute(
            sa.select(jobs.c.dataset).where(jobs.c.kind == "video.metadata", jobs.c.target == "dQw4w9WgXcQ")
        ).one()
    engine.dispose()
    assert follow_up.dataset == "watch"


def test_cosmai_collect_youtube_reaches_this_module():
    """Not `--help` -- that only proves the parser accepts `youtube`. This proves _run_collect's
    dispatch actually imports collectors.youtube.cli rather than the "not wired yet" refusal."""
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-m", "cosmai.cli", "collect", "youtube", "--dataset", "bogus"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "not wired yet" not in result.stdout
    assert "no dataset named" in result.stdout
