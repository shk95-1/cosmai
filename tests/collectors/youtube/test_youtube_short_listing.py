"""A listing that came back short is a partial run, and every artifact says which route fetched it.

These are the two things #183 adds to `work` that nothing before it could observe.

**The route on the row** (`artifacts.fetch_route`, DDL 005) is the population model of
shk95/cosmai-import-ydc#91, option D: an analysis axis declares which routes it admits, so the row
has to carry which one ran. `video.metadata` may come over either, so it cannot be inferred from the
job kind.

**The shortfall** is the #90 lesson. On #90 the fetcher fake built its answer out of the request,
every request therefore succeeded plausibly, the whole suite was green, and the production run wrote
0 rows. A fetch that returns less than the source holds looks exactly like one that returned
everything -- unless something compares the two numbers and says so. contracts/entrypoints.md
already calls that partial: "1 partial (some failed **or were truncated**)".
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from collectors.youtube.cli import FetchSpec, run
from collectors.youtube.storage.tables import artifacts, listing_entries
from collectors.youtube.transport import Route

pytestmark = pytest.mark.postgres

AT = datetime(2026, 9, 13, 3, tzinfo=UTC)
CHANNEL = "UCaaaaaaaaaaaaaaaaaaaaaa"


class _DumpFetcher:
    def __init__(self, dump: dict[str, Any]) -> None:
        self.dump = dump

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        return self.dump


def _listing(*, returned: int, expected: int | None, truncated: bool, route: str = Route.DATA_API):
    return {
        "id": "UUaaaaaaaaaaaaaaaaaaaaaa",
        "title": "Uploads",
        "entries": [
            {"id": f"vid{index:08d}", "title": f"v{index}", "channel_id": CHANNEL}
            for index in range(returned)
        ],
        "fetch_route": route,
        "expected_count": expected,
        "returned_count": returned,
        "truncated": truncated,
    }


def _watch(url: str, tmp_path: Path) -> None:
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text(f"channel {CHANNEL}\n")
    assert run("watch", read_roster=False, database_url=url, watchlist_path=watchlist, captured_at=AT) == 0


def _work(url: str, tmp_path: Path, dump: dict[str, Any]) -> int:
    return run(
        "work",
        database_url=url,
        fetcher=_DumpFetcher(dump),
        payload_root=tmp_path / "payloads",
        captured_at=AT,
    )


def test_a_walk_that_ended_short_of_what_the_source_reported_is_partial(
    tubedepth_schema: str, tmp_path: Path
):
    _watch(tubedepth_schema, tmp_path)
    assert _work(tubedepth_schema, tmp_path, _listing(returned=3, expected=5, truncated=False)) == 1


def test_the_rows_it_did_collect_are_kept(tubedepth_schema: str, tmp_path: Path):
    """Partial, not failed: three real videos are three real rows, and throwing them away would make
    a short answer worse than no answer."""
    _watch(tubedepth_schema, tmp_path)
    _work(tubedepth_schema, tmp_path, _listing(returned=3, expected=5, truncated=False))
    assert (
        run("flatten", database_url=tubedepth_schema, payload_root=tmp_path / "payloads", captured_at=AT) == 0
    )

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        count = conn.execute(sa.select(sa.func.count()).select_from(listing_entries)).scalar_one()
    engine.dispose()
    assert count == 3


def test_a_walk_our_own_cap_stopped_is_not_counted_against_the_run(tubedepth_schema: str, tmp_path: Path):
    """`truncated` is ours -- MAX_LISTING_ITEMS or the publication window. Reporting our own cap as
    the source losing rows would make every capped pass look like a broken one, and an operator who
    sees partial on every run stops reading it."""
    _watch(tubedepth_schema, tmp_path)
    assert _work(tubedepth_schema, tmp_path, _listing(returned=2, expected=5, truncated=True)) == 0


def test_a_complete_walk_is_a_clean_run(tubedepth_schema: str, tmp_path: Path):
    _watch(tubedepth_schema, tmp_path)
    assert _work(tubedepth_schema, tmp_path, _listing(returned=5, expected=5, truncated=False)) == 0


def test_a_dump_that_reports_no_counts_at_all_is_a_clean_run(tubedepth_schema: str, tmp_path: Path):
    """Every fixture-backed fake in this package predates #183 and reports nothing. Absent counters
    mean "not measured", which must not be read as "short"."""
    _watch(tubedepth_schema, tmp_path)
    assert _work(tubedepth_schema, tmp_path, _listing(returned=2, expected=None, truncated=False)) == 0


def test_the_artifact_records_which_route_fetched_it(tubedepth_schema: str, tmp_path: Path):
    _watch(tubedepth_schema, tmp_path)
    _work(tubedepth_schema, tmp_path, _listing(returned=2, expected=2, truncated=False))

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        routes = set(conn.execute(sa.select(artifacts.c.fetch_route)).scalars())
    engine.dispose()
    assert routes == {Route.DATA_API}


def test_video_metadata_records_the_route_it_actually_took_rather_than_one_read_off_the_kind(
    tubedepth_schema: str, tmp_path: Path
):
    """The reason this is a column and not a lookup table: `video.metadata` is the one kind both
    routes serve, so the kind cannot say which one ran."""
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("video dQw4w9WgXcQ\n")
    assert (
        run(
            "watch",
            read_roster=False,
            database_url=tubedepth_schema,
            watchlist_path=watchlist,
            captured_at=AT,
        )
        == 0
    )
    dump = {"id": "dQw4w9WgXcQ", "title": "t", "fetch_route": Route.YTDLP}
    assert _work(tubedepth_schema, tmp_path, dump) == 0

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        route = conn.execute(sa.select(artifacts.c.fetch_route)).scalar_one()
    engine.dispose()
    assert route == Route.YTDLP
