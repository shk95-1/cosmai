"""The onset rule and the claim it makes (#285) -- pure functions over a monthly series, no
database and no transport.

Every series here is built rather than fixed, so the shapes under test are the measured ones and not
one recorded answer: a term that launches mid-window, a flat series, a series whose rise lands in the
month in progress, a single spike, and a term already live when the window opened.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from collectors.naver import launch, onset, scope


def _series(values: list[float | None], *, start_year: int = 2016, start_month: int = 1):
    """`[(month, ratio), ...]` from a list of ratios, one a month from `start`."""
    out = []
    year, month = start_year, start_month
    for value in values:
        out.append((f"{year:04d}-{month:02d}", value))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _quiet(n: int) -> list[float | None]:
    return [0.0] * n


def test_the_onset_is_the_first_month_at_a_tenth_of_the_terms_own_peak():
    # 24 quiet months, then a rise to a peak of 100. A tenth of 100 is 10, so the onset is the
    # first month at 12.0 and not the first month above zero (which is a month earlier, at 3.0 --
    # the noise that read 2016-02 for a 2023 product).
    series = _series([*_quiet(24), 3.0, 12.0, 40.0, 100.0, 80.0])
    assert onset.series_onset(series) == "2018-02"


def test_the_first_month_above_zero_is_not_the_rule():
    series = _series([*_quiet(12), 0.4, *_quiet(11), 30.0, 60.0, 100.0])
    # 0.4 is above zero and far below a tenth of 100; the month that counts is the one at 30.0.
    assert onset.series_onset(series) != "2017-01"
    assert onset.series_onset(series) == "2018-01"


def test_a_flat_series_yields_no_onset():
    # Every month is its own peak's tenth or more, so the "onset" would be the window's first month
    # -- our window's edge, not the product's. That is no evidence and must produce no claim.
    assert onset.series_onset(_series([50.0] * 60)) is None


def test_a_series_live_from_the_first_month_of_the_window_yields_no_onset():
    # The term was already running in 2016: the rise inside the window is not its launch.
    series = _series([80.0, 90.0, 100.0, *([60.0] * 40)])
    assert onset.series_onset(series) is None


def test_a_single_spike_in_the_middle_of_the_window_yields_no_onset():
    # One month over the vendor's disclosure floor in an otherwise empty series, with ten months
    # after it to show a launch and none of them above the threshold. `min_active_months` refuses
    # it; two more active months make the same series an onset.
    spike = _series([*_quiet(30), 100.0, *_quiet(10)])
    assert onset.series_onset(spike) is None
    assert scope.ONSET_MIN_ACTIVE_MONTHS == 3
    sustained = _series([*_quiet(30), 100.0, 90.0, 80.0, *_quiet(8)])
    assert onset.series_onset(sustained) == "2018-07"


def test_an_onset_in_the_month_in_progress_is_returned():
    # A product launched this month: the series runs to the month in progress and the rise is in it.
    # The active-months guard asks for what the window can still show -- one month here -- so the
    # freshest products, the ones the metric is actually about, are not refused by a rule aimed at
    # a spike. #282's verdict clamps the interval's upper end to the reference date, so the
    # month-precision claim widened to this month's last day is not lost either.
    series = _series([*_quiet(22), 100.0], start_year=2024, start_month=11)
    assert onset.series_onset(series) == "2026-09"


def test_a_month_with_no_ratio_is_absent_rather_than_zero():
    # DataLab does return 0, so a NULL is a month it gave no number for. Reading it as zero would
    # invent a quiet month and could move the onset.
    series = _series([None, None, *_quiet(24), 50.0, 60.0, 100.0])
    assert onset.series_onset(series) == "2018-03"


def test_an_empty_series_yields_no_onset():
    assert onset.series_onset([]) is None
    assert onset.series_onset(_series([None] * 40)) is None


def test_the_claimed_month_is_the_later_of_the_two_apis():
    # The conservative bound: a later upper bound is the weaker statement, and this axis may only
    # ever make a product older.
    assert onset.combine(["2023-01", "2023-05"]) == "2023-05"
    assert onset.combine(["2023-05", "2023-01"]) == "2023-05"


def test_one_api_alone_still_makes_the_claim():
    assert onset.combine(["2023-05", None]) == "2023-05"
    assert onset.combine([None, "2023-01"]) == "2023-01"


def test_no_api_with_an_onset_makes_no_claim():
    assert onset.combine([None, None]) is None
    assert onset.combine([]) is None


def test_the_claim_is_an_upper_bound_at_month_precision_on_the_first_of_the_month():
    outcome = launch.ProductOutcome(
        product_ref="oy:A1",
        terms=("a", "b"),
        claimed_month="2023-05",
        apis=("search_trend", "shopping_insight"),
        refused=False,
    )
    observed = datetime(2026, 9, 20, 7, 20, tzinfo=UTC)
    claim = launch.claim_for(outcome, match_strength="exact", observed_at=observed)
    assert claim is not None
    assert claim.axis == "datalab_onset"
    assert claim.direction == "not_after"
    assert claim.claimed_precision == "month"
    # The axis stores the month's first day and never the widened edge: the reader widens an upper
    # bound to the month's last day (contracts/interfaces.md §Launch evidence).
    assert claim.claimed_on == date(2023, 5, 1)
    assert claim.match_strength == "exact"
    assert claim.axis_version == launch.ONSET_AXIS_VERSION
    assert claim.observed_at == observed
    assert claim.note is not None and "search_trend" in claim.note


def test_a_product_with_no_onset_makes_no_claim():
    outcome = launch.ProductOutcome(
        product_ref="oy:A1", terms=("a",), claimed_month=None, apis=(), refused=False
    )
    assert launch.claim_for(outcome, match_strength="exact", observed_at=datetime.now(UTC)) is None


def test_the_source_ref_is_the_term_set_and_does_not_move_with_the_window():
    # A monthly pass re-states one claim; a source_ref built from the request would carry endDate
    # and mint a new live claim every month under a key the last one cannot overwrite.
    first = launch.source_ref(("a", "b"))
    assert first == launch.source_ref(("b", "a")), "term order is not part of the identity"
    assert first != launch.source_ref(("a", "b", "c"))
    assert first and first != ""


def test_a_search_request_carries_exactly_one_keyword_group_and_no_anchor():
    spec = launch.search_spec("oy:A1", ("t1", "t2", "t3"), end_date=date(2026, 9, 20))
    groups = spec.params["keywordGroups"]
    assert len(groups) == scope.LAUNCH_SEARCH_GROUPS_PER_REQUEST == 1
    assert groups[0]["keywords"] == ["t1", "t2", "t3"]
    assert scope.DATALAB_ANCHOR not in [g["groupName"] for g in groups]
    assert spec.params["timeUnit"] == scope.DATALAB_TIME_UNIT
    assert spec.params["startDate"] == scope.DATALAB_WINDOW_START
    assert spec.params["endDate"] == "2026-09-20"
    assert spec.kind == "launch_trend"


def test_a_shopping_request_carries_one_term_per_keyword_group():
    spec = launch.shopping_spec("oy:A1", ("t1", "t2", "t3"), end_date=date(2026, 9, 20))
    groups = spec.params["keyword"]
    assert [g["param"] for g in groups] == [["t1"], ["t2"], ["t3"]]
    assert all(len(g["param"]) == 1 for g in groups)
    assert spec.params["category"] == scope.SHOPPING_CATEGORY
    assert spec.params["startDate"] == scope.SHOPPING_WINDOW_START
    assert spec.kind == "launch_shopping"


def test_a_shopping_request_never_exceeds_the_vendors_group_cap():
    many = tuple(f"t{i}" for i in range(9))
    spec = launch.shopping_spec("oy:A1", many, end_date=date(2026, 9, 20))
    assert len(spec.params["keyword"]) == scope.SHOPPING_MAX_KEYWORDS_PER_REQUEST


@pytest.mark.parametrize("terms", [(), ("",)])
def test_a_request_is_never_built_without_a_term(terms):
    with pytest.raises(ValueError):
        launch.search_spec("oy:A1", terms, end_date=date(2026, 9, 20))
    with pytest.raises(ValueError):
        launch.shopping_spec("oy:A1", terms, end_date=date(2026, 9, 20))
