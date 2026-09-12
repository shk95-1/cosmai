"""Work 6's arithmetic (#183): what the two-route listing comparison counts, offline.

The live half is `tool/measure-youtube-listing-routes` and it is the coordinator's to run. What can
be settled here is everything that decides anything: which ids each arm holds, which side of the
difference they land on, and that a truncated arm is reported rather than counted -- an id missing
from a walk that stopped early says nothing about the route, and a report that did not say so would
read as evidence.

The two dumps are the saved fixture shapes of the two routes, with a Shorts entry present on the
uploads-playlist side and absent from the videos-tab side. That is the claim the live run is going
to test; here it only proves that the comparison would see it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from collectors.youtube import route_compare
from collectors.youtube.transport import Route

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SHORT = "aB3dEf7GhJk"
LONG = "dQw4w9WgXcQ"
OLDER = "9bZkp7q19f0"


def _api_dump(*video_ids: str, truncated: bool = False) -> dict[str, Any]:
    """The uploads-playlist walk's own output shape, as `DataApiClient.listing` returns it."""
    return {
        "id": "UU5oM4Ai05dQqiVL6rypAo_A",
        "entries": [
            {"id": video_id, "title": video_id, "channel_id": "UC5oM4Ai05dQqiVL6rypAo_A"}
            for video_id in video_ids
        ],
        "fetch_route": Route.DATA_API,
        "expected_count": len(video_ids),
        "returned_count": len(video_ids),
        "truncated": truncated,
    }


def _tab_dump(*video_ids: str, truncated: bool = False) -> dict[str, Any]:
    dump = json.loads((FIXTURES / "listing" / "channel-videos.json").read_text(encoding="utf-8"))
    dump["entries"] = [
        {"id": video_id, "title": video_id, "channel_id": "UUsome_channel_id"} for video_id in video_ids
    ]
    # The archive's placeholder for a deleted or private video. It is on this side only, and the
    # comparison must not count it as a difference between routes -- it is one the parser drops.
    dump["entries"].append({"id": "private", "title": None})
    dump["fetch_route"] = Route.YTDLP
    dump["returned_count"] = len(video_ids)
    dump["truncated"] = truncated
    return dump


def test_a_video_only_the_uploads_playlist_holds_lands_on_that_side():
    report = route_compare.compare("UC…", _api_dump(SHORT, LONG, OLDER), _tab_dump(LONG, OLDER))
    assert report.data_api_count == 3
    assert report.ytdlp_count == 2
    assert report.only_data_api == (SHORT,)
    assert report.only_ytdlp == ()
    assert report.shared == (LONG, OLDER)
    assert not report.same_set


def test_identical_sets_are_reported_as_identical_whatever_the_order():
    """The flip condition of the listing decision: if the routes return the same set, the cheaper
    one wins and `YOUTUBE_DATA_API_TOKEN` goes back to being 'trending only'."""
    report = route_compare.compare("UC…", _api_dump(LONG, OLDER), _tab_dump(OLDER, LONG))
    assert report.same_set
    assert "identical" in route_compare.render(report)


def test_a_placeholder_entry_is_not_a_difference_between_the_routes():
    """Both arms go through `sources.normalize_listing`, which is where a deleted or private
    placeholder is dropped. Counting it on one side would put the parser's behaviour into a number
    that is read as the source's."""
    report = route_compare.compare("UC…", _api_dump(LONG), _tab_dump(LONG))
    assert report.ytdlp_count == 1
    assert report.only_ytdlp == ()


def test_a_truncated_arm_is_said_out_loud():
    report = route_compare.compare("UC…", _api_dump(SHORT, LONG, truncated=True), _tab_dump(LONG))
    assert report.data_api_truncated
    rendered = route_compare.render(report)
    assert "TRUNCATED" in rendered
    assert "not a difference between the routes" in rendered


def test_the_rendered_report_carries_both_counts_and_a_sample_of_the_difference():
    report = route_compare.compare("UCpanel", _api_dump(SHORT, LONG), _tab_dump(LONG))
    rendered = route_compare.render(report)
    assert "UCpanel" in rendered
    assert f"{Route.DATA_API} (uploads playlist): 2" in rendered
    assert f"{Route.YTDLP} (videos tab): 1" in rendered
    assert SHORT in rendered


def test_a_long_difference_is_sampled_rather_than_dumped_whole():
    many = tuple(f"vid{index:08d}" for index in range(40))
    report = route_compare.compare("UC…", _api_dump(*many), _tab_dump())
    rendered = route_compare.render(report, sample=5)
    assert "+35 more" in rendered
    assert many[39] not in rendered


def test_as_rows_gives_the_same_numbers_the_report_prints():
    report = route_compare.compare("UC…", _api_dump(SHORT, LONG), _tab_dump(LONG))
    rows = dict(route_compare.as_rows(report))
    assert rows["data_api_count"] == 2
    assert rows["ytdlp_count"] == 1
    assert rows["only_data_api"] == 1
    assert rows["shared"] == 1


# --- the window must narrow both arms or neither (#183 fix round) ---------------------------------

from datetime import UTC, datetime  # noqa: E402 - the window tests are what need it

import pytest  # noqa: E402

from collectors.youtube.route_compare import IncomparableWindow  # noqa: E402


def _dated(video_id: str, when: datetime) -> dict[str, Any]:
    return {
        "id": video_id,
        "title": video_id,
        "channel_id": "UC5oM4Ai05dQqiVL6rypAo_A",
        "timestamp": int(when.timestamp()),
    }


def _dated_dump(*entries: dict[str, Any], route: str = Route.DATA_API) -> dict[str, Any]:
    return {"id": "UU…", "entries": list(entries), "fetch_route": route, "truncated": False}


IN_WINDOW = _dated(LONG, datetime(2026, 3, 1, tzinfo=UTC))
BEFORE_WINDOW = _dated(OLDER, datetime(2023, 3, 1, tzinfo=UTC))


def test_a_window_narrows_both_arms_and_not_just_the_one_that_can_be_walked_by_date():
    """The defect this replaced: the Data API arm was walked from LISTING_WINDOW_START while the
    yt-dlp videos tab, which is walked by position, got no window at all -- so every pre-window
    upload landed in `only_ytdlp` and the report read as a difference between the routes when it was
    a difference between the two invocations."""
    both = _dated_dump(IN_WINDOW, BEFORE_WINDOW)
    report = route_compare.compare("UC…", both, dict(both, fetch_route=Route.YTDLP), since="2024-07-01")

    assert report.data_api_count == 1
    assert report.ytdlp_count == 1
    assert report.same_set
    assert report.only_ytdlp == ()


def test_no_window_is_the_default_and_leaves_both_arms_whole():
    both = _dated_dump(IN_WINDOW, BEFORE_WINDOW)
    report = route_compare.compare("UC…", both, dict(both, fetch_route=Route.YTDLP))
    assert report.data_api_count == 2
    assert report.ytdlp_count == 2


def test_a_window_is_refused_when_an_arm_carries_an_entry_with_no_publication_instant():
    """yt-dlp's flat entries often have no timestamp. Keeping such an entry puts a possibly
    pre-window upload on one side of the difference and dropping it takes a real video off the
    other; both read as a difference between the routes, so the measurement is refused instead."""
    undated = {"id": SHORT, "title": SHORT, "channel_id": "UC…"}
    with pytest.raises(IncomparableWindow, match="no publication instant"):
        route_compare.compare(
            "UC…",
            _dated_dump(IN_WINDOW),
            _dated_dump(IN_WINDOW, undated, route=Route.YTDLP),
            since="2024-07-01",
        )


def test_an_undated_entry_is_fine_when_no_window_was_asked_for():
    undated = {"id": SHORT, "title": SHORT, "channel_id": "UC…"}
    report = route_compare.compare(
        "UC…", _dated_dump(IN_WINDOW), _dated_dump(IN_WINDOW, undated, route=Route.YTDLP)
    )
    assert report.only_ytdlp == (SHORT,)
