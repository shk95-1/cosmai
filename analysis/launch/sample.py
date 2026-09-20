"""The hand-labelled launch sample and what it scores (#283 work 6, read by #125).

An axis's coverage is a property of the ledger and can be read at any time. Its **accuracy** cannot:
it needs a launch month a person supplies, which is why `eval/launch/launch_sample.csv` ships empty
and this module refuses a row without one rather than inventing a date to score against.

The number this file produces is what decides whether a further axis is worth building, and the one
#282's concern (b) asks for by name: if a containment join were marked `exact`, the row-6 gate would
be worth nothing, and only a hit rate against real launch months can say whether it is.

No database: `tool/launch-coverage` reads the ledger and hands the rows in.
"""

from __future__ import annotations

import csv
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from analysis.launch import EXCLUDED_AXES
from analysis.types import LaunchClaimRow

SAMPLE_CSV = Path(__file__).resolve().parents[2] / "eval" / "launch" / "launch_sample.csv"
COLUMNS = ("product_ref", "brand", "name", "launch_month", "source_of_knowledge", "note")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
LOWER = ("not_before", "at")
UPPER = ("not_after", "at")


@dataclass(frozen=True)
class SampleRow:
    """One labelled product. `product_ref` or `brand`+`name`, and always a month."""

    product_ref: str
    brand: str
    name: str
    launch_month: str
    source_of_knowledge: str = ""
    note: str = ""

    @property
    def window(self) -> tuple[date, date]:
        """The month as the span it names: a claim is scored against the whole of it, because a
        person who says a product launched in June is not saying the 1st."""
        year, month = (int(part) for part in self.launch_month.split("-"))
        first = date(year, month, 1)
        last = date(year + month // 12, month % 12 + 1, 1)
        return first, date.fromordinal(last.toordinal() - 1)


@dataclass(frozen=True)
class AxisScore:
    """What one axis did against the labelled products it reached."""

    axis: str
    in_rule: bool
    products: int  # sample products this axis has at least one claim for
    claims: int
    consistent: int  # claims the label does not contradict
    slack_days: tuple[int, ...]  # how much room the claim leaves; negative = it contradicts

    @property
    def hit_rate(self) -> float | None:
        """None rather than 0.0 when the axis reached nothing: a rate over an empty denominator is
        the shape #125's sample floor is there to refuse."""
        return self.consistent / self.claims if self.claims else None

    @property
    def median_slack(self) -> float | None:
        return statistics.median(self.slack_days) if self.slack_days else None


def read_sample(path: Path = SAMPLE_CSV) -> list[SampleRow]:
    """The file as rows, refusing anything that cannot be scored.

    A row with no month, or with neither a ref nor a brand and name, is an error and not a miss:
    scored as a miss it would make every axis look worse for a gap in the label, which is exactly
    the kind of quiet number this issue exists to avoid.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in COLUMNS if column not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"{path.name} is missing column(s): {', '.join(missing)}")
        rows: list[SampleRow] = []
        for line, raw in enumerate(reader, start=2):
            row = SampleRow(*(str(raw.get(column) or "").strip() for column in COLUMNS))
            if not MONTH.match(row.launch_month):
                raise ValueError(
                    f"{path.name}:{line}: launch_month must be YYYY-MM, not {row.launch_month!r}"
                )
            if not row.product_ref and not (row.brand and row.name):
                raise ValueError(f"{path.name}:{line}: give a product_ref, or a brand and a name")
            rows.append(row)
    return rows


def slack(claim: LaunchClaimRow, window: tuple[date, date]) -> int:
    """How many days of room the claim leaves around the labelled launch, signed.

    A lower bound should sit **before** the launch and an upper bound **after** it, so one number
    says both: positive is room the claim correctly leaves, and negative is the claim contradicting
    the label by that many days. Month precision is not widened here -- an axis of round one claims
    days -- and `at` is scored on whichever side it fails, which is the strictest reading of it.
    """
    first, last = window
    below = (first - claim.claimed_on).days if claim.direction in LOWER else None
    above = (claim.claimed_on - last).days if claim.direction in UPPER else None
    return min(value for value in (below, above) if value is not None)


def score(
    sample: Sequence[SampleRow], claims: Mapping[str, Iterable[LaunchClaimRow]]
) -> dict[str, AxisScore]:
    """Per axis, over the labelled products alone. `claims` is keyed by `product_ref`."""
    windows = {row.product_ref: row.window for row in sample if row.product_ref}
    gathered: dict[str, list[tuple[str, int]]] = {}
    for product_ref, window in windows.items():
        for claim in claims.get(product_ref, ()):
            gathered.setdefault(claim.axis, []).append((product_ref, slack(claim, window)))
    return {
        axis: AxisScore(
            axis=axis,
            in_rule=axis not in EXCLUDED_AXES,
            products=len({product_ref for product_ref, _ in found}),
            claims=len(found),
            consistent=sum(1 for _, room in found if room >= 0),
            slack_days=tuple(room for _, room in found),
        )
        for axis, found in sorted(gathered.items())
    }


def coverage(claims: Iterable[LaunchClaimRow]) -> dict[str, tuple[int, int, int]]:
    """Per axis: products reached, claims written, of them `exact`. The half of the report that
    needs no label at all, and the one that can be printed today."""
    seen: dict[str, tuple[set[str], int, int]] = {}
    for claim in claims:
        products, total, exact = seen.get(claim.axis, (set(), 0, 0))
        products.add(claim.product_ref)
        seen[claim.axis] = (products, total + 1, exact + int(claim.match_strength == "exact"))
    return {axis: (len(products), total, exact) for axis, (products, total, exact) in sorted(seen.items())}
