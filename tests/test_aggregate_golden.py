"""Three seed runs are kept as the golden set and this measures what RuleAggregator emits differently from
the same input (#4, first pass)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pytest

from analysis.aggregate import WISH_SCOPES, RuleAggregator
from analysis.aggregate.pipeline import load_denominators, load_needs, load_wishes
from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

TOLERANCE = 0.01
SAMPLES = 6

# population_share_pct is left out: the seed's p1 value is an approximation from the collection sample rather
# than the contract formula (interfaces.md B7).
NEED_COLUMNS = (
    "neg", "pos", "yt_neg", "yt_pos", "unresolved", "unresolved_new", "low_share", "low_mentioning",
    "denom_low", "denom_site", "strength_mean", "strength_low_rating_ratio", "persist_months",
    "persist_months_total", "persist_products", "persist_products_total", "aspect_scope",
)  # fmt: skip
WISH_COLUMNS = (
    "mentions", "channels", "videos", "months_present", "first_month", "last_month", "like_sum",
    "max_like", "example",
)  # fmt: skip

# Measured on the first pass (2026-08-24, remeasured after 005 was applied). Fewer means the second pass moved
# forward, more is a regression.
EXPECTED: Mapping[str, Mapping[str, int]] = {
    # unresolved_new needs the product's first_seen, which the Aggregator is not given (see the report).
    "suncare": {"missing": 0, "extra": 0, "unresolved_new": 15},
    # Before 005, neg/pos/persist_* were off by 9 rows each as well: the 548 reviews p1 re-extracted were
    # absorbed into slice-suncare rows under the old natural key UNIQUE (src, ref, need_key, sentence) and so
    # were not in the input. With those 548 back, those four become 0 and only low_* is left — the golden set
    # counting neutral-polarity mentions that are not stored in need_mention is a second difference, one that
    # has nothing to do with 005.
    "p1": {"missing": 0, "extra": 0, "low_mentioning": 86, "low_share": 61},
    # The seed does not fill wish_mention.channel_id — the channel count does not exist in the input.
    "p9": {"missing": 0, "extra": 0, "channels": 601},
}


def _golden(cur: Any, table: str, keys: str, columns: Sequence[str], note: str, extra: str = "") -> dict:
    cur.execute(
        f"SELECT {keys}, {', '.join(columns)} FROM {table} "  # noqa: S608 - the columns are module constants
        f"WHERE run_id = (SELECT run_id FROM analysis_run WHERE note = %s) {extra}",
        (note,),
    )
    width = len(keys.split(","))
    return {tuple(r[:width]): dict(zip(columns, r[width:], strict=True)) for r in cur.fetchall()}


def _same(golden: Any, got: Any) -> tuple[bool, float]:
    if isinstance(golden, str) or golden is None or got is None:
        return golden == got, 0.0
    error = abs(float(golden) - float(got if got is not None else 0))
    return error <= TOLERANCE, error


def _measure(name: str, golden: dict, got: dict, columns: Sequence[str]) -> tuple[dict, list[str]]:
    counts = {"missing": len(set(golden) - set(got)), "extra": len(set(got) - set(golden))}
    worst: dict[str, float] = {}
    samples: list[str] = []
    for key in sorted(set(golden) & set(got), key=str):
        for column in columns:
            expected = golden[key][column]
            if expected is None:  # a cell the golden set did not measure is not compared
                continue
            ok, error = _same(expected, got[key][column])
            if ok:
                continue
            counts[column] = counts.get(column, 0) + 1
            worst[column] = max(worst.get(column, 0.0), error)
            if len(samples) < SAMPLES:
                samples.append(f"{name} {key} {column}: golden {expected!r} != {got[key][column]!r}")
    cells = sum(1 for k in set(golden) & set(got) for c in columns if golden[k][c] is not None)
    hit = cells - sum(v for c, v in counts.items() if c not in ("missing", "extra"))
    samples.append(
        f"{name}: rows {len(golden)} golden / {len(got)} computed, cells {hit}/{cells} "
        f"({hit / cells:.1%}), worst { {c: round(e, 4) for c, e in sorted(worst.items())} }"
    )
    samples.append(f"{name}: 어긋난 행 수 {dict(sorted(counts.items()))}")
    return counts, samples


def _rows(rows: Iterable[Any], keys: Sequence[str], columns: Sequence[str]) -> dict:
    return {tuple(getattr(r, k) for k in keys): {c: getattr(r, c) for c in columns} for r in rows}


def _category_sums(rows: Iterable[Any]) -> list[Any]:
    """What the golden set measures is the category total row — a product-axis row (#41) writes the same
    (scope, need_key) again, so without filtering it out the keys collide and it overwrites the total row.
    The query on the seed side reads only product_ref = '' as well."""
    return [r for r in rows if r.product_ref == "" and r.month == ""]


def test_the_aggregator_is_measured_against_the_three_seed_goldens(needs_runtime_url: str):
    seed.run_all(needs_runtime_url)
    aggregator = RuleAggregator()
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        denominators = load_denominators(cur)
        suncare = load_needs(cur, ("slice-suncare",))
        # slice-p1 aggregated only oliveyoung, which has a denominator; the same category name from another
        # site is not in the golden set.
        p1 = [m for m in load_needs(cur, ("slice-p1",)) if m.site == "oliveyoung"]
        wishes = load_wishes(cur, ("slice-p9",))

        goldens = {
            "suncare": (
                _golden(cur, "metrics_need", "scope, need_key", NEED_COLUMNS, "seed:slice-suncare",
                        "AND product_ref = '' AND month = ''"),
                _rows(_category_sums(aggregator.need_metrics(suncare, [], "선블록")),
                      ("scope", "need_key"), NEED_COLUMNS),
            ),
            "p1": (
                _golden(cur, "metrics_need", "scope, need_key", NEED_COLUMNS, "seed:slice-p1"),
                _rows(
                    _category_sums(
                        r for s in {d.category for d in denominators if d.category}
                        for r in aggregator.need_metrics(p1, denominators, s)
                    ),
                    ("scope", "need_key"), NEED_COLUMNS,
                ),
            ),
            "p9": (
                _golden(cur, "metrics_wish", "scope, format, attribute, brand", WISH_COLUMNS,
                        "seed:slice-p9"),
                _rows(
                    [r for s in WISH_SCOPES for r in aggregator.wish_metrics(wishes, s)],
                    ("scope", "format", "attribute", "brand"), WISH_COLUMNS,
                ),
            ),
        }  # fmt: skip

    measured: dict[str, Mapping[str, int]] = {}
    report: list[str] = []
    for name, (golden, got) in goldens.items():
        columns = WISH_COLUMNS if name == "p9" else NEED_COLUMNS
        counts, samples = _measure(name, golden, got, columns)
        measured[name] = counts
        report += samples
    print("\n".join(report))
    assert measured == EXPECTED, "\n".join(report)
