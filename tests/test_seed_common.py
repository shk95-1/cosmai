"""The CSV -> Python conversions the loaders share. No database."""

from __future__ import annotations

from datetime import UTC, date, datetime

from db.seed._common import as_date, as_timestamp, comment_resolution, month_of


def test_a_naive_timestamp_is_read_as_utc():
    """price_rank_events.csv writes datetime.utcfromtimestamp(), i.e. UTC without the offset."""
    parsed = as_timestamp("2026-08-21T03:00:00")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == UTC.utcoffset(None)
    assert parsed == datetime(2026, 8, 21, 3, 0, tzinfo=UTC)


def test_an_offset_in_the_value_is_kept():
    assert as_timestamp("2026-07-01 11:21:03+00") == datetime(2026, 7, 1, 11, 21, 3, tzinfo=UTC)
    assert as_timestamp("2026-07-01T20:21:03+09:00") == datetime(2026, 7, 1, 11, 21, 3, tzinfo=UTC)


def test_as_date_takes_the_date_off_a_timestamp():
    assert as_date("2026-07-01 11:21:03+00") == date(2026, 7, 1)


def test_month_and_comment_resolution_follow_formats_md():
    assert month_of(date(2026, 3, 9)) == "2026-03"
    assert comment_resolution(date(2025, 9, 1)) == "month"
    assert comment_resolution(date(2025, 8, 31)) == "year"
