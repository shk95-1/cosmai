"""What actually goes out on the wire for each of the three routes, and what the run does with what
comes back (#183).

Every HTTP request here is served by an `httpx.MockTransport` reading a **saved** fixture body, and
every yt-dlp extraction by a runtime that replays a saved dump. Neither answers out of the request:
the handler looks a body up by path and page token and hands back whatever the file holds, including
a body that is *shorter* than the one before it. That distinction is the point of this file. On #90
the fake built its answer from the request, every request therefore succeeded plausibly, the whole
suite was green, and the production run wrote 0 rows -- because a fake shaped like the request can
never produce the one failure that matters here, the source returning less than we asked for.

So the three tests that carry this file are the truncation ones: a paging walk cut off by our own
cap, a walk that ends holding fewer items than the first page's `pageInfo.totalResults` said were
there, and a comment harvest that comes back at exactly the limit it asked for.

The API key in this file is a dummy string. A real one never reaches a test, a log or an error
message, which the last test of the error block asserts rather than assumes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from collectors.youtube import transport
from collectors.youtube.cli import FetchSpec
from collectors.youtube.transport import (
    BudgetExhausted,
    DataApiClient,
    LiveFetcher,
    RateLimited,
    RequestFailed,
    Route,
    Unavailable,
    YtdlpRoute,
)

API_KEY = "dummy-data-api-key"
CHANNEL_ID = "UC5oM4Ai05dQqiVL6rypAo_A"
UPLOADS_PLAYLIST = "UU5oM4Ai05dQqiVL6rypAo_A"

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(*parts: str) -> dict[str, Any]:
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


CHANNELS = _fixture("data_api", "channels-contentdetails.json")
PAGE_1 = _fixture("data_api", "uploads-page-1.json")
PAGE_2 = _fixture("data_api", "uploads-page-2.json")
PAGE_EMPTY = _fixture("data_api", "uploads-page-empty.json")
VIDEOS = _fixture("data_api", "videos-list.json")
QUOTA_403 = _fixture("data_api", "error-403-quota-exceeded.json")
FORBIDDEN_403 = _fixture("data_api", "error-403-access-not-configured.json")

COMMENTS_AT_LIMIT = _fixture("ytdlp", "comments-truncated-at-limit.json")
COMMENTS_DISABLED = _fixture("ytdlp", "comments-disabled.json")
WITH_CAPTIONS = _fixture("ytdlp", "video-with-captions.json")
WITHOUT_CAPTIONS = _fixture("ytdlp", "video-without-captions.json")
JSON3 = _fixture("transcript", "dQw4w9WgXcQ-en-json3.json")


# --- the saved-body server -----------------------------------------------------------------------


class _SavedBodies:
    """Serves a stored body per (path, pageToken). It has no idea what the request asked for beyond
    that key, so it cannot invent an answer of the right size -- which is the only reason it can
    fail the truncation tests at all."""

    def __init__(self, bodies: dict[tuple[str, str | None], tuple[int, dict[str, Any]]]) -> None:
        self._bodies = bodies
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        key = (request.url.path, request.url.params.get("pageToken"))
        if key not in self._bodies:
            return httpx.Response(404, json={"error": {"code": 404, "errors": []}})
        status, body = self._bodies[key]
        return httpx.Response(status, json=body)


def _client(handler: Any, *, budget: int | None = None, sleeps: list[float] | None = None) -> DataApiClient:
    return DataApiClient(
        API_KEY,
        transport=httpx.MockTransport(handler),
        budget=transport.RequestBudget(
            {Route.DATA_API: budget if budget is not None else 50},
            {Route.DATA_API: 0.0},
            sleep=(sleeps.append if sleeps is not None else lambda _s: None),
        ),
    )


UPLOADS_TWO_PAGES = _SavedBodies(
    {
        ("/youtube/v3/channels", None): (200, CHANNELS),
        ("/youtube/v3/playlistItems", None): (200, PAGE_1),
        ("/youtube/v3/playlistItems", PAGE_1["nextPageToken"]): (200, PAGE_2),
    }
)


class _ReplayingRuntime:
    """Hands back a saved yt-dlp dump per target. Records the options it was called with so the
    tests can assert what was asked for -- never to build the answer from."""

    def __init__(self, dumps: dict[str, Any]) -> None:
        self._dumps = dumps
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def extract(self, target: str, *, options: dict[str, Any] | None = None) -> Any:
        self.calls.append((target, dict(options or {})))
        # Keyed by video id, whatever URL the route wrapped it in -- the dumps are saved per video.
        key = target.rpartition("v=")[2] or target
        try:
            return self._dumps[key]
        except KeyError:
            raise transport.RequestFailed(
                f"yt-dlp failed for: {target} (Video unavailable)", route=Route.YTDLP
            ) from None


def _ytdlp(dumps: dict[str, Any], *, budget: int | None = None) -> YtdlpRoute:
    return YtdlpRoute(
        runtime=_ReplayingRuntime(dumps),
        budget=transport.RequestBudget(
            {Route.YTDLP: budget if budget is not None else 50}, {Route.YTDLP: 0.0}, sleep=lambda _s: None
        ),
    )


# --- what goes out -------------------------------------------------------------------------------


def test_the_data_api_host_and_the_three_paths_are_pinned():
    """These literals are the console the key belongs to (contracts/secrets.md). Moving one is a
    decision, not a refactor -- #182 shipped the wrong host for a whole issue and only a live 401
    could say so."""
    assert transport.DATA_API_HOST == "https://www.googleapis.com/youtube/v3"
    assert transport.CHANNELS_PATH == "/youtube/v3/channels"
    assert transport.PLAYLIST_ITEMS_PATH == "/youtube/v3/playlistItems"
    assert transport.VIDEOS_PATH == "/youtube/v3/videos"


def test_a_channel_listing_resolves_the_uploads_playlist_then_walks_it():
    handler = _SavedBodies(dict(UPLOADS_TWO_PAGES._bodies))
    dump = _client(handler).listing("channel.videos", CHANNEL_ID)

    first, second, third = handler.seen
    assert first.url.path == transport.CHANNELS_PATH
    assert first.url.params["id"] == CHANNEL_ID
    assert first.url.params["part"] == "contentDetails"
    assert second.url.path == transport.PLAYLIST_ITEMS_PATH
    assert second.url.params["playlistId"] == UPLOADS_PLAYLIST
    assert third.url.params["pageToken"] == PAGE_1["nextPageToken"]

    assert dump["id"] == UPLOADS_PLAYLIST
    assert [entry["id"] for entry in dump["entries"]] == ["aB3dEf7GhJk", "dQw4w9WgXcQ", "9bZkp7q19f0"]
    assert dump["fetch_route"] == Route.DATA_API


def test_the_api_key_travels_as_a_query_parameter_and_not_in_a_header():
    handler = _SavedBodies(dict(UPLOADS_TWO_PAGES._bodies))
    _client(handler).listing("channel.videos", CHANNEL_ID)
    for request in handler.seen:
        assert request.url.params["key"] == API_KEY
        assert API_KEY not in str(request.headers)


def test_a_handle_target_is_resolved_by_handle_rather_than_by_id():
    handler = _SavedBodies(dict(UPLOADS_TWO_PAGES._bodies))
    _client(handler).listing("channel.videos", "@beauty_channel")
    assert handler.seen[0].url.params["forHandle"] == "@beauty_channel"
    assert "id" not in handler.seen[0].url.params


def test_a_playlist_listing_skips_the_channel_lookup():
    handler = _SavedBodies({("/youtube/v3/playlistItems", None): (200, PAGE_2)})
    dump = _client(handler).listing("playlist.items", UPLOADS_PLAYLIST)
    assert [request.url.path for request in handler.seen] == [transport.PLAYLIST_ITEMS_PATH]
    assert [entry["id"] for entry in dump["entries"]] == ["9bZkp7q19f0"]


def test_the_uploads_page_becomes_exactly_the_shape_normalize_listing_already_parses():
    """One normalizer for both routes (`sources.normalize_listing`), so the Work 6 comparison is
    comparing two listings and not two parsers."""
    from collectors.youtube import sources

    handler = _SavedBodies(dict(UPLOADS_TWO_PAGES._bodies))
    listing = sources.normalize_listing(
        _client(handler).listing("channel.videos", CHANNEL_ID), source_kind="channel.videos"
    )
    assert [video["video_id"] for video in listing["videos"]] == [
        "aB3dEf7GhJk",
        "dQw4w9WgXcQ",
        "9bZkp7q19f0",
    ]
    assert listing["videos"][0]["channel_id"] == CHANNEL_ID
    assert listing["videos"][0]["published_at"] == datetime(2026, 9, 10, 9, 0, 7, tzinfo=UTC)
    assert listing["videos"][-1]["published_at"] == datetime(2024, 8, 2, 9, 0, 7, tzinfo=UTC)


# --- the source returned less than we asked for ----------------------------------------------------


def test_a_walk_that_ends_short_of_the_reported_total_says_so():
    """The #90 axis. Page 1 says `totalResults: 5` and the walk ends holding 3, because page 2 came
    back with one item and no token. Nothing about the request says that; only the saved bodies do.
    A dump that did not carry the gap would be a successful-looking listing missing 40% of a
    channel, which is exactly what production saw and the suite did not."""
    handler = _SavedBodies(dict(UPLOADS_TWO_PAGES._bodies))
    dump = _client(handler).listing("channel.videos", CHANNEL_ID)

    assert dump["returned_count"] == 3
    assert dump["expected_count"] == 5
    assert dump["truncated"] is False  # our own cap did not stop it; the source simply gave less


def test_a_walk_stopped_by_our_own_item_cap_is_marked_truncated_instead():
    handler = _SavedBodies(dict(UPLOADS_TWO_PAGES._bodies))
    dump = _client(handler).listing("channel.videos", CHANNEL_ID, max_items=2)

    assert dump["returned_count"] == 2
    assert dump["truncated"] is True
    # The second page is never asked for: the cap is spent.
    assert [request.url.path for request in handler.seen] == [
        transport.CHANNELS_PATH,
        transport.PLAYLIST_ITEMS_PATH,
    ]


def test_a_page_with_a_token_and_no_items_ends_the_walk_rather_than_looping():
    """The Data API returns this when every item on a page was filtered out. A walker that stops
    only on a missing token spins until the budget runs out, and the budget is the request quota."""
    handler = _SavedBodies(
        {
            ("/youtube/v3/channels", None): (200, CHANNELS),
            ("/youtube/v3/playlistItems", None): (200, PAGE_1),
            ("/youtube/v3/playlistItems", PAGE_1["nextPageToken"]): (200, PAGE_EMPTY),
        }
    )
    dump = _client(handler).listing("channel.videos", CHANNEL_ID)
    assert dump["returned_count"] == 2
    assert len(handler.seen) == 3


def test_the_walk_stops_at_the_first_entry_published_before_the_window():
    handler = _SavedBodies(dict(UPLOADS_TWO_PAGES._bodies))
    dump = _client(handler).listing("channel.videos", CHANNEL_ID, since="2026-01-01")
    assert [entry["id"] for entry in dump["entries"]] == ["aB3dEf7GhJk", "dQw4w9WgXcQ"]
    assert dump["truncated"] is True


def test_a_comment_harvest_that_comes_back_at_the_limit_is_marked_truncated():
    """Four comments against a request for four, on a video whose own `comment_count` is 5,127. A
    complete harvest and a cut-off one are the same JSON apart from that comparison."""
    route = _ytdlp({"dQw4w9WgXcQ": COMMENTS_AT_LIMIT})
    dump = route.comments("dQw4w9WgXcQ", limit=4)

    assert dump["returned_count"] == 4
    assert dump["truncated"] is True
    assert dump["fetch_route"] == Route.YTDLP


def test_a_harvest_below_the_limit_is_not_truncated():
    route = _ytdlp({"dQw4w9WgXcQ": COMMENTS_AT_LIMIT})
    dump = route.comments("dQw4w9WgXcQ", limit=40)
    assert dump["returned_count"] == 4
    assert dump["truncated"] is False


def test_comments_turned_off_are_zero_rows_and_not_a_failure():
    from collectors.youtube import sources

    route = _ytdlp({"9bZkp7q19f0": COMMENTS_DISABLED})
    dump = route.comments("9bZkp7q19f0", limit=40)
    assert dump["truncated"] is False
    assert sources.normalize_comments(dump) == {"comments": []}


def test_the_comment_limit_is_what_the_extractor_is_actually_asked_for():
    """A limit that never reaches yt-dlp is a cap that does nothing, and the harvest comes back
    whatever size YouTube feels like -- the truncation flag above would then be measuring the
    request against itself."""
    runtime = _ReplayingRuntime({"dQw4w9WgXcQ": COMMENTS_AT_LIMIT})
    route = YtdlpRoute(
        runtime=runtime,
        budget=transport.RequestBudget({Route.YTDLP: 10}, {Route.YTDLP: 0.0}, sleep=lambda _s: None),
    )
    route.comments("dQw4w9WgXcQ", limit=17)
    (_target, options) = runtime.calls[0]
    assert options["getcomments"] is True
    assert options["extractor_args"]["youtube"]["max_comments"][0] == "17"


# --- the transcript route ---------------------------------------------------------------------------


def _timedtext(body: dict[str, Any], status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda _request: httpx.Response(status, json=body))


def test_the_transcript_route_discovers_a_track_with_ytdlp_and_fetches_it_over_httpx():
    route = transport.TimedtextRoute(
        ytdlp=_ytdlp({"dQw4w9WgXcQ": WITH_CAPTIONS}),
        transport=_timedtext(JSON3),
        budget=transport.RequestBudget(
            {Route.YTDLP: 10, Route.TIMEDTEXT: 10},
            {Route.YTDLP: 0.0, Route.TIMEDTEXT: 0.0},
            sleep=lambda _s: None,
        ),
    )
    dump = route.transcript("dQw4w9WgXcQ")

    assert dump["language"] == "ko"
    assert dump["is_automatic"] is False  # the manual track outranks the ASR one
    assert dump["events"] == JSON3["events"]
    assert dump["fetch_route"] == Route.TIMEDTEXT


def test_an_auto_translated_track_is_never_a_candidate():
    """`tlang=` is this video's words run through a translator. Serving one makes a transcript's
    language a fact about how recently we ran rather than about the video."""
    tracks = transport.caption_track_candidates(WITH_CAPTIONS)
    assert [(track.language, track.is_automatic) for track in tracks] == [("ko", False), ("ko", True)]
    assert all("tlang=" not in track.url for track in tracks)


def test_a_video_with_no_caption_track_is_unavailable_rather_than_a_transport_failure():
    route = transport.TimedtextRoute(
        ytdlp=_ytdlp({"9bZkp7q19f0": WITHOUT_CAPTIONS}),
        transport=_timedtext(JSON3),
        budget=transport.RequestBudget(
            {Route.YTDLP: 10, Route.TIMEDTEXT: 10},
            {Route.YTDLP: 0.0, Route.TIMEDTEXT: 0.0},
            sleep=lambda _s: None,
        ),
    )
    with pytest.raises(Unavailable):
        route.transcript("9bZkp7q19f0")


# --- errors, by route ------------------------------------------------------------------------------


def test_a_403_quota_body_raises_an_error_that_still_carries_code_and_read():
    """contracts/entrypoints.md puts the burden here: `_classify_error` was written against
    `urllib.error.HTTPError`'s `.code`/`.read()` and "making whatever transport #10 attaches raise in
    that shape is #10's job"."""
    handler = _SavedBodies({("/youtube/v3/channels", None): (403, QUOTA_403)})
    with pytest.raises(transport.Blocked) as raised:
        _client(handler).listing("channel.videos", CHANNEL_ID)

    assert raised.value.code == 403
    assert raised.value.route == Route.DATA_API
    assert json.loads(raised.value.read())["error"]["errors"][0]["reason"] == "quotaExceeded"


def test_a_403_that_is_not_quota_carries_its_own_body():
    handler = _SavedBodies({("/youtube/v3/channels", None): (403, FORBIDDEN_403)})
    with pytest.raises(transport.Blocked) as raised:
        _client(handler).listing("channel.videos", CHANNEL_ID)
    assert json.loads(raised.value.read())["error"]["errors"][0]["reason"] == "accessNotConfigured"


def test_a_429_is_rate_limited_on_the_route_it_happened_on():
    handler = _SavedBodies({("/youtube/v3/channels", None): (429, {"error": {"code": 429}})})
    with pytest.raises(RateLimited) as raised:
        _client(handler).listing("channel.videos", CHANNEL_ID)
    assert raised.value.route == Route.DATA_API
    assert raised.value.code == 429


def test_a_ytdlp_bot_check_is_rate_limited_and_names_the_ytdlp_route():
    """yt-dlp reports a private video, a network blip and a bot check as one `DownloadError` with
    the reason in the message. The bot check is the one that is evidence about the address, and it
    has to arrive as something the caller recognises rather than as an unexpected exception."""

    class _BotChecked:
        def extract(self, target: str, *, options: dict[str, Any] | None = None) -> Any:
            raise transport.ytdlp_error(
                "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot", target=target
            )

    route = YtdlpRoute(
        runtime=_BotChecked(),
        budget=transport.RequestBudget({Route.YTDLP: 10}, {Route.YTDLP: 0.0}, sleep=lambda _s: None),
    )
    with pytest.raises(RateLimited) as raised:
        route.comments("dQw4w9WgXcQ", limit=4)
    assert raised.value.route == Route.YTDLP
    assert raised.value.code is None  # yt-dlp has no status to report


def test_a_private_video_is_unavailable_rather_than_rate_limited():
    error = transport.ytdlp_error("ERROR: [youtube] xxx: Private video", target="xxx")
    assert isinstance(error, Unavailable)


def test_an_unknown_ytdlp_message_stays_a_request_failure():
    """Guessing a route is blocked from a message nobody has read yet is how a working address gets
    quarantined."""
    error = transport.ytdlp_error("ERROR: something new", target="xxx")
    assert isinstance(error, RequestFailed)
    assert not isinstance(error, (RateLimited, Unavailable))


def test_no_error_message_repeats_the_api_key():
    handler = _SavedBodies({("/youtube/v3/channels", None): (403, FORBIDDEN_403)})
    with pytest.raises(transport.TransportError) as raised:
        _client(handler).listing("channel.videos", CHANNEL_ID)
    assert API_KEY not in str(raised.value)


# --- the per-source budget and pause ----------------------------------------------------------------


def test_every_request_is_charged_to_its_own_route_and_a_spent_budget_sends_nothing():
    handler = _SavedBodies(dict(UPLOADS_TWO_PAGES._bodies))
    client = _client(handler, budget=2)
    with pytest.raises(BudgetExhausted):
        client.listing("channel.videos", CHANNEL_ID)
    assert len(handler.seen) == 2  # the third request is refused before it is sent


def test_the_pause_between_requests_is_the_one_scope_json_names():
    handler = _SavedBodies(dict(UPLOADS_TWO_PAGES._bodies))
    sleeps: list[float] = []
    budget = transport.RequestBudget({Route.DATA_API: 50}, {Route.DATA_API: 0.75}, sleep=sleeps.append)
    DataApiClient(API_KEY, transport=httpx.MockTransport(handler), budget=budget).listing(
        "channel.videos", CHANNEL_ID
    )
    # Three requests, two pauses: the pause is between requests, not before the first.
    assert sleeps == [0.75, 0.75]


def test_one_route_spending_its_budget_leaves_the_others_alone():
    budget = transport.RequestBudget(
        {Route.DATA_API: 1, Route.YTDLP: 5}, {Route.DATA_API: 0.0, Route.YTDLP: 0.0}, sleep=lambda _s: None
    )
    budget.charge(Route.DATA_API)
    with pytest.raises(BudgetExhausted):
        budget.charge(Route.DATA_API)
    budget.charge(Route.YTDLP)
    assert budget.spent[Route.YTDLP] == 1


# --- the fetcher the CLI plugs in ---------------------------------------------------------------------


def _live_fetcher(**overrides: Any) -> LiveFetcher:
    handler = _SavedBodies(dict(UPLOADS_TWO_PAGES._bodies))
    budget = transport.RequestBudget(
        {Route.DATA_API: 50, Route.YTDLP: 50, Route.TIMEDTEXT: 50},
        {Route.DATA_API: 0.0, Route.YTDLP: 0.0, Route.TIMEDTEXT: 0.0},
        sleep=lambda _s: None,
    )
    kwargs: dict[str, Any] = {
        "data_api": DataApiClient(API_KEY, transport=httpx.MockTransport(handler), budget=budget),
        "ytdlp": _ytdlp({"dQw4w9WgXcQ": COMMENTS_AT_LIMIT}),
        "timedtext": transport.TimedtextRoute(
            ytdlp=_ytdlp({"dQw4w9WgXcQ": WITH_CAPTIONS}),
            transport=_timedtext(JSON3),
            budget=budget,
        ),
        "budget": budget,
    }
    kwargs.update(overrides)
    return LiveFetcher(**kwargs)


@pytest.mark.parametrize(
    ("kind", "target", "route"),
    [
        ("channel.videos", CHANNEL_ID, Route.DATA_API),
        ("playlist.items", UPLOADS_PLAYLIST, Route.DATA_API),
        ("video.comments", "dQw4w9WgXcQ", Route.YTDLP),
        ("video.transcript", "dQw4w9WgXcQ", Route.TIMEDTEXT),
    ],
)
def test_each_job_kind_goes_out_on_the_route_the_decision_named(kind: str, target: str, route: str):
    dump = _live_fetcher().fetch(FetchSpec(kind=kind, target=target))
    assert dump["fetch_route"] == route


def test_video_metadata_follows_the_scope_json_route_and_records_which_one_ran():
    ytdlp = _ytdlp({"dQw4w9WgXcQ": dict(WITH_CAPTIONS, title="long upload")})
    over_ytdlp = _live_fetcher(ytdlp=ytdlp, metadata_route=Route.YTDLP)
    assert over_ytdlp.fetch(FetchSpec(kind="video.metadata", target="dQw4w9WgXcQ"))["fetch_route"] == (
        Route.YTDLP
    )

    handler = _SavedBodies({("/youtube/v3/videos", None): (200, VIDEOS)})
    budget = transport.RequestBudget({Route.DATA_API: 9}, {Route.DATA_API: 0.0}, sleep=lambda _s: None)
    over_api = _live_fetcher(
        data_api=DataApiClient(API_KEY, transport=httpx.MockTransport(handler), budget=budget),
        metadata_route=Route.DATA_API,
    )
    dump = over_api.fetch(FetchSpec(kind="video.metadata", target="dQw4w9WgXcQ"))
    assert dump["fetch_route"] == Route.DATA_API
    # Same keys `sources.normalize_video_metadata` reads off a yt-dlp dump -- one normalizer, two routes.
    assert dump["id"] == "dQw4w9WgXcQ"
    assert dump["duration"] == 253  # PT4M13S
    assert dump["view_count"] == 1523
    assert dump["upload_date"] == "20260901"


def test_an_unknown_job_kind_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="video.chapters"):
        _live_fetcher().fetch(FetchSpec(kind="video.chapters", target="dQw4w9WgXcQ"))


# --- the one real request -----------------------------------------------------------------------


@pytest.mark.live
def test_one_live_uploads_listing_is_accepted_by_the_normalizer():
    """`-m live`, so it runs only when a person asks for it (`pyproject.toml` markers, and
    `tests/conftest.py` refuses a socket to anything unmarked). What it proves is the join the
    fixtures cannot: that this key opens `channels.list` and `playlistItems.list`, that the uploads
    playlist of a real channel pages the way the walker assumes, and that
    `sources.normalize_listing` finds videos in what Google actually answers with.

    One channel, capped at one page. It writes nothing -- no database, no artifact, no job row --
    and the channel is the archive's own reference target rather than a panel channel, so running it
    spends a unit of quota and touches nothing this collector owns.
    """
    from collectors.youtube import sources
    from collectors.youtube.cli import DATA_API_SECRET_KEY
    from db import secrets

    key = secrets.load().get(DATA_API_SECRET_KEY)
    if not key:
        pytest.skip(f"no {DATA_API_SECRET_KEY} on this host")

    client = DataApiClient(key)
    try:
        dump = client.listing("channel.videos", "@YouTube", max_items=5, since="2000-01-01")
    finally:
        client.close()

    listing = sources.normalize_listing(dump, source_kind="channel.videos")
    assert listing["videos"], "the uploads playlist answered, but the normalizer found no video in it"
    assert all(len(video["video_id"]) == 11 for video in listing["videos"])
    assert dump["expected_count"] is not None, "pageInfo.totalResults is what the shortfall check reads"
