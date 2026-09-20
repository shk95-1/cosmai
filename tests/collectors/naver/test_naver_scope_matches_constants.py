"""scope.json is `collectors/naver/scope.py`'s declarative copy -- proves the two cannot drift
(same form as tests/collectors/commerce/test_scope_matches_constants.py, #7)."""

from __future__ import annotations

import json
from pathlib import Path

from collectors.naver import scope

SCOPE_JSON = Path(__file__).resolve().parents[3] / "collectors" / "naver" / "scope.json"


def test_scope_json_matches_the_module_constants():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    assert on_disk["datalab"]["window_start"] == scope.DATALAB_WINDOW_START
    assert on_disk["datalab"]["time_unit"] == scope.DATALAB_TIME_UNIT
    assert on_disk["datalab"]["max_groups_per_request"] == scope.DATALAB_MAX_GROUPS_PER_REQUEST
    assert on_disk["datalab"]["anchor"] == scope.DATALAB_ANCHOR
    assert tuple(on_disk["datalab"]["anchor_terms"]) == scope.DATALAB_ANCHOR_TERMS
    assert on_disk["datalab"]["category_groups_per_request"] == scope.DATALAB_CATEGORY_GROUPS_PER_REQUEST
    assert on_disk["blog"]["display"] == scope.BLOG_DISPLAY
    assert on_disk["blog"]["pages_max"] == scope.BLOG_PAGES_MAX
    assert on_disk["blog"]["sort"] == scope.BLOG_SORT
    assert on_disk["http"]["timeout_s"] == scope.REQUEST_TIMEOUT_S
    assert on_disk["http"]["retry_max_attempts"] == scope.RETRY_MAX_ATTEMPTS
    assert on_disk["http"]["retry_backoff_s"] == scope.RETRY_BACKOFF_S
    assert on_disk["http"]["max_requests_per_run"] == scope.MAX_REQUESTS_PER_RUN
    launch = on_disk["launch_onset"]
    assert launch["search_window_start"] == scope.DATALAB_WINDOW_START
    assert launch["shopping_window_start"] == scope.SHOPPING_WINDOW_START
    assert launch["shopping_category"] == scope.SHOPPING_CATEGORY
    assert launch["shopping_max_keywords_per_request"] == scope.SHOPPING_MAX_KEYWORDS_PER_REQUEST
    assert launch["search_groups_per_request"] == scope.LAUNCH_SEARCH_GROUPS_PER_REQUEST
    assert launch["peak_fraction"] == scope.ONSET_PEAK_FRACTION
    assert launch["min_quiet_months"] == scope.ONSET_MIN_QUIET_MONTHS
    assert launch["min_active_months"] == scope.ONSET_MIN_ACTIVE_MONTHS
    assert launch["max_terms_per_product"] == scope.LAUNCH_MAX_TERMS_PER_PRODUCT
    assert launch["generic_min_brands"] == scope.LAUNCH_GENERIC_MIN_BRANDS
    assert launch["requests_per_product"] == scope.LAUNCH_REQUESTS_PER_PRODUCT
    assert launch["max_requests_per_run"] == scope.LAUNCH_MAX_REQUESTS_PER_RUN


def test_scope_json_has_no_stray_top_level_keys():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    assert set(on_disk) - {"_comment"} == {"datalab", "launch_onset", "blog", "http"}


def test_the_launch_onset_budget_covers_a_pass_over_the_whole_catalogue():
    # The ceiling is a guard on one run, not a reservation: it has to be above what two requests a
    # product cost over a catalogue bigger than today's 248 refs, and far below the vendor's month.
    assert scope.LAUNCH_MAX_REQUESTS_PER_RUN > 248 * scope.LAUNCH_REQUESTS_PER_PRODUCT
    assert scope.LAUNCH_MAX_REQUESTS_PER_RUN < scope.DATALAB_MONTHLY_QUOTA
    # One group per search request is what makes an anchor unnecessary, so it is checked as a value
    # and not only read: a second group would put the product's series on another group's scale.
    assert scope.LAUNCH_SEARCH_GROUPS_PER_REQUEST == 1
    assert scope.LAUNCH_MAX_TERMS_PER_PRODUCT <= scope.SHOPPING_MAX_KEYWORDS_PER_REQUEST


def test_the_anchor_leaves_room_for_the_category_groups_beside_it():
    # The anchor occupies one of the vendor's five group slots (#90), so a request that packed
    # max_groups_per_request category groups would be refused with the anchor added.
    assert scope.DATALAB_CATEGORY_GROUPS_PER_REQUEST == scope.DATALAB_MAX_GROUPS_PER_REQUEST - 1
    assert scope.DATALAB_ANCHOR


def test_the_anchor_label_and_the_terms_it_searches_are_two_different_things():
    """#250: the group name is only a label the vendor echoes back as `results[].title`; the
    keywords are what is searched. Sending the label as its own keyword searched a token with no
    volume, the anchor series came back empty, and every rescaled row was NULL."""
    terms = tuple(scope.DATALAB_ANCHOR_TERMS)
    assert terms, "the anchor group must search something"
    assert all(isinstance(t, str) and t for t in terms)
    assert scope.DATALAB_ANCHOR not in terms
