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


def months_before(day: date, months: int) -> date:
    """The window edge. Day-of-month is clamped to the month's length, the same arithmetic
    `date - interval 'n months'` does, so a 31st never walks into the next month."""
    total = day.year * 12 + day.month - 1 - months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def claim_edges(claim: LaunchClaimRow) -> tuple[date, date]:
    """The claim widened to the span its own precision can actually name."""
    return claim.claimed_on, claim.claimed_on


def launch_interval(product_ref: str, claims: Iterable[LaunchClaimRow]) -> LaunchIntervalRow:
    """[earliest, latest] over the claims this rule version reads, plus what it read."""
    return LaunchIntervalRow(product_ref, None, None, 0, 0, 0, 0)


def launch_verdict(interval: LaunchIntervalRow, reference_date: date) -> str:
    """The rule table of §Launch evidence, row by row."""
    return UNKNOWN


def launch_at(interval: LaunchIntervalRow) -> date | None:
    """The launch instant a reader may quote, or None when the evidence does not pin one."""
    return None
