"""A listing walk is charged what it spent, not a flat estimate (#272).

#259 read the day's Data API spend back out of `tubedepth.artifacts` and could add no column, so it
reconstructed each fetch's cost from the row's `kind`: exact for `video.metadata`, where a run's
rows divide by the 50 ids a `videos.list` call carries, and a flat `LISTING_REQUESTS_PER_WALK = 9`
for a listing walk, whose real cost is anywhere between 1 and 41 requests. DDL `tubedepth/007` gives
the row a `request_count`, and these tests are the three halves of that:

* the walk counts its own requests -- the `channels.list` that resolves the uploads playlist plus
  every page -- and says so on its dump, so a walk longer than the estimate is not cheap by fiat;
* `work` writes that number to the artifact row;
* `quota.py` sums it where it is there and falls back to the estimate where it is not, which is what
  a row written before this migration is, and what the day's bound then trips on.

The pages here are **saved bodies keyed by page token**, the discipline
`tests/collectors/youtube/test_youtube_transport.py` states: a handler that built its answer out of
the request could return a page for every token forever, and "the walk paged ten times" is exactly
the fact this file must not be able to invent.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from collectors.youtube import quota, transport
from collectors.youtube.cli import FetchSpec, run
from collectors.youtube.storage.tables import artifacts
from collectors.youtube.transport import DataApiClient, Route

API_KEY = "dummy-data-api-key"
CHANNEL = "UC5oM4Ai05dQqiVL6rypAo_A"
UPLOADS_PLAYLIST = "UU5oM4Ai05dQqiVL6rypAo_A"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "data_api"

#: More pages than the flat estimate covers, which is the only length at which the two readings of a
#: walk differ. 10 pages + the channels.list that found the playlist = 11 requests against 9.
LONG_WALK_PAGES = 10

AT = datetime(2026, 9, 13, 3, tzinfo=UTC)
T0 = datetime(2026, 9, 19, 20, tzinfo=UTC)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


CHANNELS = _fixture("channels-contentdetails.json")
PAGE = _fixture("uploads-page-1.json")


def _page(index: int, *, last: bool) -> dict[str, Any]:
    """One saved page: the stored body with its own ids and its own forward token.

    Built once, up front, and put in the handler's table -- never derived from the request that
    asks for it. A page carries two items whatever `maxResults` says, so a walk that asked for 50
    and got 2 is a short page here rather than a page shaped to the question (#90)."""
    body = deepcopy(PAGE)
    body["items"] = [deepcopy(body["items"][0]) for _ in range(2)]
    for position, item in enumerate(body["items"]):
        item["snippet"]["resourceId"]["videoId"] = f"walk{index:02d}pg{position}"
        item["snippet"]["position"] = position
    if last:
        body.pop("nextPageToken", None)
    else:
        body["nextPageToken"] = f"token-page-{index + 1}"
    return body


class _SavedPages:
    """`tests/collectors/youtube/test_youtube_transport.py`'s `_SavedBodies`, kept to this file's
    two paths: a body per (path, pageToken) and a 404 for anything the table does not hold."""

    def __init__(self, bodies: dict[tuple[str, str | None], dict[str, Any]]) -> None:
        self._bodies = bodies
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        key = (request.url.path, request.url.params.get("pageToken"))
        if key not in self._bodies:
            return httpx.Response(404, json={"error": {"code": 404, "errors": []}})
        return httpx.Response(200, json=self._bodies[key])


def _long_walk() -> _SavedPages:
    bodies: dict[tuple[str, str | None], dict[str, Any]] = {
        (transport.CHANNELS_PATH, None): CHANNELS,
    }
    for index in range(LONG_WALK_PAGES):
        token = None if index == 0 else f"token-page-{index}"
        bodies[(transport.PLAYLIST_ITEMS_PATH, token)] = _page(index, last=index == LONG_WALK_PAGES - 1)
    return _SavedPages(bodies)


def _client(handler: _SavedPages) -> DataApiClient:
    return DataApiClient(
        API_KEY,
        transport=httpx.MockTransport(handler),
        budget=transport.RequestBudget({Route.DATA_API: 50}, {Route.DATA_API: 0.0}, sleep=lambda _s: None),
    )


# --- the walk counts itself ------------------------------------------------------------------


def test_a_long_walk_reports_every_request_it_made():
    """One `channels.list` plus one per page. The number is read off the requests that went out --
    the handler saw them -- rather than off the entries that came back, because a page that
    returned nothing still cost a unit."""
    handler = _long_walk()
    dump = _client(handler).listing("channel.videos", CHANNEL)

    assert len(handler.seen) == LONG_WALK_PAGES + 1
    assert dump.get("request_count") == LONG_WALK_PAGES + 1
    assert dump["id"] == UPLOADS_PLAYLIST


def test_a_walk_longer_than_the_estimate_is_not_charged_the_estimate():
    """The undercharge #272 exists to end, stated as the comparison it is: this walk costs 11 and
    the flat charge would have called it 9."""
    dump = _client(_long_walk()).listing("channel.videos", CHANNEL)
    assert dump.get("request_count", 0) > quota.LISTING_REQUESTS_PER_WALK


def test_a_playlist_walk_is_not_charged_a_channels_list_it_never_made():
    """`playlist.items` takes the playlist id as its target, so no `channels.list` resolves it -- a
    count derived from the kind would charge it one request it never sent."""
    handler = _SavedPages({(transport.PLAYLIST_ITEMS_PATH, None): _page(LONG_WALK_PAGES - 1, last=True)})
    dump = _client(handler).listing("playlist.items", UPLOADS_PLAYLIST)
    assert dump.get("request_count") == 1


# --- the row keeps it ------------------------------------------------------------------------


class _DumpFetcher:
    def __init__(self, dump: dict[str, Any]) -> None:
        self.dump = dump

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        return self.dump


def _walk_dump(*, request_count: int | None) -> dict[str, Any]:
    dump = {
        "id": UPLOADS_PLAYLIST,
        "title": "Uploads",
        "entries": [
            {"id": f"vid{index:08d}", "title": f"v{index}", "channel_id": CHANNEL} for index in range(2)
        ],
        "fetch_route": Route.DATA_API,
        "expected_count": 2,
        "returned_count": 2,
        "truncated": False,
    }
    if request_count is not None:
        dump["request_count"] = request_count
    return dump


def _watch_then_work(url: str, tmp_path: Path, dump: dict[str, Any]) -> None:
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text(f"channel {CHANNEL}\n")
    assert run("watch", read_roster=False, database_url=url, watchlist_path=watchlist, captured_at=AT) == 0
    assert (
        run(
            "work",
            database_url=url,
            fetcher=_DumpFetcher(dump),
            payload_root=tmp_path / "payloads",
            captured_at=AT,
        )
        == 0
    )


def _recorded(url: str) -> list[int | None]:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            return list(conn.execute(sa.select(artifacts.c.request_count)).scalars())
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_artifact_records_what_the_walk_spent(tubedepth_schema: str, tmp_path: Path):
    _watch_then_work(tubedepth_schema, tmp_path, _walk_dump(request_count=23))
    assert _recorded(tubedepth_schema) == [23]


@pytest.mark.postgres
def test_a_route_that_counts_nothing_leaves_the_column_null(tubedepth_schema: str, tmp_path: Path):
    """NULL is "never asked", which is what every fake fetcher in this package and every yt-dlp dump
    is. Writing 0 there would say the fetch sent no request, and `quota.py` would then charge the
    row nothing instead of falling back."""
    _watch_then_work(tubedepth_schema, tmp_path, _walk_dump(request_count=None))
    assert _recorded(tubedepth_schema) == [None]


# --- the day's bound reads it ------------------------------------------------------------------


def _artifact(
    kind: str,
    target: str,
    *,
    fetched_at: datetime,
    request_count: int | None = None,
    route: str | None = Route.DATA_API,
) -> dict[str, Any]:
    return {
        # md5, not a truncated join: `identifier` is 32 characters and a day of walks differs only
        # in the tail of that join, so truncating it gives 125 rows one primary key.
        "identifier": hashlib.md5(f"{kind}:{target}:{fetched_at.isoformat()}".encode()).hexdigest(),
        "kind": kind,
        "target": target,
        "fingerprint": f"{kind}:{target}",
        "digest": "0" * 64,
        "byte_count": 1,
        "fetched_at": fetched_at,
        "fresh_until": fetched_at,
        "schema_version": "1",
        "fetch_route": route,
        "request_count": request_count,
    }


def _insert(url: str, rows: list[dict[str, Any]]) -> None:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.insert(artifacts), rows)
    finally:
        engine.dispose()


def _spent(url: str) -> int:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            return quota.requests_spent_today(conn, now=T0)
    finally:
        engine.dispose()


def _remaining(url: str) -> int:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            return quota.remaining_requests(conn, now=T0)
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_a_walk_that_recorded_its_requests_is_charged_them(tubedepth_schema: str):
    _insert(
        tubedepth_schema,
        [_artifact("channel.videos", "UC1", fetched_at=T0 - timedelta(hours=2), request_count=41)],
    )
    assert _spent(tubedepth_schema) == 41


@pytest.mark.postgres
def test_a_row_written_before_the_column_is_still_charged_the_estimate(tubedepth_schema: str):
    """The rows already on production, and any row a deploy writes between the DDL and a route that
    fills it. They are not guessed at and not dropped: they keep the reading #259 counted them
    with."""
    _insert(
        tubedepth_schema,
        [_artifact("channel.videos", "UC1", fetched_at=T0 - timedelta(hours=2))],
    )
    assert _spent(tubedepth_schema) == quota.LISTING_REQUESTS_PER_WALK


@pytest.mark.postgres
def test_a_day_of_both_kinds_of_row_charges_each_the_way_it_was_recorded(tubedepth_schema: str):
    _insert(
        tubedepth_schema,
        [
            _artifact("channel.videos", "UC1", fetched_at=T0 - timedelta(hours=2), request_count=41),
            _artifact("channel.videos", "UC2", fetched_at=T0 - timedelta(hours=1)),
        ],
    )
    assert _spent(tubedepth_schema) == 41 + quota.LISTING_REQUESTS_PER_WALK


@pytest.mark.postgres
def test_metadata_rows_are_still_the_calls_they_rode_on(tubedepth_schema: str):
    """`video.metadata` keeps `ceil(rows / 50)`: one batched `videos.list` answers up to 50 rows, so
    the call is not any one row's to carry and the column stays NULL on all of them (#272 decision).
    The fallback is per kind for exactly this reason -- charging these rows the walk estimate, or
    one request each, would be 9 or 60 where the truth is 2."""
    _insert(
        tubedepth_schema,
        [
            _artifact("video.metadata", f"video{index:06d}", fetched_at=T0 - timedelta(hours=1))
            for index in range(60)
        ],
    )
    assert _spent(tubedepth_schema) == 2


@pytest.mark.postgres
def test_the_day_bound_trips_on_what_the_walks_actually_spent(tubedepth_schema: str):
    """The failure #272 closes, at the scale it bites: 125 ceiling-length walks spend the whole day
    and the flat charge would have called them an eighth of it, leaving a run free to spend past
    Google's own ceiling."""
    walks = quota.MAX_REQUESTS_PER_DAY // 40
    _insert(
        tubedepth_schema,
        [
            _artifact(
                "channel.videos",
                f"UC{index:04d}",
                fetched_at=T0 - timedelta(minutes=index + 1),
                request_count=40,
            )
            for index in range(walks)
        ],
    )
    assert _spent(tubedepth_schema) == walks * 40
    assert _remaining(tubedepth_schema) == quota.MAX_REQUESTS_PER_DAY - walks * 40
    assert _remaining(tubedepth_schema) < walks * quota.LISTING_REQUESTS_PER_WALK
