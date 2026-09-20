"""The launch verdict — `contracts/interfaces.md` §Launch evidence is canonical (#282).

This module knows no DB. It takes the evidence claims of one product (the rows of
`needs.product_launch_evidence`, or the interval `needs.product_launch` already folded them into)
and answers one question: at a given reference date, is this product new, and inside which window.
The rule table lives here and nowhere else -- the view restates the interval half of it in SQL for
SQL readers alone, and `tests/test_product_launch_view.py` pins the two together.

A verdict is a statement about evidence, not about a product: `unknown` and `conflict` are answers,
not failures, and a tier is assigned only where the evidence separates the tiers.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from datetime import date

from analysis.types import LaunchClaimRow, LaunchIntervalRow

# The rule table's own version -- `needs.analysis_run.versions.launch` (contracts/versioning.md).
LAUNCH_VERSION = "rule-v1.0"

# The three claim directions and which bound each one moves.
LOWER_DIRECTIONS = ("not_before", "at")
UPPER_DIRECTIONS = ("not_after", "at")
PRECISIONS = ("day", "month")

# Recorded as evidence, not read by this rule version: a vendor's `[NEW]` title tag is marketing
# text on one vendor (#283 axis 5, user decision 2026-09-20).
EXCLUDED_AXES = frozenset({"vendor_title_tag"})

# The windows, narrowest first. The verdict names the first one that holds.
WINDOW_MONTHS = (3, 6, 12)
# A pair this close pins the launch at its lower bound (#282's rule table).
FIXED_WITHIN_MONTHS = 6

NEW = "new_{months}m"
NOT_NEW = "not_new"
UNKNOWN = "unknown"
CONFLICT = "conflict"
VERDICTS = ("new_3m", "new_6m", "new_12m", NOT_NEW, UNKNOWN, CONFLICT)


def shift_months(day: date, months: int) -> date:
    """Day-of-month is clamped to the month's length, the same arithmetic
    `date + interval 'n months'` does, so a 31st never walks into the next month."""
    total = day.year * 12 + day.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def months_before(day: date, months: int) -> date:
    """A window edge: the reference date less the window."""
    return shift_months(day, -months)


def months_after(day: date, months: int) -> date:
    return shift_months(day, months)


def claim_edges(claim: LaunchClaimRow) -> tuple[date, date]:
    """The claim widened to the span its own precision can actually name: a day is itself, a month
    is its whole month. The widening only ever moves a bound outward, so mixed precision costs a
    tier and never buys one."""
    if claim.claimed_precision == "day":
        return claim.claimed_on, claim.claimed_on
    if claim.claimed_precision == "month":
        first = claim.claimed_on.replace(day=1)
        return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])
    # Not a guess and not a skip: a claim dropped in silence would make a narrower interval out of
    # less evidence, which is the one direction this rule table may not move in.
    raise ValueError(f"{claim.axis}: unknown claimed_precision {claim.claimed_precision!r}")


def launch_interval(product_ref: str, claims: Iterable[LaunchClaimRow]) -> LaunchIntervalRow:
    """[earliest, latest] over the claims this rule version reads, and how many it read.

    `earliest` is the tightest lower bound and `latest` the tightest upper bound; either is None
    when no claim of that side exists, which is a different answer from a wide interval and the
    rule table treats it as one."""
    earliest: date | None = None
    latest: date | None = None
    read = lower = upper = excluded = 0
    for claim in claims:
        low, high = claim_edges(claim)  # also refuses a precision this version cannot read
        if claim.axis in EXCLUDED_AXES:
            excluded += 1
            continue
        read += 1
        if claim.direction in LOWER_DIRECTIONS:
            lower += 1
            earliest = low if earliest is None else max(earliest, low)
        if claim.direction in UPPER_DIRECTIONS:
            upper += 1
            latest = high if latest is None else min(latest, high)
    return LaunchIntervalRow(product_ref, earliest, latest, read, lower, upper, excluded)


def launch_verdict(interval: LaunchIntervalRow, reference_date: date) -> str:
    """The rule table of §Launch evidence, in its own order -- the rows below are that table."""
    earliest, latest = interval.earliest, interval.latest
    if interval.claims == 0:
        return UNKNOWN
    if earliest is not None and latest is not None and earliest > latest:
        return CONFLICT
    if latest is not None and latest < months_before(reference_date, WINDOW_MONTHS[-1]):
        return NOT_NEW
    if earliest is None or latest is None:
        return UNKNOWN
    for months in WINDOW_MONTHS:
        # The whole interval inside the window, never merely touching it -- which is also what
        # makes a tier narrower than the interval's own width impossible to assign.
        if months_before(reference_date, months) <= earliest and latest <= reference_date:
            return NEW.format(months=months)
    return UNKNOWN


def launch_at(interval: LaunchIntervalRow) -> date | None:
    """The launch instant a reader may quote: the lower bound, and only where a pair pins it
    within FIXED_WITHIN_MONTHS. No verdict is read off this value (§Launch evidence)."""
    earliest, latest = interval.earliest, interval.latest
    if earliest is None or latest is None or earliest > latest:
        return None
    return earliest if latest <= months_after(earliest, FIXED_WITHIN_MONTHS) else None
