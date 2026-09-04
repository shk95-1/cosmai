"""Sample-design constants `cli.py` actually runs on. `scope.json` is this module's declarative
copy (contracts/entrypoints.md: 'a variant of scope.lock'), and
`tests/collectors/naver/test_scope_matches_constants.py` is the drift check -- the same split
`collectors/commerce/models.py` constants vs. `collectors/commerce/scope.json` uses (#7's form)."""

from __future__ import annotations

DATALAB_WINDOW_START = "2016-01-01"
DATALAB_TIME_UNIT = "month"
DATALAB_MAX_GROUPS_PER_REQUEST = 5

BLOG_DISPLAY = 100
BLOG_PAGES_MAX = 3
BLOG_SORT = "date"
#: `[확인 사실]` vendor-documented hard ceiling on `start`, independent of `total` -- not a scope.json
#: knob, since no configuration of ours can move it.
BLOG_START_MAX = 1000

__all__ = [
    "DATALAB_WINDOW_START",
    "DATALAB_TIME_UNIT",
    "DATALAB_MAX_GROUPS_PER_REQUEST",
    "BLOG_DISPLAY",
    "BLOG_PAGES_MAX",
    "BLOG_SORT",
    "BLOG_START_MAX",
]
