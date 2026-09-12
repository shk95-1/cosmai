"""collectors/youtube/scope.json is the one place the fan-out caps and their rationale are written down
(issue #8 §산출물 2); models.py and queue.py must read the same numbers, not a hardcoded copy -- same
guard collectors/commerce's test_scope_matches_constants.py gives the ranking/review constants."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from collectors.youtube import models, queue

SCOPE_JSON = Path(__file__).resolve().parents[3] / "collectors" / "youtube" / "scope.json"


def test_scope_json_has_both_caps_as_positive_ints():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    for key in ("MAX_FOLLOWUPS_PER_VIDEO", "MAX_QUEUE_DEPTH"):
        value = on_disk[key]
        assert isinstance(value, int) and not isinstance(value, bool) and value > 0


def test_models_constants_are_scope_json_verbatim():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    assert models.MAX_FOLLOWUPS_PER_VIDEO == on_disk["MAX_FOLLOWUPS_PER_VIDEO"]
    assert models.MAX_QUEUE_DEPTH == on_disk["MAX_QUEUE_DEPTH"]


def test_queue_module_uses_the_same_constants_as_models():
    assert queue.MAX_FOLLOWUPS_PER_VIDEO == models.MAX_FOLLOWUPS_PER_VIDEO
    assert queue.MAX_QUEUE_DEPTH == models.MAX_QUEUE_DEPTH


def test_freshness_seconds_are_positive_ints_keyed_by_a_known_kind():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    seconds_by_kind = on_disk["FRESHNESS_SECONDS"]
    assert seconds_by_kind
    for kind, seconds in seconds_by_kind.items():
        assert isinstance(kind, str) and kind
        assert isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0


def test_models_freshness_is_scope_json_verbatim_as_timedeltas():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    assert models.FRESHNESS == {k: timedelta(seconds=v) for k, v in on_disk["FRESHNESS_SECONDS"].items()}


# --- the live transport's constants (#183) --------------------------------------------------------


def test_the_transport_constants_are_scope_json_verbatim():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    assert models.COMMENT_REFETCH_WINDOW_DAYS == on_disk["COMMENT_REFETCH_WINDOW_DAYS"]
    assert models.LISTING_WINDOW_START == on_disk["LISTING_WINDOW_START"]
    assert models.MAX_LISTING_ITEMS == on_disk["MAX_LISTING_ITEMS"]
    assert models.MAX_COMMENTS_PER_VIDEO == on_disk["MAX_COMMENTS_PER_VIDEO"]
    assert models.VIDEO_METADATA_ROUTE == on_disk["VIDEO_METADATA_ROUTE"]
    assert list(models.VIDEO_PARTS) == on_disk["VIDEO_PARTS"]
    assert models.ROUTES == on_disk["ROUTES"]


def test_the_comment_window_is_a_positive_number_of_days_and_says_it_is_provisional():
    """The value 30 is a placeholder chosen to equal PRUNE_MAX_AGE_DAYS, not a measurement -- fork
    shk95/cosmai-import-ydc#38's timed sample is what sets the real one. A reader has to be able to
    see that from the file rather than from an issue comment."""
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    value = on_disk["COMMENT_REFETCH_WINDOW_DAYS"]
    assert isinstance(value, int) and not isinstance(value, bool) and value > 0
    assert "PROVISIONAL" in on_disk["COMMENT_REFETCH_WINDOW_DAYS_note"]


def test_every_route_declares_a_budget_a_pause_and_a_timeout():
    """A route with no entry would fall through to a budget of zero and refuse its first request --
    a whole source silently dark, which is the failure shape this issue exists to end."""
    from collectors.youtube.transport import Route

    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))["ROUTES"]
    assert set(on_disk) == {route.value for route in Route}
    for route, values in on_disk.items():
        assert values["max_requests_per_run"] > 0, route
        assert values["sleep_seconds"] >= 0, route
        assert values["timeout_s"] > 0, route


def test_the_video_metadata_route_is_one_of_the_two_that_exist():
    from collectors.youtube.transport import Route

    assert models.VIDEO_METADATA_ROUTE in (Route.DATA_API, Route.YTDLP)


def test_the_paid_placement_part_is_asked_for():
    """The one videos.list part with a reader: analysis/sensitivity reads
    source_metadata.has_paid_product_placement as the declared half of ad marking. Dropping it from
    this list costs about 12% of ad videos and says nothing (contracts/interfaces.md)."""
    assert "paidProductPlacementDetails" in models.VIDEO_PARTS
