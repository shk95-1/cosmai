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

# ---- launch_onset (#285) -------------------------------------------------------------------
#: One keyword group per search-trend request, and that is a design statement rather than a knob: a
#: DataLab ratio is rescaled inside one request, so a small product's series beside a large one's
#: rounds toward zero -- while the onset rule reads a term against its **own** peak, so it needs
#: neither a companion group nor the `DATALAB_ANCHOR` the `datalab` dataset carries for exactly the
#: opposite reason (there, two requests have to be put on one scale afterwards).
LAUNCH_SEARCH_GROUPS_PER_REQUEST = 1
#: `[확인 사실]` The shopping-insight keyword trend answers monthly from 2017-08 and not before
#: (measured 2026-09-20). The search-trend window is `DATALAB_WINDOW_START`, nineteen months
#: earlier, which is why a term already live in 2017 gets no onset from this API: the quiet-months
#: guard refuses the window's own first month, which is the honest answer rather than a bound at
#: the edge of what the vendor happens to hold.
SHOPPING_WINDOW_START = "2017-08-01"
#: `[확인 사실]` The vendor's category id for cosmetics/beauty; the keyword-trend endpoint takes one
#: category per request and refuses a request without it (measured 2026-09-20).
SHOPPING_CATEGORY = "50000002"
#: `[확인 사실]` The vendor's cap on `keyword` groups in one shopping-insight request, and **one term
#: per group**: a group's `param` array is what it searches, and the experiment put one term in
#: each so every term keeps its own series. A group of several terms gives one merged series and
#: loses exactly the per-term onset this axis reads.
SHOPPING_MAX_KEYWORDS_PER_REQUEST = 5
#: The onset threshold, measured on #125's seven sunscreens (2026-09-20): the first month a term
#: reaches a tenth of its own peak. "The first month above zero" read 2016-02 for a 2023 product.
ONSET_PEAK_FRACTION = 0.10
#: The volume guard, part one. A single-group series is scaled to 100 at its own peak whatever the
#: real volume was, so "is the peak high enough" cannot be asked; this asks the shape instead. The
#: onset must be preceded by at least this many months below the threshold, so a flat or near-flat
#: series -- above a tenth of its own peak from the window's first month -- yields no claim at all.
ONSET_MIN_QUIET_MONTHS = 6
#: The volume guard, part two: from the onset onward the series has to be at or above the threshold
#: in this many months -- or in all of the months that are left, when fewer remain. A lone spike in
#: the middle of the window (one month over the vendor's disclosure floor in an otherwise empty
#: series) is refused; a product that launched this month, which can only ever show one or two
#: active months, is not -- and those are the products the metric is about.
ONSET_MIN_ACTIVE_MONTHS = 3
#: How many terms a product's list may hold. The experiment used three per product (brand+line+
#: category, line+category, brand+line) and three fit one shopping request's five groups with room.
LAUNCH_MAX_TERMS_PER_PRODUCT = 3
#: A token of `needs.product_ref.name_norm` is a **generic** category word once it appears in the
#: names of this many distinct brands' products. Derived from the catalogue rather than listed by
#: hand, so the rule holds as the catalogue grows.
LAUNCH_GENERIC_MIN_BRANDS = 3
#: Two requests a product -- one search trend, one shopping insight.
LAUNCH_REQUESTS_PER_PRODUCT = 2
#: How many requests one `launch_onset` run may send: `MAX_REQUESTS_PER_RUN`'s counterpart for the
#: one dataset whose spend scales with the catalogue. Today's 248 `needs.product_ref` rows cost 496
#: requests at two apiece; this ceiling leaves room for the refs to grow and for a run that retried
#: everything, and it is 3% of `DATALAB_MONTHLY_QUOTA` -- the quota is not what binds here either
#: (#110), the term list is.
LAUNCH_MAX_REQUESTS_PER_RUN = 1500

BLOG_DISPLAY = 100
BLOG_PAGES_MAX = 3
BLOG_SORT = "date"
#: `[확인 사실]` vendor-documented hard ceiling on `start`, independent of `total` -- not a scope.json
#: knob, since no configuration of ours can move it.
BLOG_START_MAX = 1000
#: `[확인 사실]` NAVER's own daily ceiling on the Search API (blog), user-confirmed 2026-08-26 (#110)
#: -- not a scope.json knob, since no configuration of ours can move it. 775,000/month converted,
#: matching the search-APIs figure the API Hub docs give (`contracts/entrypoints.md`, read
#: 2026-09-06). Far above today's use; `BLOG_START_MAX` is what actually binds a run.
BLOG_DAILY_QUOTA = 25_000
#: `[확인 사실]` NAVER's own monthly ceiling on each DataLab family endpoint, user-confirmed
#: 2026-08-26 (#110) -- not a scope.json knob. Far above today's use; `BLOG_START_MAX` is what
#: actually binds a run, not this quota.
DATALAB_MONTHLY_QUOTA = 50_000

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
    "LAUNCH_SEARCH_GROUPS_PER_REQUEST",
    "SHOPPING_WINDOW_START",
    "SHOPPING_CATEGORY",
    "SHOPPING_MAX_KEYWORDS_PER_REQUEST",
    "ONSET_PEAK_FRACTION",
    "ONSET_MIN_QUIET_MONTHS",
    "ONSET_MIN_ACTIVE_MONTHS",
    "LAUNCH_MAX_TERMS_PER_PRODUCT",
    "LAUNCH_GENERIC_MIN_BRANDS",
    "LAUNCH_REQUESTS_PER_PRODUCT",
    "LAUNCH_MAX_REQUESTS_PER_RUN",
    "BLOG_DISPLAY",
    "BLOG_PAGES_MAX",
    "BLOG_SORT",
    "BLOG_START_MAX",
    "BLOG_DAILY_QUOTA",
    "DATALAB_MONTHLY_QUOTA",
    "REQUEST_TIMEOUT_S",
    "RETRY_MAX_ATTEMPTS",
    "RETRY_BACKOFF_S",
    "MAX_REQUESTS_PER_RUN",
]
