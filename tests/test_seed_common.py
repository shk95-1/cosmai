"""The CSV -> Python conversions the loaders share. No database."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from db.seed._common import (
    DEFAULT_SLICES,
    REPO_ROOT,
    as_date,
    as_timestamp,
    comment_resolution,
    month_of,
)


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


# Every slice CSV the seed actually opens (the read_csv calls in db/seed/{products,mentions,metrics}.py).
SEED_INPUTS = {
    "slice-suncare": ("product_ref.csv", "need_mention.csv", "metrics.csv", "metrics_population.csv"),
    "slice-p1-category-gap": ("product_denominator.csv", "need_mention.csv", "metrics_by_category.csv"),
    "slice-p2-ranking-dynamics": (
        "product_ref.csv",
        "product_ref_member.csv",
        "product_ref_candidates.csv",
        "rank_daily.csv",
        "price_rank_events.csv",
    ),
    "slice-p3-youtube-brand-link": ("brand_mentions.csv",),
    "slice-p9-wish-mining": ("wish_aggregates.csv", "wish_mention.csv"),
}


def test_the_seed_inputs_live_inside_the_repository():
    """Reading outside the repo makes the path differ per worktree, and the seed tests quietly skip
    (#79)."""
    assert DEFAULT_SLICES == REPO_ROOT / "db" / "seed" / "data"
    missing = [
        f"{d}/{name}"
        for d, names in SEED_INPUTS.items()
        for name in names
        if not (DEFAULT_SLICES / d / name).is_file()
    ]
    assert missing == []


def test_no_seed_or_test_module_builds_a_path_out_of_the_repository():
    """#79's completion bar: neither db/ nor tests/ has a path fragment pointing at the out-of-repo
    slice tree anywhere. This searches quote-and-all so it catches only a path fragment, not prose
    quoting one (`architect/slice-suncare/README.md`)."""
    here = Path(__file__).resolve()
    named = [
        str(p.relative_to(REPO_ROOT))
        for base in ("db", "tests")
        for p in sorted((REPO_ROOT / base).rglob("*.py"))
        if p != here and '"architect"' in p.read_text(encoding="utf-8")
    ]
    assert named == []
