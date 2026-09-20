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

from analysis.types import LaunchClaimRow, LaunchIntervalRow, LaunchVerdict

# The rule table's own version -- `needs.analysis_run.versions.launch` (contracts/versioning.md).
# v1.1 (#283 review + the #285 review's addendum): lower bounds fold per axis by the earliest,
# the `exact` gate on the deciding lower bound no longer depends on the basis, and `not_new`
# needs an `exact` upper bound of its own.
LAUNCH_VERSION = "rule-v1.1"

# The three claim directions and which bound each one moves.
LOWER_DIRECTIONS = ("not_before", "at")
UPPER_DIRECTIONS = ("not_after", "at")

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

# What the interval's upper end rests on, which is what tells a tier held up by two axes from one
# held up by a lower bound and the reference date alone.
CORROBORATED = "corroborated"
SINGLE_AXIS = "single_axis"
BASES = (CORROBORATED, SINGLE_AXIS)

# The join strengths a claim may carry, and the one this rule version trusts on its own.
MATCH_STRENGTHS = ("exact", "partial")
EXACT = "exact"


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

    `earliest` is the tightest lower bound and `latest` the tightest upper bound **the evidence
    names**; either is None when no claim of that side exists, which is a different answer from a
    wide interval and the rule table treats it as one. The reference-date clamp on the upper end
    belongs to the verdict, not here -- this row has no reference date and the view that mirrors it
    takes none.

    **The lower side folds twice** (rule version 1.1). Several `not_before` claims of **one axis**
    are one statement about one product, and the only part of it that is certainly true is the
    earliest: an MFDS line filed again in 2026 does not say the launch was not before 2026, and a
    refill or gift-set filing of a 2021 line normalises to that same line. So each axis contributes
    the **earliest** edge it names, and only then is the **latest** of those taken, because two
    axes are two independent statements and both have to hold. Upper bounds need no such fold: the
    tightest of them is the earliest, and taking the minimum over all of them already does that.

    `earliest_match` is the join strength of whichever claim ended up setting `earliest`, and on a
    tie -- inside an axis or between two of them -- the surer one."""
    per_axis: dict[str, tuple[date, str]] = {}
    latest: date | None = None
    latest_exact: date | None = None
    read = lower = upper = excluded = 0
    for claim in claims:
        low, high = claim_edges(claim)  # also refuses a precision this version cannot read
        if claim.axis in EXCLUDED_AXES:
            excluded += 1
            continue
        read += 1
        if claim.direction in LOWER_DIRECTIONS:
            lower += 1
            held = per_axis.get(claim.axis)
            if held is None or low < held[0] or (low == held[0] and claim.match_strength < held[1]):
                per_axis[claim.axis] = (low, claim.match_strength)
        if claim.direction in UPPER_DIRECTIONS:
            upper += 1
            latest = high if latest is None else min(latest, high)
            if claim.match_strength == EXACT:
                latest_exact = high if latest_exact is None else min(latest_exact, high)
    earliest: date | None = None
    earliest_match: str | None = None
    for edge, strength in per_axis.values():
        if earliest is None or edge > earliest or (edge == earliest and strength < (earliest_match or "")):
            earliest, earliest_match = edge, strength
    return LaunchIntervalRow(
        product_ref, earliest, earliest_match, latest, latest_exact, read, lower, upper, excluded
    )


def launch_basis(interval: LaunchIntervalRow) -> str:
    """What the interval's upper end rests on: a claim of its own (`corroborated`), or the
    reference-date clamp alone (`single_axis`). It is description and **not a gate** -- since rule
    version 1.1 no row turns on it; it is how much of an answer to believe, and a reader that shows
    a tier shows this beside it."""
    return CORROBORATED if interval.lower_claims and interval.upper_claims else SINGLE_AXIS


def launch_verdict(interval: LaunchIntervalRow, reference_date: date) -> LaunchVerdict:
    """The rule table of §Launch evidence, in its own order -- the rows below are that table."""
    earliest, evidence_latest = interval.earliest, interval.latest
    basis = launch_basis(interval)
    # The one upper bound that is always true: a product cannot have launched after the date being
    # asked about. Without it a month-precision onset in the month in progress widens past the
    # reference date and drops a corroborated pair to `unknown` (the review's measured defect).
    latest = reference_date if evidence_latest is None else min(evidence_latest, reference_date)
    if interval.claims == 0:
        return LaunchVerdict(UNKNOWN, basis)
    # Between two claims, never against the clamp: a crossing is how a variant, a renewal or a set
    # announces itself, and it is never auto-resolved.
    if earliest is not None and evidence_latest is not None and earliest > evidence_latest:
        return LaunchVerdict(CONFLICT, basis)
    # Row 3, and the only way to `not_new`. It reads `latest_exact` and not `latest`: a `partial`
    # upper bound can be FALSE rather than weak -- a sibling line sharing a DataLab term makes the
    # series rise when the OLDER sibling launched, and a member-linked review belongs to a listing
    # the linker only guessed was this product -- so on its own it may not declare a product old.
    # `latest_exact` is the tightest `exact` upper bound, so testing it is testing whether **any**
    # `exact` upper bound is older than the widest window; a `partial` claim tighter than an `exact`
    # one cannot take that `exact` one's proof away.
    if interval.latest_exact is not None and interval.latest_exact < months_before(
        reference_date, WINDOW_MONTHS[-1]
    ):
        return LaunchVerdict(NOT_NEW, basis)
    if earliest is None:
        return LaunchVerdict(UNKNOWN, basis)
    if earliest > latest:
        # The lower bound is later than the reference date, so the launch is after it. That is not
        # a contradiction between axes, and the vocabulary has no word for "has not launched yet".
        return LaunchVerdict(UNKNOWN, basis)
    if interval.earliest_match != EXACT:
        # Option (B)'s risk, priced (rule version 1.1: whatever the basis). A tier is only as good
        # as the join that set its lower bound, and an upper bound corroborates that the product
        # existed, never that the lower bound names the right product -- so it may not open this
        # gate. Rows 2 and 3 are tested before it, so a weak join still contradicts and still ages.
        return LaunchVerdict(UNKNOWN, basis)
    for months in WINDOW_MONTHS:
        # The whole interval inside the window, never merely touching it -- which is also what
        # makes a tier narrower than the interval's own width impossible to assign.
        if months_before(reference_date, months) <= earliest:
            return LaunchVerdict(NEW.format(months=months), basis)
    return LaunchVerdict(UNKNOWN, basis)


def launch_at(interval: LaunchIntervalRow) -> date | None:
    """The launch instant a reader may quote: the lower bound, and only where a pair of real
    claims pins it within FIXED_WITHIN_MONTHS. The reference-date clamp is not a claim and never
    pins anything here, so a `single_axis` interval has no quotable instant. No verdict is read off
    this value (§Launch evidence)."""
    earliest, latest = interval.earliest, interval.latest
    if earliest is None or latest is None or earliest > latest:
        return None
    return earliest if latest <= months_after(earliest, FIXED_WITHIN_MONTHS) else None
