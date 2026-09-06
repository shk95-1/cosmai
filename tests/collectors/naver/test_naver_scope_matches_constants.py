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
    assert on_disk["datalab"]["category_groups_per_request"] == scope.DATALAB_CATEGORY_GROUPS_PER_REQUEST
    assert on_disk["blog"]["display"] == scope.BLOG_DISPLAY
    assert on_disk["blog"]["pages_max"] == scope.BLOG_PAGES_MAX
    assert on_disk["blog"]["sort"] == scope.BLOG_SORT
    assert on_disk["http"]["timeout_s"] == scope.REQUEST_TIMEOUT_S
    assert on_disk["http"]["retry_max_attempts"] == scope.RETRY_MAX_ATTEMPTS
    assert on_disk["http"]["retry_backoff_s"] == scope.RETRY_BACKOFF_S
    assert on_disk["http"]["max_requests_per_run"] == scope.MAX_REQUESTS_PER_RUN


def test_scope_json_has_no_stray_top_level_keys():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    assert set(on_disk) - {"_comment"} == {"datalab", "blog", "http"}


def test_the_anchor_leaves_room_for_the_category_groups_beside_it():
    # The anchor occupies one of the vendor's five group slots (#90), so a request that packed
    # max_groups_per_request category groups would be refused with the anchor added.
    assert scope.DATALAB_CATEGORY_GROUPS_PER_REQUEST == scope.DATALAB_MAX_GROUPS_PER_REQUEST - 1
    assert scope.DATALAB_ANCHOR
