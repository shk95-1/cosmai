"""The live transport (#183): three routes behind the one `Fetcher` seam `cli.py` already had.

    listing  (channel.videos · playlist.items · search.videos · trending.videos)  YouTube Data API v3
    video.metadata                                                                either route
    video.comments                                                                yt-dlp
    video.transcript                                                              timedtext json3

**Why the listing is not yt-dlp**, which is what this issue's Work first said: yt-dlp lists a
channel's *videos tab*, and that tab excludes Shorts and past live. Measured on the production
database 2026-09-12, `needs.corpus_document` holds video_long 7,085 · video_short 6,888, so a
videos-tab listing loses about half the video axis and the denominator the judgement rests on stops
being closed. The uploads playlist is the whole upload history, and `videos.list` costs one quota
unit whatever parts are asked for. `tool/measure-youtube-listing-routes` is the measurement that
confirms or flips that (Work 6); until it has run, the Shorts claim is Google's documentation and
the archive's 697-vs-474, not ours.

**Every route annotates its dump** with `fetch_route` and, for the kinds that can come back short,
`expected_count` · `returned_count` · `truncated`. `cli._collect_one` writes the route onto the
artifact row (`artifacts.fetch_route`, DDL 005) so an analysis axis can declare which routes it
accepts -- the population model of shk95/cosmai-import-ydc#91, option D -- and turns a short listing
into a partial run. That last part is the lesson of #90: a fetch that quietly returns less than the
source holds looks exactly like a successful one, and the suite that only ever asked a fake built
out of the request could not tell them apart.

Shaped after `collectors/naver/transport.py` (#182): one client per run, an injectable
`httpx.BaseTransport` so tests drive whole runs without a socket, a per-source request budget, and
the classification of what comes back as the interesting part. What differs is that there are three
sources here rather than one gateway, so the error a job fails with has to say which of them it came
from -- a Data API 403 body carries `reason: quotaExceeded` and a yt-dlp failure carries no status
at all, and `cli._classify_error` cannot read one as the other.

The resilience layer of the archive -- retry and `attempt_count`, the AIMD window, lane quarantine,
lease and heartbeat -- is deliberately not here (the issue's "Not in this issue"). What is here is
the budget that stops a runaway walk and the pause that keeps us unremarkable in someone's logs.

The API key is set on the client once, never logged, and never interpolated into an error message.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from types import TracebackType
from typing import TYPE_CHECKING, Any

import httpx

from collectors.youtube.models import (
    LISTING_WINDOW_START,
    MAX_COMMENTS_PER_VIDEO,
    MAX_LISTING_ITEMS,
    ROUTES,
    VIDEO_METADATA_ROUTE,
    VIDEO_PARTS,
)

if TYPE_CHECKING:  # cli imports this module; the spec type comes back the other way for typing only.
    from collectors.youtube.cli import FetchSpec

DATA_API_HOST = "https://www.googleapis.com/youtube/v3"
CHANNELS_PATH = "/youtube/v3/channels"
PLAYLIST_ITEMS_PATH = "/youtube/v3/playlistItems"
VIDEOS_PATH = "/youtube/v3/videos"
SEARCH_PATH = "/youtube/v3/search"

#: The vendor's own page cap on every list method. Asking for more is refused, not silently clamped.
PAGE_SIZE = 50

#: We identify ourselves rather than imitate a browser -- STATE.md §3, and the same line
#: `collectors/naver/transport.py` and `collectors/commerce/contract.py` state for their sources.
#: The archive set a Chrome user agent here; that is the one thing from it deliberately not ported.
USER_AGENT = "cosmai-youtube/0.1"


class Route(StrEnum):
    """Which source answered. Written to `artifacts.fetch_route` on every row, and the first thing
    `cli._classify_error` asks an error, because the three sources report failure in three
    vocabularies and only one of them has an HTTP status."""

    DATA_API = "data_api"
    YTDLP = "ytdlp"
    TIMEDTEXT = "timedtext"


class TransportError(Exception):
    """`code` and `read()` are `urllib.error.HTTPError`'s shape on purpose: contracts/entrypoints.md
    says the classifier was written against `.code`/`.read()` and that "making whatever transport #10
    attaches raise in that shape is #10's job". `route` is what this issue adds -- two of the three
    sources have no status to put in `code`, so the code alone can no longer say what happened."""

    def __init__(self, message: str, *, route: str, code: int | None = None, body: bytes = b"") -> None:
        super().__init__(message)
        self.route = route
        self.code = code
        self._body = body

    def read(self) -> bytes:
        return self._body


class Blocked(TransportError):
    """401/403 -- the credential or the quota. Every later request goes the same way (exit 2)."""


class RateLimited(TransportError):
    """429, or yt-dlp's bot check -- the one yt-dlp failure that is evidence about the address
    rather than about the video. More requests now would only deepen it."""


class Unavailable(TransportError):
    """The thing exists and we may not have it: private, members-only, age-gated, removed, or a
    video with no caption track. No retry helps and nothing is wrong with the route."""


class BudgetExhausted(TransportError):
    """This run has spent `scope.json`'s `ROUTES[route].max_requests_per_run`."""


class RequestFailed(TransportError):
    """One request failed. The run goes on and ends partial if anything was collected."""


# What yt-dlp says when YouTube wants proof we are a person. The only message of the set that is
# about the address rather than about the video, which is why it is the only one that becomes
# RateLimited. Copied from the archive's BOT_CHECK_MARKERS (sources/ytdlp_runtime.py).
BOT_CHECK_MARKERS = ("sign in to confirm you're not a bot", "confirm you're not a bot")

# The video exists and we may not have it. Copied from the archive's UNAVAILABLE_MARKERS, which
# collected both phrasings of each after seeing them on consecutive live runs.
UNAVAILABLE_MARKERS = (
    "private video",
    "video unavailable",
    "video is unavailable",
    "this video is available to this channel's members",
    "join this channel to get access",
    "sign in to confirm your age",
    "age-restricted",
    "not made this video available in your country",
    "video has been removed",
    "account associated with this video has been terminated",
    "this live event has ended",
)


def ytdlp_error(message: str, *, target: str) -> TransportError:
    """yt-dlp reports a private video, a network blip and a bot check as one `DownloadError` with the
    reason in the message, so the message is the only thing there is to classify on.

    An unrecognised message stays `RequestFailed` rather than being guessed at: deciding a route is
    blocked from a message nobody has read yet is how a working address gets quarantined."""
    lowered = message.lower()
    if any(marker in lowered for marker in BOT_CHECK_MARKERS):
        return RateLimited(f"youtube asked for proof we are not a bot, fetching: {target}", route=Route.YTDLP)
    if any(marker in lowered for marker in UNAVAILABLE_MARKERS):
        return Unavailable(f"video cannot be watched from here: {target} ({message})", route=Route.YTDLP)
    return RequestFailed(f"yt-dlp failed for: {target} ({message})", route=Route.YTDLP)


class RequestBudget:
    """One per run, shared by the three routes but counted per route -- a listing walk that spends
    the Data API's budget must not also stop comments, which are somebody else's quota entirely.

    Every attempt is charged, a retry included, because a retry is another request the source
    serves (the rule `collectors/naver/transport.py` and `collectors/commerce/engine.py` both state).
    The pause goes *between* requests on a route, not before its first: the first one costs nothing
    to nobody, and paying for it once per job is a second of every run spent asleep for no reason.
    """

    def __init__(
        self,
        limits: Mapping[str, int],
        pauses: Mapping[str, float],
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.limits = dict(limits)
        self.pauses = dict(pauses)
        self.spent: dict[str, int] = dict.fromkeys(limits, 0)
        self._sleep = sleep

    def charge(self, route: str) -> None:
        spent = self.spent.get(route, 0)
        limit = self.limits.get(route, 0)
        if spent >= limit:
            raise BudgetExhausted(
                f"this run has spent its budget of {limit} request(s) on {route}", route=route
            )
        if spent:
            pause = self.pauses.get(route, 0.0)
            if pause:
                self._sleep(pause)
        self.spent[route] = spent + 1

    @classmethod
    def from_scope(cls, *, sleep: Callable[[float], None] = time.sleep) -> RequestBudget:
        return cls(
            {route: int(values["max_requests_per_run"]) for route, values in ROUTES.items()},
            {route: float(values["sleep_seconds"]) for route, values in ROUTES.items()},
            sleep=sleep,
        )


# --- the YouTube Data API ------------------------------------------------------------------------

_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_iso_duration(value: str | None) -> int | None:
    """`contentDetails.duration` is ISO 8601 (`PT4M13S`); every other shape in this collector counts
    seconds. A live stream in progress comes back as `P0D`, which is zero here rather than an error."""
    if not value:
        return None
    found = _ISO_DURATION.match(value)
    if found is None:
        return None
    parts = {name: int(raw or 0) for name, raw in found.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    """Every count in a Data API response is a decimal *string*; a video with statistics hidden has
    no key at all rather than a zero."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _entry(
    *,
    video_id: str | None,
    title: str | None,
    channel: str | None,
    channel_id: str | None,
    published: datetime | None,
    duration: int | None = None,
    view_count: int | None = None,
) -> dict[str, Any]:
    """One listed video in the shape `sources.normalize_listing` already parses -- yt-dlp's flat
    playlist entry. Both listing routes produce this, so Work 6 compares two listings rather than
    two parsers, and `sources.py` needs no second normalizer for the Data API."""
    return {
        "id": video_id,
        "title": title,
        "duration": duration,
        "view_count": view_count,
        "channel": channel,
        "channel_id": channel_id,
        "timestamp": int(published.timestamp()) if published is not None else None,
    }


def _playlist_item_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    details = item.get("contentDetails") or {}
    # videoPublishedAt is when the video went up; snippet.publishedAt is when it was added to the
    # playlist. On an uploads playlist they usually agree, and where they do not the video's own
    # instant is the one every downstream date axis means.
    published = _instant(details.get("videoPublishedAt")) or _instant(snippet.get("publishedAt"))
    return _entry(
        video_id=details.get("videoId") or (snippet.get("resourceId") or {}).get("videoId"),
        title=snippet.get("title"),
        channel=snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle"),
        channel_id=snippet.get("videoOwnerChannelId") or snippet.get("channelId"),
        published=published,
    )


def _search_item_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    return _entry(
        video_id=(item.get("id") or {}).get("videoId"),
        title=snippet.get("title"),
        channel=snippet.get("channelTitle"),
        channel_id=snippet.get("channelId"),
        published=_instant(snippet.get("publishedAt")),
    )


def _video_item_entry(item: Mapping[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    return _entry(
        video_id=item.get("id"),
        title=snippet.get("title"),
        channel=snippet.get("channelTitle"),
        channel_id=snippet.get("channelId"),
        published=_instant(snippet.get("publishedAt")),
        duration=parse_iso_duration((item.get("contentDetails") or {}).get("duration")),
        view_count=_as_int((item.get("statistics") or {}).get("viewCount")),
    )


def _archive_bool(value: Any) -> str | None:
    """`"True"`/`"False"`, capitalised -- a Python `repr`, not JSON.

    This is not a style choice and it must not be tidied. `analysis/sensitivity/pipeline.py` reads
    `source_metadata ->> 'has_paid_product_placement'` and compares it against its `DECLARED`
    constant, which is the string `"True"`; the archive's 13,979 video rows are stored that way
    (2,025 True / 11,954 False, measured). Writing JSON's `true` here makes that comparison false for
    every live row and the declared half of ad marking silently becomes zero -- no error, rows still
    returned. `tests/collectors/youtube/test_youtube_source_metadata.py` asserts this against
    `analysis.sensitivity.pipeline.DECLARED` itself rather than against a literal, so normalising
    either side fails a test instead of a measurement.

    `None` stays `None`: "the route could not tell us" is not "the uploader said no".
    """
    if value is None:
        return None
    if isinstance(value, str):
        return "True" if value.strip().lower() == "true" else "False"
    return "True" if value else "False"


class DataApiClient:
    """One per run. The key rides as the `key` query parameter, which is what an API-key (rather than
    OAuth) request to this API uses -- never a header, so it cannot leak into one that gets logged."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        budget: RequestBudget | None = None,
        video_parts: Sequence[str] = VIDEO_PARTS,
    ) -> None:
        self._key = api_key
        self._budget = budget if budget is not None else RequestBudget.from_scope()
        self._video_parts = list(video_parts)
        self._client = httpx.Client(
            base_url=DATA_API_HOST.rsplit("/youtube/v3", 1)[0],
            timeout=float(ROUTES[Route.DATA_API]["timeout_s"]),
            transport=transport,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def __enter__(self) -> DataApiClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- the listing routes ------------------------------------------------------------------------

    def listing(
        self, kind: str, target: str, *, max_items: int | None = None, since: str | None = None
    ) -> dict[str, Any]:
        cap = MAX_LISTING_ITEMS if max_items is None else max_items
        window_start = LISTING_WINDOW_START if since is None else since
        if kind == "channel.videos":
            playlist = self.uploads_playlist(target)
            return self._walk_listing(
                PLAYLIST_ITEMS_PATH,
                {"part": "snippet,contentDetails", "playlistId": playlist},
                _playlist_item_entry,
                listing_id=playlist,
                cap=cap,
                since=window_start,
            )
        if kind == "playlist.items":
            return self._walk_listing(
                PLAYLIST_ITEMS_PATH,
                {"part": "snippet,contentDetails", "playlistId": target},
                _playlist_item_entry,
                listing_id=target,
                cap=cap,
                since=window_start,
            )
        if kind == "search.videos":
            # 100 quota units a page against the uploads playlist's 1, which is why no watchlist
            # directive for the panel uses it -- it stays for an operator's one-off query.
            return self._walk_listing(
                SEARCH_PATH,
                {"part": "snippet", "q": target, "type": "video", "order": "date"},
                _search_item_entry,
                listing_id=target,
                cap=cap,
                since=window_start,
            )
        if kind == "trending.videos":
            # `target` is a region code (KR). chart=mostPopular is not paged by date, so the
            # publication window does not apply to it.
            return self._walk_listing(
                VIDEOS_PATH,
                {
                    "part": "snippet,contentDetails,statistics",
                    "chart": "mostPopular",
                    "regionCode": target,
                },
                _video_item_entry,
                listing_id=target,
                cap=cap,
                since=None,
            )
        raise ValueError(f"the Data API has no listing route for job kind {kind!r}")

    def uploads_playlist(self, target: str) -> str:
        """`channels.list` rather than deriving `UU…` from `UC…` by swapping two characters. The
        derivation is real and undocumented; this costs one unit, and it is also the only thing that
        tells a renamed handle from a channel that is gone."""
        selector = {"forHandle": target} if target.startswith("@") else {"id": target}
        body = self._get(CHANNELS_PATH, {"part": "contentDetails", **selector})
        items = body.get("items") or []
        if not items:
            raise Unavailable(f"no channel answers to {target!r}", route=Route.DATA_API)
        related = (items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}
        uploads = related.get("uploads")
        if not uploads:
            raise Unavailable(f"channel {target!r} publishes no uploads playlist", route=Route.DATA_API)
        return str(uploads)

    def _walk_listing(
        self,
        path: str,
        params: Mapping[str, Any],
        to_entry: Callable[[Mapping[str, Any]], dict[str, Any]],
        *,
        listing_id: str,
        cap: int,
        since: str | None,
    ) -> dict[str, Any]:
        """Page until one of four things stops it, and say which one on the dump.

        Three of the four mean "there is more and we did not take it" (`truncated`): our item cap,
        the publication window, and a page we chose not to follow. The fourth -- the source simply
        running out -- does not, and that is the case where `returned_count` short of
        `expected_count` is the source having given us less than it said it had. `work` turns the
        first into a note and the second into a partial run; conflating them would make every capped
        walk look like a source that lost rows."""
        cutoff = date.fromisoformat(since) if since else None
        entries: list[dict[str, Any]] = []
        expected: int | None = None
        token: str | None = None
        truncated = False
        while True:
            page_params = dict(params)
            page_params["maxResults"] = min(PAGE_SIZE, cap - len(entries)) or 1
            if token:
                page_params["pageToken"] = token
            body = self._get(path, page_params)
            if expected is None:
                expected = _as_int((body.get("pageInfo") or {}).get("totalResults"))
            items = body.get("items") or []
            reached_window = False
            for item in items:
                entry = to_entry(item)
                if cutoff is not None and _before(entry["timestamp"], cutoff):
                    # The listing comes back newest first, so the first entry outside the window
                    # means the rest of the walk is outside it too.
                    reached_window = True
                    truncated = True
                    break
                entries.append(entry)
            token = body.get("nextPageToken")
            if reached_window or token is None:
                break
            if len(entries) >= cap:
                truncated = True
                break
            if not items:
                # A page with a token and no items at all: the API returns this when everything on
                # the page was filtered out, and a walker that stops only on a missing token spins
                # here until it has spent the day's quota.
                break
        return {
            "id": listing_id,
            "title": None,
            "entries": entries,
            "fetch_route": Route.DATA_API,
            "expected_count": expected,
            "returned_count": len(entries),
            "truncated": truncated,
        }

    # -- video.metadata -----------------------------------------------------------------------------

    def video_metadata(self, video_id: str) -> dict[str, Any]:
        """`videos.list` in the shape `sources.normalize_video_metadata` reads off a yt-dlp dump, so
        either route lands in the same row. One unit whatever parts are asked for."""
        body = self._videos_list(video_id)
        items = body.get("items") or []
        if not items:
            raise Unavailable(f"no video answers to {video_id!r}", route=Route.DATA_API)
        item = items[0]
        snippet = item.get("snippet") or {}
        statistics = item.get("statistics") or {}
        details = item.get("contentDetails") or {}
        placement = item.get("paidProductPlacementDetails")
        published = _instant(snippet.get("publishedAt"))
        return {
            "id": item.get("id") or video_id,
            "title": snippet.get("title"),
            "description": snippet.get("description") or "",
            "channel": snippet.get("channelTitle"),
            "channel_id": snippet.get("channelId"),
            "duration": parse_iso_duration(details.get("duration")),
            "view_count": _as_int(statistics.get("viewCount")),
            "like_count": _as_int(statistics.get("likeCount")),
            "comment_count": _as_int(statistics.get("commentCount")),
            "timestamp": int(published.timestamp()) if published is not None else None,
            "upload_date": published.strftime("%Y%m%d") if published is not None else None,
            "tags": list(snippet.get("tags") or []),
            "category_id": snippet.get("categoryId"),
            "caption_available": details.get("caption"),
            # Absent rather than False when the part came back empty: "this route could not tell us"
            # and "the uploader said no" are different facts, and `_archive_bool` keeps them apart.
            "has_paid_product_placement": (
                (placement or {}).get("hasPaidProductPlacement") if placement is not None else None
            ),
            "fetch_route": Route.DATA_API,
        }

    def _videos_list(self, video_id: str) -> dict[str, Any]:
        """One retry without `paidProductPlacementDetails`, and only for the error that names a part.

        Google documents that field as retrievable by the video's owner, and this collector's key is
        not an owner's. If that restriction is enforced as a refused part, the whole `videos.list`
        call fails and every `video.metadata` job in the run fails with it -- for a field three of
        the nine `source_metadata` keys do not even need. So a part-shaped 400/403 drops it once and
        goes on; anything else is raised as it came."""
        parts = ",".join(self._video_parts)
        try:
            return self._get(VIDEOS_PATH, {"part": parts, "id": video_id})
        except TransportError as error:
            reduced = [part for part in self._video_parts if part != "paidProductPlacementDetails"]
            if len(reduced) == len(self._video_parts) or not _is_part_refusal(error):
                raise
            self._video_parts = reduced
            return self._get(VIDEOS_PATH, {"part": ",".join(reduced), "id": video_id})

    # -- one request ---------------------------------------------------------------------------------

    def _get(self, path: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self._budget.charge(Route.DATA_API)
        try:
            response = self._client.get(path, params={**params, "key": self._key})
        except httpx.TimeoutException:
            raise RequestFailed(
                f"timed out after {ROUTES[Route.DATA_API]['timeout_s']}s", route=Route.DATA_API
            ) from None
        except httpx.HTTPError as error:
            raise RequestFailed(f"request failed: {type(error).__name__}", route=Route.DATA_API) from None

        status = response.status_code
        body = response.content
        if status < 400:
            parsed = response.json() if body else {}
            if not isinstance(parsed, dict):
                raise RequestFailed(
                    f"HTTP {status} with a JSON body that is not an object", route=Route.DATA_API
                )
            return parsed
        # The path, not the URL: the URL carries `key=`. Nothing in this message is the vendor's
        # prose either -- a Data API error message embeds the request and ends up in a jobs row.
        message = f"HTTP {status} from {path} ({_error_reason(body)})"
        if status in (401, 403):
            raise Blocked(message, route=Route.DATA_API, code=status, body=body)
        if status == 429:
            raise RateLimited(message, route=Route.DATA_API, code=status, body=body)
        if status == 404:
            raise Unavailable(message, route=Route.DATA_API, code=status, body=body)
        raise RequestFailed(message, route=Route.DATA_API, code=status, body=body)


def _before(timestamp: int | None, cutoff: date) -> bool:
    """An entry with no publication instant never ends the walk: "we do not know when" is not
    "older than the window", and stopping on it would cut the listing at the first odd row."""
    if timestamp is None:
        return False
    return datetime.fromtimestamp(timestamp, tz=UTC).date() < cutoff


_REASON = re.compile(r"[^0-9A-Za-z_-]")


def _error_reason(body: bytes) -> str:
    """The vendor's machine-readable `reason` alone (`quotaExceeded`, `accessNotConfigured`). The
    `message` beside it repeats the request and would carry the key into a jobs row."""
    import json

    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        return "?"
    reasons = [
        item.get("reason")
        for item in ((parsed.get("error") or {}).get("errors") or [])
        if isinstance(item, Mapping)
    ]
    first = next((reason for reason in reasons if isinstance(reason, str) and reason), None)
    return _REASON.sub("", first)[:32] if first else "?"


_PART_REFUSAL_REASONS = ("invalidpart", "forbidden", "insufficientpermissions", "badrequest")


def _is_part_refusal(error: TransportError) -> bool:
    if error.code not in (400, 403):
        return False
    return _error_reason(error.read()).lower() in _PART_REFUSAL_REASONS


# --- yt-dlp --------------------------------------------------------------------------------------

# `extract_flat` is what makes a listing one request rather than one per video.
FLAT_OPTIONS: dict[str, Any] = {"extract_flat": "in_playlist"}


class LibraryYtdlpRuntime:
    """The one place yt-dlp is called. Reaches the network.

    Ported from `service/yt-scrapper/src/tubedepth/sources/ytdlp_runtime.py`, minus the egress and
    cookie-jar layers (no proxy here, and a cookie jar is the resilience ladder this issue leaves
    out). Its rule is kept: no other module builds a `YoutubeDL`, because yt-dlp is the single most
    likely thing here to break when YouTube changes and the fix is almost always one extractor
    argument -- so there should be exactly one file to edit, not one per kind.

    The import is inside the call rather than at module scope: importing yt_dlp pulls in its whole
    extractor tree, and every fixture-backed test in this package replaces this class outright."""

    BASE_OPTIONS: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        # Copied from the archive: politeness is a default, not an afterthought.
        "sleep_requests": float(ROUTES[Route.YTDLP]["sleep_seconds"]),
        "retries": 3,
        "socket_timeout": float(ROUTES[Route.YTDLP]["timeout_s"]),
        "http_headers": {"User-Agent": USER_AGENT},
    }

    def extract(self, target: str, *, options: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError

        merged = self.BASE_OPTIONS | dict(options or {})
        try:
            with YoutubeDL(merged) as downloader:  # type: ignore[arg-type]
                info = downloader.extract_info(target, download=False)
        except DownloadError as error:
            # Translated here so no caller has to know yt-dlp's error shape, and so the bot check
            # reaches `cli._classify_error` as something it recognises.
            raise ytdlp_error(str(error), target=target) from error
        if info is None:
            raise RequestFailed(f"yt-dlp returned nothing for: {target}", route=Route.YTDLP)
        # sanitize_info drops the private keys and non-serializable objects extract_info leaves
        # behind. Without it the dict looks fine right up until json.dumps.
        sanitized = YoutubeDL.sanitize_info(info)
        if sanitized is None:
            raise RequestFailed(f"yt-dlp returned nothing usable for: {target}", route=Route.YTDLP)
        return sanitized


class YtdlpRoute:
    """Comments, and the yt-dlp half of the two `video.metadata` routes."""

    def __init__(self, *, runtime: Any = None, budget: RequestBudget | None = None) -> None:
        self._runtime = runtime if runtime is not None else LibraryYtdlpRuntime()
        self._budget = budget if budget is not None else RequestBudget.from_scope()

    def extract(self, target: str, options: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        # One extraction is one charge even though yt-dlp pages inside it: the budget here bounds how
        # many videos a run touches, which is the number that matters to the address.
        self._budget.charge(Route.YTDLP)
        return self._runtime.extract(target, options=dict(options or {}))

    def comments(self, target: str, *, limit: int = MAX_COMMENTS_PER_VIDEO) -> dict[str, Any]:
        """`limit` is the real control, not an optimisation: yt-dlp fetches roughly one request per
        twenty comments, so a thousand-comment video is fifty-odd requests and minutes of wall clock.

        A harvest that comes back at exactly `limit` is marked truncated. Nothing else in the dump
        distinguishes a thread that ended from one that was cut off, and a truncated harvest passed
        off as whole is a comment count that is really a measure of our own cap."""
        dump = self.extract(
            _watch_url(target),
            {
                "getcomments": True,
                "extractor_args": {
                    "youtube": {"comment_sort": ["top"], "max_comments": [str(limit), "all", "all", "8"]}
                },
            },
        )
        harvested = dump.get("comments") or []
        return {
            **dump,
            "comments": list(harvested),
            "fetch_route": Route.YTDLP,
            "requested_limit": limit,
            "returned_count": len(harvested),
            "truncated": len(harvested) >= limit,
        }

    def video_metadata(self, target: str) -> dict[str, Any]:
        dump = self.extract(_watch_url(target))
        return {
            **dump,
            "fetch_route": Route.YTDLP,
            # yt-dlp reports no paid-placement flag of any kind, and `caption_available` here would
            # be a different measurement (whether yt-dlp found a track) than the Data API's. Left
            # absent so `flatten` writes JSON null and the gap is queryable rather than a false "no".
            "has_paid_product_placement": None,
            "caption_available": None,
            "category_id": None,
        }

    def listing(self, kind: str, target: str, *, max_items: int | None = None) -> dict[str, Any]:
        """The videos tab. Not a route any watchlist directive takes -- it is the comparison arm of
        Work 6, which is what decides whether the Data API listing was the right call."""
        cap = MAX_LISTING_ITEMS if max_items is None else max_items
        dump = self.extract(_listing_url(kind, target, cap), {**FLAT_OPTIONS, "playlistend": cap})
        entries = list(dump.get("entries") or [])
        return {
            **dump,
            "entries": entries,
            "fetch_route": Route.YTDLP,
            "expected_count": _as_int(dump.get("playlist_count")),
            "returned_count": len(entries),
            "truncated": len(entries) >= cap,
        }


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _listing_url(kind: str, target: str, limit: int) -> str:
    if kind == "channel.videos":
        path = target if target.startswith("@") else f"channel/{target}"
        return f"https://www.youtube.com/{path}/videos"
    if kind == "playlist.items":
        return f"https://www.youtube.com/playlist?list={target}"
    if kind == "search.videos":
        return f"ytsearch{limit}:{target}"
    raise ValueError(f"yt-dlp has no listing target for job kind {kind!r}")


# --- timedtext -------------------------------------------------------------------------------------

#: json3 specifically: the only format YouTube offers that carries the per-segment timings as data
#: rather than as text needing a second parser (`sources.parse_json3_transcript` reads it).
CAPTION_FORMAT = "json3"
#: yt-dlp appends this to the automatic-caption key for the language the transcription was actually
#: made in; every other key in that bucket is a translation of it.
ORIGINAL_SUFFIX = "-orig"
#: The query parameter marking an auto-translated track rather than a transcription.
TRANSLATION_MARKER = "tlang="
#: yt-dlp files live chat replay under `subtitles`. It is not a caption track.
LIVE_CHAT_KEY = "live_chat"
#: Used only when the video itself says nothing about what language it is in.
FALLBACK_LANGUAGES = ("ko", "en")


@dataclass(frozen=True, slots=True)
class CaptionTrack:
    language: str
    is_automatic: bool
    url: str


def video_language(dump: Mapping[str, Any]) -> str | None:
    """What yt-dlp reports, or the `-orig` key YouTube's own transcriber ran in. Both are absent
    together on old uploads, which is a real state rather than a parsing failure."""
    reported = dump.get("language")
    if isinstance(reported, str) and reported:
        return reported
    for key in dump.get("automatic_captions") or {}:
        if key.endswith(ORIGINAL_SUFFIX):
            return key.removesuffix(ORIGINAL_SUFFIX)
    return None


def _json3_track(tracks: Iterable[Mapping[str, Any]] | None) -> Mapping[str, Any] | None:
    for track in tracks or []:
        if track.get("ext") == CAPTION_FORMAT and track.get("url"):
            return track
    return None


def _track(bucket: Mapping[str, Any], key: str, *, is_automatic: bool) -> CaptionTrack | None:
    found = _json3_track(bucket.get(key))
    if found is None:
        return None
    if TRANSLATION_MARKER in found["url"]:
        # A `tlang=` track is this video's words run through a translator. Serving one makes a
        # transcript's language a fact about how recently we ran rather than about the video.
        return None
    return CaptionTrack(
        language=key.removesuffix(ORIGINAL_SUFFIX), is_automatic=is_automatic, url=found["url"]
    )


def _matching_keys(bucket: Mapping[str, Any], language: str) -> list[str]:
    """Keys for `language`, exact first, then regional variants. yt-dlp reports `pt` where YouTube
    lists the track as `pt-BR`; comparing the primary subtag is what keeps those from looking like
    two languages."""
    primary = language.partition("-")[0]
    exact = [key for key in bucket if key == language]
    regional = [
        key
        for key in bucket
        if key not in exact and not key.endswith(ORIGINAL_SUFFIX) and key.partition("-")[0] == primary
    ]
    return exact + sorted(regional)


def caption_track_candidates(
    dump: Mapping[str, Any], *, fallback_languages: Sequence[str] = FALLBACK_LANGUAGES
) -> list[CaptionTrack]:
    """Every track worth trying, best first: captions a person wrote in the video's own language,
    then YouTube's transcription of it. Translations never appear at either tier.

    A list rather than one track because the ranking is a preference and a fetch can still be
    refused -- a manual track can 403 while the ASR track of the same video serves."""
    manual = dump.get("subtitles") or {}
    automatic = dump.get("automatic_captions") or {}
    language = video_language(dump)
    languages = [language] if language is not None else list(fallback_languages)

    candidates: list[CaptionTrack] = []
    for candidate in languages:
        for key in _matching_keys(manual, candidate):
            if key == LIVE_CHAT_KEY:
                continue
            track = _track(manual, key, is_automatic=False)
            if track is not None:
                candidates.append(track)
        for key in (candidate + ORIGINAL_SUFFIX, *_matching_keys(automatic, candidate)):
            track = _track(automatic, key, is_automatic=True)
            if track is not None:
                candidates.append(track)
    return candidates


class TimedtextRoute:
    """Two steps in one job, and they must stay in one job: yt-dlp discovers the track URLs, and
    those URLs are signed and short-lived. Storing one to fetch later guarantees a 403 later."""

    def __init__(
        self,
        *,
        ytdlp: YtdlpRoute | None = None,
        transport: httpx.BaseTransport | None = None,
        budget: RequestBudget | None = None,
        fallback_languages: Sequence[str] = FALLBACK_LANGUAGES,
    ) -> None:
        self._ytdlp = ytdlp if ytdlp is not None else YtdlpRoute(budget=budget)
        self._budget = budget if budget is not None else RequestBudget.from_scope()
        self._fallback_languages = tuple(fallback_languages)
        self._client = httpx.Client(
            timeout=float(ROUTES[Route.TIMEDTEXT]["timeout_s"]),
            transport=transport,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def transcript(self, target: str) -> dict[str, Any]:
        dump = self._ytdlp.extract(_watch_url(target))
        candidates = caption_track_candidates(dump, fallback_languages=self._fallback_languages)
        if not candidates:
            raise Unavailable(_no_track_message(dump, self._fallback_languages), route=Route.TIMEDTEXT)

        last: TransportError | None = None
        for track in candidates:
            try:
                body = self._fetch(track.url)
            except TransportError as error:
                # Walk the ranking rather than committing to its head: returning the video's words
                # in the second tier beats failing on the first.
                last = error
                continue
            return {
                **body,
                "language": track.language,
                "is_automatic": track.is_automatic,
                "fetch_route": Route.TIMEDTEXT,
            }
        assert last is not None  # noqa: S101 - the loop ran at least once and every path sets it
        raise last

    def _fetch(self, url: str) -> dict[str, Any]:
        self._budget.charge(Route.TIMEDTEXT)
        try:
            response = self._client.get(url)
        except httpx.TimeoutException:
            raise RequestFailed(
                f"timed out after {ROUTES[Route.TIMEDTEXT]['timeout_s']}s", route=Route.TIMEDTEXT
            ) from None
        except httpx.HTTPError as error:
            raise RequestFailed(f"request failed: {type(error).__name__}", route=Route.TIMEDTEXT) from None
        status = response.status_code
        if status >= 400:
            # The caption URL is signed; it never reaches a message.
            message = f"HTTP {status} for a caption track"
            if status in (401, 403):
                raise Blocked(message, route=Route.TIMEDTEXT, code=status)
            if status == 429:
                raise RateLimited(message, route=Route.TIMEDTEXT, code=status)
            raise RequestFailed(message, route=Route.TIMEDTEXT, code=status)
        try:
            body = response.json()
        except ValueError:
            raise RequestFailed("a caption body that is not json3", route=Route.TIMEDTEXT) from None
        if not isinstance(body, dict):
            raise RequestFailed("a caption body that is not a json3 object", route=Route.TIMEDTEXT)
        return body


def _no_track_message(dump: Mapping[str, Any], fallback_languages: Sequence[str]) -> str:
    """Say which of the three failures this is; they are acted on differently. Captions turned off is
    nothing to do. A video whose own language we cannot serve is a fact about that video. Neither is
    a reason to revisit the configured fallback, and one message for all three invites exactly that."""
    keys = {*(dump.get("subtitles") or {}), *(dump.get("automatic_captions") or {})} - {LIVE_CHAT_KEY}
    if not keys:
        return "the video has no caption tracks at all"
    language = video_language(dump)
    if language is not None:
        return f"no caption track in the video's own language: {language}"
    return f"the video reports no language and has no caption track in {', '.join(fallback_languages)}"


# --- the fetcher the CLI plugs in ------------------------------------------------------------------

LISTING_KINDS = frozenset({"channel.videos", "search.videos", "playlist.items", "trending.videos"})


class LiveFetcher:
    """One per run -- the budget it holds counts that run's requests, so a shared instance would let
    one run spend another's."""

    def __init__(
        self,
        *,
        data_api: DataApiClient | None = None,
        ytdlp: YtdlpRoute | None = None,
        timedtext: TimedtextRoute | None = None,
        budget: RequestBudget | None = None,
        metadata_route: str = VIDEO_METADATA_ROUTE,
    ) -> None:
        self._budget = budget if budget is not None else RequestBudget.from_scope()
        self._data_api = data_api
        self._ytdlp = ytdlp if ytdlp is not None else YtdlpRoute(budget=self._budget)
        self._timedtext = (
            timedtext if timedtext is not None else TimedtextRoute(ytdlp=self._ytdlp, budget=self._budget)
        )
        self._metadata_route = Route(metadata_route)

    def close(self) -> None:
        if self._data_api is not None:
            self._data_api.close()
        self._timedtext.close()

    def _api(self) -> DataApiClient:
        if self._data_api is None:
            raise Blocked(
                "this route needs the YouTube Data API key and none was loaded "
                "(contracts/secrets.md names it)",
                route=Route.DATA_API,
            )
        return self._data_api

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        if spec.kind in LISTING_KINDS:
            return self._api().listing(spec.kind, spec.target)
        if spec.kind == "video.comments":
            return self._ytdlp.comments(spec.target)
        if spec.kind == "video.transcript":
            return self._timedtext.transcript(spec.target)
        if spec.kind == "video.metadata":
            if self._metadata_route is Route.DATA_API:
                return self._api().video_metadata(spec.target)
            return self._ytdlp.video_metadata(spec.target)
        raise ValueError(f"no route for job kind {spec.kind!r}")


__all__ = [
    "DATA_API_HOST",
    "CHANNELS_PATH",
    "PLAYLIST_ITEMS_PATH",
    "VIDEOS_PATH",
    "SEARCH_PATH",
    "PAGE_SIZE",
    "USER_AGENT",
    "Route",
    "TransportError",
    "Blocked",
    "RateLimited",
    "Unavailable",
    "BudgetExhausted",
    "RequestFailed",
    "ytdlp_error",
    "RequestBudget",
    "CaptionTrack",
    "caption_track_candidates",
    "video_language",
    "parse_iso_duration",
    "DataApiClient",
    "LibraryYtdlpRuntime",
    "YtdlpRoute",
    "TimedtextRoute",
    "LiveFetcher",
]
