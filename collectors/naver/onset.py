"""The onset rule: a monthly series in, a month or nothing out (#285, the #125 experiment of
2026-09-20).

**Onset = the first month a term reaches `ONSET_PEAK_FRACTION` of that term's own peak.** Measured
on seven sunscreens with a known MFDS report date, it sat 1-3 months after the filing on three
cleanly named lines, years after on a slow burner, and *before* it on two -- and both of those
turned out to be MFDS join errors, which is the cross-check the axis is kept for. "The first month
above zero" is not the rule: it read 2016-02 for a 2023 product.

Because the onset is relative to the term's **own** peak, this axis needs no anchor group of the
kind every `datalab` request carries (#90). An anchor exists there because a ratio is rescaled
inside one request, so two requests cannot be compared without a shared series; here nothing is
compared across requests, and the one thing a second group would do is round a small product's
series toward zero beside a large one's. Hence one keyword group per search-trend request, and one
term per shopping-insight keyword group (the vendor's own shape).

A self-normalised series is also why the volume guard cannot be "the peak is high enough": with a
single group the vendor scales the peak to 100 whatever the real volume was. The two guards below
are about the series' shape instead, and both were chosen against the measured failures:

  ONSET_MIN_QUIET_MONTHS   the onset must have at least this many months below the threshold before
                           it. A flat or near-flat series is above a tenth of its own peak from the
                           window's first month, so its "onset" is the edge of our window and not
                           the product's -- the same for a term that was already live in 2016.
  ONSET_MIN_ACTIVE_MONTHS  from the onset onward, this many months at or above the threshold -- or
                           all of the ones that are left, when fewer remain. A lone spike in the
                           middle of the window (one month over the vendor's disclosure floor) is
                           refused, while a product that launched this month, which can show only
                           one active month, is not: a guard aimed at noise must not refuse exactly
                           the products the metric is about.

The month in progress counts: the series runs to today and a fresh product's onset lands in it.
That is deliberate and it is safe because #282's verdict clamps the interval's upper end to the
reference date -- without the clamp a month-precision claim widened to that month's last day would
run past `R` and drop a corroborated pair to `unknown` (contracts/interfaces.md §Launch evidence).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from collectors.naver.scope import (
    ONSET_MIN_ACTIVE_MONTHS,
    ONSET_MIN_QUIET_MONTHS,
    ONSET_PEAK_FRACTION,
)


def series_onset(points: Sequence[tuple[str, float | None]]) -> str | None:
    """`[(month, ratio), ...]` in any order -> the onset month `'YYYY-MM'`, or None when the series
    says nothing. A month with a NULL ratio is a month the vendor gave no number for and is read as
    absent, not as zero: zero is a number DataLab does return."""
    months = sorted((month, ratio) for month, ratio in points if ratio is not None)
    if not months:
        return None
    peak = max(ratio for _, ratio in months)
    threshold = peak * ONSET_PEAK_FRACTION
    active = [index for index, (_, ratio) in enumerate(months) if ratio >= threshold]
    if not active:
        # Unreachable while the threshold is a fraction of the peak, since the peak itself clears
        # it. Written out rather than asserted: a later fraction above 1 would make it reachable.
        return None
    first = active[0]
    if first < ONSET_MIN_QUIET_MONTHS:
        # The term was already at a tenth of its own peak when the window opened -- a flat series,
        # or one whose rise predates 2016. Either way the month would be our window's edge.
        return None
    remaining = len(months) - first
    if sum(1 for index in active if index >= first) < min(ONSET_MIN_ACTIVE_MONTHS, remaining):
        return None
    return months[first][0]


def combine(onsets: Iterable[str | None]) -> str | None:
    """The claimed month out of the two APIs' onsets: **the later of the ones that answered**.

    Later is the conservative direction for a `not_after` bound -- a later upper bound is a weaker
    statement, and this axis may only ever make a product older, never newer (contracts/interfaces.md
    §Launch evidence, row 4). When one API has no series at all, or its series is refused by the
    guards above, the other one's onset stands alone: an onset is a sound observation on its own,
    and the pair is a second opinion rather than a precondition. Which APIs answered is recorded in
    the claim's `note`, which no rule reads -- an onset that is too early lands as `conflict`, which
    is a finding, not a silent error."""
    answered = [month for month in onsets if month]
    return max(answered) if answered else None


def api_onset(series: Mapping[str, Sequence[tuple[str, float | None]]]) -> str | None:
    """One API's own answer over the several series it returned: **the earliest** of their onsets.

    The asymmetry with `combine` above is deliberate. The terms of one product are the same
    observation spelled differently, so the first month any spelling took off is what that
    instrument saw -- which is what the 2026-09-20 experiment took across a shopping request's five
    keyword groups. The two APIs are two instruments, and there the axis keeps the *weaker* bound so
    one instrument's early read cannot mint a tight one on its own."""
    answered = [month for month in (series_onset(points) for points in series.values()) if month]
    return min(answered) if answered else None


__all__ = [
    "series_onset",
    "api_onset",
    "combine",
    "ONSET_PEAK_FRACTION",
    "ONSET_MIN_QUIET_MONTHS",
    "ONSET_MIN_ACTIVE_MONTHS",
]
