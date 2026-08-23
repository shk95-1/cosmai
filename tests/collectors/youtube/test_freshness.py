"""#8 수정 라운드 2: `_fresh_artifact` is a real cache, not a permanent one -- an artifact past its
`models.FRESHNESS` window must be refetched, not served forever."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from collectors.youtube.cli import FetchSpec, run
from collectors.youtube.models import FRESHNESS
from collectors.youtube.storage.tables import artifacts

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 8, 24, 3, tzinfo=UTC)


class _CountingFetcher:
    def __init__(self, dump: dict) -> None:
        self._dump = dump
        self.calls = 0

    def fetch(self, spec: FetchSpec) -> dict:
        self.calls += 1
        return self._dump


def _run_watch_then_work(tubedepth_schema: str, tmp_path, fetcher, *, now: datetime) -> None:
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("video dQw4w9WgXcQ\n")
    assert run("watch", database_url=tubedepth_schema, watchlist_path=watchlist, captured_at=now) == 0
    work_exit = run(
        "work", database_url=tubedepth_schema, fetcher=fetcher, payload_root=tmp_path / "p", captured_at=now
    )
    assert work_exit == 0


def test_a_second_observation_inside_the_freshness_window_does_not_refetch(tubedepth_schema: str, tmp_path):
    fetcher = _CountingFetcher({"id": "dQw4w9WgXcQ", "title": "t"})
    _run_watch_then_work(tubedepth_schema, tmp_path, fetcher, now=T0)
    assert fetcher.calls == 1

    just_inside = T0 + FRESHNESS["video.metadata"] - timedelta(seconds=1)
    _run_watch_then_work(tubedepth_schema, tmp_path, fetcher, now=just_inside)
    assert fetcher.calls == 1  # still cached -- no new artifact, no new fetch


def test_an_observation_past_the_freshness_window_refetches(tubedepth_schema: str, tmp_path):
    fetcher = _CountingFetcher({"id": "dQw4w9WgXcQ", "title": "t"})
    _run_watch_then_work(tubedepth_schema, tmp_path, fetcher, now=T0)
    assert fetcher.calls == 1

    past_window = T0 + FRESHNESS["video.metadata"] + timedelta(seconds=1)
    _run_watch_then_work(tubedepth_schema, tmp_path, fetcher, now=past_window)
    assert fetcher.calls == 2  # window elapsed -- the cache is not permanent

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        count = conn.execute(sa.select(sa.func.count()).select_from(artifacts)).scalar_one()
    engine.dispose()
    assert count == 2  # one artifact row per real fetch, not per job
