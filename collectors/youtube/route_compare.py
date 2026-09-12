"""Work 6 of #183: list one channel over both routes and say how the two answers differ.

The whole listing decision rests on a claim this repository has not measured: that the yt-dlp videos
tab excludes Shorts and past live while the Data API uploads playlist does not. The evidence behind
it today is Google's documentation and the archived RUNBOOK's 697-vs-474, which is somebody else's
number on somebody else's channel. This module is what replaces it with ours.

It costs one extra listing call. `tool/measure-youtube-listing-routes` is the live half -- it builds
both routes, runs them against one channel and prints this report. Everything that decides anything
is here, pure, and tested against saved fixtures, so the offline suite covers the arithmetic and the
live run supplies only the two dumps.

What the report is read for: if the two sets are the same, the cheaper route wins and
`YOUTUBE_DATA_API_TOKEN` goes back to being "trending only"; if the uploads playlist is a strict
superset, the decision stands and the size of the difference is the size of the axis a videos-tab
listing would have lost.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from collectors.youtube import sources
from collectors.youtube.transport import Route


class IncomparableWindow(ValueError):
    """A publication window was asked for that one arm cannot honour.

    The Data API walks the uploads playlist by publication date; the yt-dlp videos tab is walked by
    position and its flat entries often carry no publication instant at all. Windowing the arm that
    can and not the arm that cannot is not a comparison -- every pre-window upload lands in
    `only_ytdlp` and the report reads as a difference between the routes when it is a difference
    between the two invocations. So the window is applied to both or the measurement is refused.
    """


@dataclass(frozen=True, slots=True)
class RouteComparison:
    """Counts and set differences. Ordered lists rather than sets so the printed report is stable
    between runs and two reports can be diffed against each other."""

    channel: str
    data_api_count: int
    ytdlp_count: int
    only_data_api: tuple[str, ...]
    only_ytdlp: tuple[str, ...]
    shared: tuple[str, ...]
    data_api_truncated: bool
    ytdlp_truncated: bool

    @property
    def same_set(self) -> bool:
        return not self.only_data_api and not self.only_ytdlp


def _video_ids(dump: Mapping[str, Any], *, kind: str) -> list[str]:
    """Through `normalize_listing`, not off the raw dump: it is the function that drops yt-dlp's
    placeholder entries for deleted and private videos, and counting those on one side and not the
    other would put a difference in the report that is the parser's rather than the source's."""
    listing = sources.normalize_listing(dump, source_kind=kind)
    return [video["video_id"] for video in listing["videos"]]


def _windowed(dump: Mapping[str, Any], *, kind: str, since: str | None) -> list[str]:
    """The ids of one arm, narrowed to the window if there is one.

    Refuses rather than guesses on an entry with no publication instant: keeping it would put a
    pre-window upload on one side of the difference, dropping it would take a real video off the
    other, and both read as a difference between the routes.
    """
    listing = sources.normalize_listing(dump, source_kind=kind)
    if since is None:
        return [video["video_id"] for video in listing["videos"]]
    cutoff = date.fromisoformat(since)
    undated = [video["video_id"] for video in listing["videos"] if not _instant_of(video)]
    if undated:
        raise IncomparableWindow(
            f"{len(undated)} entry/entries carry no publication instant, so the window "
            f"{since} cannot be applied to this arm (first: {undated[0]})"
        )
    return [
        video["video_id"]
        for video in listing["videos"]
        if (_instant_of(video) or datetime.min.replace(tzinfo=UTC)).date() >= cutoff
    ]


def _instant_of(video: Mapping[str, Any]) -> datetime | None:
    published = video.get("published_at")
    return published if isinstance(published, datetime) else None


def compare(
    channel: str,
    data_api_dump: Mapping[str, Any],
    ytdlp_dump: Mapping[str, Any],
    *,
    kind: str = "channel.videos",
    since: str | None = None,
) -> RouteComparison:
    """`since` narrows **both** arms or neither -- see `IncomparableWindow`."""
    api_ids = _windowed(data_api_dump, kind=kind, since=since)
    tab_ids = _windowed(ytdlp_dump, kind=kind, since=since)
    api_set, tab_set = set(api_ids), set(tab_ids)
    return RouteComparison(
        channel=channel,
        data_api_count=len(api_ids),
        ytdlp_count=len(tab_ids),
        only_data_api=tuple(video_id for video_id in api_ids if video_id not in tab_set),
        only_ytdlp=tuple(video_id for video_id in tab_ids if video_id not in api_set),
        shared=tuple(video_id for video_id in api_ids if video_id in tab_set),
        # A truncated arm makes every difference unreadable -- an id missing from a walk that stopped
        # early says nothing about the route -- so the report has to carry the flag, not just the
        # counts.
        data_api_truncated=bool(data_api_dump.get("truncated")),
        ytdlp_truncated=bool(ytdlp_dump.get("truncated")),
    )


def render(report: RouteComparison, *, sample: int = 10) -> str:
    """The comment body that goes on the issue. Ids are sampled rather than dumped whole: a panel
    channel has hundreds and the reader wants the counts and enough ids to spot-check a few."""
    lines = [
        f"channel: {report.channel}",
        f"{Route.DATA_API} (uploads playlist): {report.data_api_count}"
        + ("  [TRUNCATED]" if report.data_api_truncated else ""),
        f"{Route.YTDLP} (videos tab): {report.ytdlp_count}"
        + ("  [TRUNCATED]" if report.ytdlp_truncated else ""),
        f"in both: {len(report.shared)}",
        f"only {Route.DATA_API}: {len(report.only_data_api)}",
        f"only {Route.YTDLP}: {len(report.only_ytdlp)}",
    ]
    if report.data_api_truncated or report.ytdlp_truncated:
        lines.append("one arm stopped early, so the difference below is not a difference between the routes")
    for label, ids in ((Route.DATA_API, report.only_data_api), (Route.YTDLP, report.only_ytdlp)):
        if ids:
            shown = ", ".join(ids[:sample])
            more = f" (+{len(ids) - sample} more)" if len(ids) > sample else ""
            lines.append(f"  only {label}: {shown}{more}")
    lines.append(
        "the sets are identical -- the cheaper route wins"
        if report.same_set
        else "the sets differ -- the routes are not interchangeable for this channel"
    )
    return "\n".join(lines)


def as_rows(report: RouteComparison) -> Sequence[tuple[str, Any]]:
    """The same numbers as pairs, for anything that wants to tabulate rather than print."""
    return (
        ("channel", report.channel),
        ("data_api_count", report.data_api_count),
        ("ytdlp_count", report.ytdlp_count),
        ("shared", len(report.shared)),
        ("only_data_api", len(report.only_data_api)),
        ("only_ytdlp", len(report.only_ytdlp)),
        ("data_api_truncated", report.data_api_truncated),
        ("ytdlp_truncated", report.ytdlp_truncated),
    )


__all__ = ["RouteComparison", "compare", "render", "as_rows"]
