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
    assert on_disk["blog"]["display"] == scope.BLOG_DISPLAY
    assert on_disk["blog"]["pages_max"] == scope.BLOG_PAGES_MAX
    assert on_disk["blog"]["sort"] == scope.BLOG_SORT


def test_scope_json_has_no_stray_top_level_keys():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    assert set(on_disk) - {"_comment"} == {"datalab", "blog"}
