"""The hand-labelled launch sample and the score it produces (#283 work 6).

Two things are held here. That the file the user fills **ships empty** — a launch month nobody knows
is not a label, and a row invented to make a number would make the number worthless. And that the
scorer answers per axis in the one direction each axis claims: a lower bound is right when it sits
before the labelled month and an upper bound when it sits after it, so one signed number says both
and a negative one is the axis contradicting the label.

The sample's own Korean product names are data under `tests/fixtures/launch/` (`tool/checks/lang`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from analysis.launch.axes import MFDS, OLD_REVIEW, VENDOR_TITLE_TAG
from analysis.launch.sample import (
    COLUMNS,
    SAMPLE_CSV,
    coverage,
    read_sample,
    score,
    slack,
)
from analysis.types import LaunchClaimRow

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "launch" / "sample.csv"
AT = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
HEADER = ",".join(COLUMNS)


def _claim(axis: str, direction: str, claimed_on: date, strength: str = "exact") -> LaunchClaimRow:
    return LaunchClaimRow("oy:A1", axis, direction, claimed_on, "day", f"{axis}:1", strength, "rule-v1.0", AT)


def test_the_shipped_sample_is_a_header_and_no_rows():
    # #283 work 6: ship it empty. A launch date this issue invented would be scored against itself.
    body = SAMPLE_CSV.read_text(encoding="utf-8").splitlines()
    assert body == [HEADER]
    assert read_sample(SAMPLE_CSV) == []


def test_a_labelled_row_names_the_month_as_the_span_it_is():
    first, second = read_sample(FIXTURE)
    assert first.product_ref == "oy:A1"
    assert first.window == (date(2021, 5, 1), date(2021, 5, 31))
    # December is the wrap the month arithmetic has to survive; January is its neighbour.
    assert second.window == (date(2019, 1, 1), date(2019, 1, 31))


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("oy:A1,b,n,2021-5,page,", "launch_month must be YYYY-MM"),
        ("oy:A1,b,n,,page,", "launch_month must be YYYY-MM"),
        (",,,2021-05,page,", "needs a product_ref"),
        # A row given as brand+name alone used to be accepted here and then dropped in silence by
        # `score`, which keys on `product_ref` -- forty labelled rows would have printed "40
        # labelled products" beside empty axis columns (#283 review, fix-when-touched).
        (",brand,name,2021-05,page,", "needs a product_ref"),
    ],
)
def test_a_row_that_cannot_be_scored_is_refused_rather_than_counted_as_a_miss(
    tmp_path: Path, line: str, reason: str
):
    path = tmp_path / "sample.csv"
    path.write_text(f"{HEADER}\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        read_sample(path)


def test_a_file_without_the_columns_is_refused(tmp_path: Path):
    path = tmp_path / "sample.csv"
    path.write_text("product_ref,launch_month\noy:A1,2021-05\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing column"):
        read_sample(path)


@pytest.mark.parametrize(
    ("direction", "claimed_on", "expected"),
    [
        # A lower bound belongs before the month and leaves that many days of room.
        ("not_before", date(2021, 4, 16), 15),
        ("not_before", date(2021, 5, 1), 0),
        ("not_before", date(2021, 6, 15), -45),
        # An upper bound belongs after it.
        ("not_after", date(2021, 6, 15), 15),
        ("not_after", date(2021, 5, 31), 0),
        ("not_after", date(2021, 4, 16), -45),
        # `at` is scored on whichever side it fails: it claims both bounds at once.
        ("at", date(2021, 5, 10), -21),
    ],
)
def test_a_claim_leaves_room_or_contradicts_the_label(direction, claimed_on, expected):
    window = read_sample(FIXTURE)[0].window
    assert slack(_claim(MFDS, direction, claimed_on), window) == expected


def test_the_score_is_per_axis_and_says_how_far_off_each_one_was():
    sample = read_sample(FIXTURE)
    claims = {
        "oy:A1": [
            _claim(MFDS, "not_before", date(2021, 4, 16)),
            _claim(MFDS, "not_before", date(2021, 6, 15), "partial"),
            _claim(OLD_REVIEW, "not_after", date(2021, 6, 15)),
        ]
    }
    scored = score(sample, claims)
    assert scored[MFDS].claims == 2
    assert scored[MFDS].consistent == 1
    assert scored[MFDS].hit_rate == 0.5
    assert scored[MFDS].median_slack == -15
    assert scored[OLD_REVIEW].hit_rate == 1.0
    assert scored[OLD_REVIEW].in_rule is True
    # The second labelled product has no claim at all, so no axis counts it -- coverage and accuracy
    # are different questions and the report keeps them apart.
    assert scored[MFDS].products == 1


def test_an_axis_the_rule_version_excludes_is_scored_and_marked():
    sample = read_sample(FIXTURE)
    scored = score(sample, {"oy:A1": [_claim(VENDOR_TITLE_TAG, "not_after", date(2021, 6, 1))]})
    assert scored[VENDOR_TITLE_TAG].in_rule is False
    assert scored[VENDOR_TITLE_TAG].hit_rate == 1.0


def test_an_axis_with_no_labelled_product_has_no_rate_rather_than_a_zero():
    assert score(read_sample(FIXTURE), {}) == {}


def test_coverage_needs_no_label_at_all():
    # The half of the report that can be printed today, while the sample is still empty.
    claims = [
        _claim(MFDS, "not_before", date(2021, 4, 16)),
        _claim(MFDS, "not_before", date(2023, 11, 2), "partial"),
        _claim(OLD_REVIEW, "not_after", date(2019, 3, 2)),
    ]
    assert coverage(claims) == {MFDS: (1, 2, 1), OLD_REVIEW: (1, 1, 1)}
