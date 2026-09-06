"""Sample-design constants `cli.py` actually runs on. `scope.json` is this module's declarative
copy (contracts/entrypoints.md: 'a variant of scope.lock'), and
`tests/collectors/naver/test_scope_matches_constants.py` is the drift check -- the same split
`collectors/commerce/models.py` constants vs. `collectors/commerce/scope.json` uses (#7's form)."""

from __future__ import annotations

DATALAB_WINDOW_START = "2016-01-01"
DATALAB_TIME_UNIT = "month"
DATALAB_MAX_GROUPS_PER_REQUEST = 5
#: The one global anchor group (#90, user decision 2026-08-26), sent as its own `keywordGroups`
#: entry in every DataLab request so that two requests can be put on one scale afterwards
#: (`db/views/naver_datalab_rescaled.sql`). This is the `groupName` alone -- a label the vendor
#: echoes back as `results[].title`, which is what the point's `group_key` and the rescale view's
#: SQL literal key off. It is never searched: sending it as its own keyword searched a token with no
#: volume, so the anchor series came back with an empty `data` array and nothing rescaled (#250).
DATALAB_ANCHOR = "기준_세럼"
#: What the anchor group actually searches (#250, user decision 2026-09-06), a tuple like a
#: category group's terms. Measured live over the collector's own window (2016-01-01 -> today,
#: monthly): a point in all 129 months, no zero, and beside the four real groups of a request its
#: own series runs 5.19970..100 -- high enough that the view never divides by zero, low enough that
#: the observed groups keep their scale. Changing it changes `datalab_request_key`, which is
#: correct: it is a different request. Old rows keep the ratios they were collected with.
DATALAB_ANCHOR_TERMS = ("세럼",)
#: How many of a category's own groups ride beside the anchor. The vendor's cap counts the anchor as
#: one of its 5 groups, so a category with more groups than this takes more than one request.
DATALAB_CATEGORY_GROUPS_PER_REQUEST = DATALAB_MAX_GROUPS_PER_REQUEST - 1

BLOG_DISPLAY = 100
BLOG_PAGES_MAX = 3
BLOG_SORT = "date"
#: `[확인 사실]` vendor-documented hard ceiling on `start`, independent of `total` -- not a scope.json
#: knob, since no configuration of ours can move it.
BLOG_START_MAX = 1000

#: The live transport (#182). REQUEST_TIMEOUT_S is `collectors/commerce/contract.py`'s house default
#: for an HTTP source; a JSON API answering slower than that is not answering.
REQUEST_TIMEOUT_S = 20.0
#: A 5xx or a timeout is retried this many times in total (the first attempt included), waiting
#: RETRY_BACKOFF_S doubled at each step -- 2s then 4s.
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = 2.0
#: How many requests one run may send. Every attempt is charged, retries included, because a retry
#: is another request NAVER serves. Today's worst case is 46 (1 datalab + 15 blog terms x 3 pages),
#: so this bounds a keyword file that grows or a run that retries everything without ever nearing
#: the vendor quota (#110: 50,000/month datalab, 775,000/month search).
MAX_REQUESTS_PER_RUN = 200

__all__ = [
    "DATALAB_WINDOW_START",
    "DATALAB_TIME_UNIT",
    "DATALAB_MAX_GROUPS_PER_REQUEST",
    "DATALAB_ANCHOR",
    "DATALAB_ANCHOR_TERMS",
    "DATALAB_CATEGORY_GROUPS_PER_REQUEST",
    "BLOG_DISPLAY",
    "BLOG_PAGES_MAX",
    "BLOG_SORT",
    "BLOG_START_MAX",
    "REQUEST_TIMEOUT_S",
    "RETRY_MAX_ATTEMPTS",
    "RETRY_BACKOFF_S",
    "MAX_REQUESTS_PER_RUN",
]
