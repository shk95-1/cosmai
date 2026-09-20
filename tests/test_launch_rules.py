"""The launch verdict rule table, row by row -- `contracts/interfaces.md` §Launch evidence (#282).

Every case here is a set of evidence claims over one product and a reference date, and every one
of them is a row of that table rather than an example of it. The four traps the user's 2026-09-20
measurement found (a slow burner, a line name older than the product, a later variant matched by
MFDS, upper bounds standing alone) are at the bottom: they are what the table was written against,
and the point of holding them here is that none of them needed a rule of its own.

Nothing in this file touches a database. The rule table is a pure function over claims so that a
row of it can be asked in isolation -- the view is pinned to it separately
(tests/test_product_launch_view.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

import pytest

from analysis.launch import (
    CONFLICT,
    EXCLUDED_AXES,
    LAUNCH_VERSION,
    NOT_NEW,
    UNKNOWN,
    VERDICTS,
    claim_edges,
    launch_at,
    launch_interval,
    launch_verdict,
)
from analysis.types import LaunchClaimRow

REF = date(2026, 9, 20)  # the reference date of the user's measurement, and a parameter everywhere
PRODUCT = "oy:A000000155458"
OBSERVED = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)


def claim(
    direction: str,
    claimed_on: date,
    precision: str = "day",
    axis: str = "mfds_report",
    source_ref: str = "report_seq=2026000001",
) -> LaunchClaimRow:
    return LaunchClaimRow(
        product_ref=PRODUCT,
        axis=axis,
        direction=direction,
        claimed_on=claimed_on,
        claimed_precision=precision,
        source_ref=source_ref,
        axis_version="rule-v1.0",
        observed_at=OBSERVED,
    )


def verdict(claims: Sequence[LaunchClaimRow], reference_date: date = REF) -> str:
    return launch_verdict(launch_interval(PRODUCT, claims), reference_date)


def test_an_empty_claim_set_is_unknown():
    # Row 1. The whole catalogue starts here, and it is an answer rather than a missing value.
    assert verdict([]) == UNKNOWN


def test_a_not_after_earlier_than_a_not_before_is_conflict():
    # Row 2. Never auto-resolved: it is how a variant, a renewal or a set announces itself.
    claims = [claim("not_before", date(2026, 5, 4)), claim("not_after", date(2023, 1, 9))]
    assert verdict(claims) == CONFLICT


def test_conflict_outranks_every_window_the_bounds_would_otherwise_fall_in():
    # Both bounds sit inside the 3-month window, and they still cross -- the tier must not be read
    # off an interval that does not exist.
    claims = [claim("not_before", date(2026, 9, 10)), claim("not_after", date(2026, 9, 1))]
    assert verdict(claims) == CONFLICT


def test_an_upper_bound_older_than_the_widest_window_is_not_new():
    # Row 3. A 2019 review proves age on its own: the launch is no later than it.
    assert verdict([claim("not_after", date(2019, 3, 2), axis="old_review")]) == NOT_NEW


def test_upper_bounds_alone_never_yield_a_tier():
    # Row 4, the measured trap: a recent upper bound says the launch is no later than a recent
    # date, which is true of a product launched in 2011 as well.
    claims = [
        claim("not_after", date(2026, 7, 2), axis="old_review"),
        claim("not_after", date(2026, 8, 1), axis="datalab_onset", precision="month"),
    ]
    assert verdict(claims) == UNKNOWN


def test_lower_bounds_alone_are_unknown_however_recent_they_are():
    # Row 5. [earliest, forever) is wider than every window, so no tier separates.
    assert verdict([claim("not_before", date(2026, 9, 1))]) == UNKNOWN


@pytest.mark.parametrize(
    ("earliest", "latest", "expected"),
    [
        (date(2026, 8, 1), date(2026, 8, 20), "new_3m"),
        (date(2026, 6, 20), date(2026, 9, 1), "new_3m"),  # exactly on the 3-month edge
        (date(2026, 6, 19), date(2026, 9, 1), "new_6m"),  # one day past it
        (date(2026, 4, 1), date(2026, 5, 1), "new_6m"),
        (date(2025, 12, 1), date(2026, 1, 5), "new_12m"),
        (date(2025, 9, 20), date(2025, 10, 1), "new_12m"),  # exactly on the 12-month edge
    ],
)
def test_a_pair_takes_the_narrowest_window_that_holds_the_whole_interval(
    earliest: date, latest: date, expected: str
):
    # Row 6, and the user's "a not_before and a not_after within 6 months of each other" row: the
    # tier is the narrowest window the interval fits inside, never the narrowest it touches.
    assert verdict([claim("not_before", earliest), claim("not_after", latest)]) == expected


def test_an_interval_wider_than_three_months_is_never_new_3m():
    # The tier guard, stated as the user stated it. The upper bound is yesterday, so the product is
    # recent by any reading -- and the interval is still 4 months wide.
    claims = [claim("not_before", date(2026, 5, 19)), claim("not_after", date(2026, 9, 19))]
    assert verdict(claims) == "new_6m"


def test_an_interval_that_straddles_the_widest_window_is_unknown():
    # Row 7: the slow burner's shape. Nothing here proves new and nothing proves not-new.
    claims = [claim("not_before", date(2019, 5, 2)), claim("not_after", date(2026, 3, 31))]
    assert verdict(claims) == UNKNOWN


def test_an_upper_bound_past_the_reference_date_takes_no_tier():
    # The interval is not inside any window ending at the reference date, so it separates nothing.
    claims = [claim("not_before", date(2026, 9, 1)), claim("not_after", date(2026, 10, 31))]
    assert verdict(claims) == UNKNOWN


def test_the_reference_date_is_a_parameter_and_moves_the_verdict():
    claims = [claim("not_before", date(2026, 7, 1)), claim("not_after", date(2026, 7, 20))]
    assert verdict(claims, date(2026, 9, 20)) == "new_3m"
    assert verdict(claims, date(2026, 12, 20)) == "new_6m"
    assert verdict(claims, date(2027, 9, 20)) == NOT_NEW


def test_a_month_claim_is_widened_to_its_whole_month():
    # A DataLab month and an MFDS day in one interval: the month is worth its whole month, so the
    # bound moves outward and never inward.
    lower, upper = claim_edges(claim("not_after", date(2026, 7, 1), precision="month"))
    assert (lower, upper) == (date(2026, 7, 1), date(2026, 7, 31))
    lower, upper = claim_edges(claim("not_before", date(2026, 2, 15), precision="month"))
    assert (lower, upper) == (date(2026, 2, 1), date(2026, 2, 28))


def test_mixed_precision_costs_the_tier_the_widening_crosses():
    # The same two claims, month precision on the lower bound alone: 2026-06-25 as a day sits
    # inside the 3-month window, and the month it belongs to does not.
    day = [claim("not_before", date(2026, 6, 25)), claim("not_after", date(2026, 7, 10))]
    month = [
        claim("not_before", date(2026, 6, 25), precision="month"),
        claim("not_after", date(2026, 7, 10)),
    ]
    assert verdict(day) == "new_3m"
    assert verdict(month) == "new_6m"


def test_an_at_claim_moves_both_bounds():
    # A month-precision `at` is a one-month interval, and that is narrow enough for the 3-month tier.
    assert verdict([claim("at", date(2026, 8, 3), precision="month")]) == "new_3m"


def test_a_precision_this_rule_version_does_not_know_is_refused():
    # Silently dropping the claim would make a narrower interval out of less evidence, which is the
    # one direction this rule table may never move in.
    with pytest.raises(ValueError, match="precision"):
        launch_interval(PRODUCT, [claim("at", date(2026, 8, 3), precision="quarter")])


def test_an_excluded_axis_is_recorded_and_not_read():
    excluded = next(iter(EXCLUDED_AXES))
    claims = [
        claim("not_before", date(2026, 8, 1), axis=excluded, source_ref="oy:A1"),
        claim("not_after", date(2026, 8, 20), axis=excluded, source_ref="oy:A2"),
    ]
    interval = launch_interval(PRODUCT, claims)
    assert (interval.earliest, interval.latest) == (None, None)
    assert (interval.claims, interval.excluded_claims) == (0, 2)
    assert launch_verdict(interval, REF) == UNKNOWN


def test_the_interval_counts_what_it_read():
    claims = [
        claim("not_before", date(2026, 8, 1), source_ref="report_seq=1"),
        claim("not_before", date(2026, 8, 9), source_ref="report_seq=2"),
        claim("not_after", date(2026, 8, 20), axis="old_review", source_ref="oy:A1"),
        claim("at", date(2026, 8, 15), axis="vendor_board", source_ref="daisomall:99"),
    ]
    interval = launch_interval(PRODUCT, claims)
    # The bounds are the tightest of each side: max of the lower ones, min of the upper ones.
    assert (interval.earliest, interval.latest) == (date(2026, 8, 15), date(2026, 8, 15))
    assert (interval.claims, interval.lower_claims, interval.upper_claims) == (4, 3, 2)
    assert interval.excluded_claims == 0


def test_launch_at_is_the_lower_bound_only_when_the_pair_pins_it():
    pinned = launch_interval(
        PRODUCT, [claim("not_before", date(2026, 5, 4)), claim("not_after", date(2026, 7, 1))]
    )
    assert launch_at(pinned) == date(2026, 5, 4)
    loose = launch_interval(
        PRODUCT, [claim("not_before", date(2019, 5, 2)), claim("not_after", date(2026, 3, 31))]
    )
    assert launch_at(loose) is None
    assert launch_at(launch_interval(PRODUCT, [claim("not_before", date(2026, 5, 4))])) is None


@pytest.mark.parametrize(
    ("name", "claims", "expected"),
    [
        (
            # 3 of 7 sunscreens: a DataLab onset 1-3 months after the MFDS report date.
            "clean pair",
            [
                claim("not_before", date(2026, 6, 12)),
                claim("not_after", date(2026, 8, 1), axis="datalab_onset", precision="month"),
            ],
            "new_6m",
        ),
        (
            # The slow burner: the term only reaches its own 10% years after the filing.
            "slow burner",
            [
                claim("not_before", date(2019, 5, 2)),
                claim("not_after", date(2026, 3, 1), axis="datalab_onset", precision="month"),
            ],
            UNKNOWN,
        ),
        (
            # A line name older than the product: the onset precedes the filing.
            "line name older than the product",
            [
                claim("not_before", date(2024, 6, 3)),
                claim("not_after", date(2016, 1, 1), axis="datalab_onset", precision="month"),
            ],
            CONFLICT,
        ),
        (
            # A later variant registration matched to an older line: the held review is older than
            # the filing the join picked.
            "later variant matched by MFDS",
            [
                claim("not_before", date(2026, 5, 4)),
                claim("not_after", date(2023, 1, 9), axis="old_review", source_ref="oy:A1"),
            ],
            CONFLICT,
        ),
        (
            # Upper bounds alone, both recent: the collector pages only recent reviews, so a recent
            # minimum proves nothing.
            "upper bounds alone",
            [
                claim("not_after", date(2026, 7, 2), axis="old_review", source_ref="oy:A1"),
                claim("not_after", date(2026, 8, 1), axis="datalab_onset", precision="month"),
            ],
            UNKNOWN,
        ),
    ],
)
def test_the_measured_traps_need_no_rule_of_their_own(
    name: str, claims: Sequence[LaunchClaimRow], expected: str
):
    assert verdict(claims) == expected, name


def test_every_answer_is_one_of_the_six_the_contract_names():
    assert set(VERDICTS) == {"new_3m", "new_6m", "new_12m", "not_new", "unknown", "conflict"}
    assert LAUNCH_VERSION == "rule-v1.0"
