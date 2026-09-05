"""The code version of the baseline table of contracts/interfaces.md. Drift from the table and
tests/test_baselines.py catches it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal


@dataclass(frozen=True)
class Check:
    metric: str  # evaluate 가 내는 이름: acc | P:<라벨> | R:<라벨> | strict | 변형허용
    # Exactly the characters written in the contract table -- the decimal places are the resolution of this
    # gate, so a float cannot hold them.
    threshold: Decimal


@dataclass(frozen=True)
class EvalSet:
    """One row of the baseline table = one group to pick out of labeled_set."""

    task: str
    name: str
    split: str
    checks: tuple[Check, ...]
    ref_prefix: str = ""
    extra_key: str = ""  # a key of labeled_set.extra -- picks a set a ref prefix cannot separate
    extra_value: str = ""


# interfaces.md §Baselines — the same as the table down to the row order.
BASELINES: tuple[EvalSet, ...] = (
    EvalSet(
        task="polarity",
        name="sun holdout 100",
        split="holdout",
        checks=(Check("acc", Decimal(".77")), Check("P:불만", Decimal(".89"))),
        ref_prefix="sun:",
    ),
    EvalSet(
        task="polarity",
        name="p1 blind40",
        split="holdout",
        checks=(Check("acc", Decimal(".47")), Check("P:불만", Decimal(".67"))),
        ref_prefix="p1:",
    ),
    # The ref of wish is the comment_id alone (formats.md), so holdout60 and blind60_v2 cannot be told apart
    # by prefix -- the seed puts the set name into labeled_set.extra.set.
    EvalSet(
        task="wish_class",
        name="P9 blind60_v2",
        split="holdout",
        checks=(Check("P:a", Decimal(".90")),),
        extra_key="set",
        extra_value="blind60_v2",
    ),
    # brand_link has both 120-row samples as one whole set, so there is nothing to pick.
    EvalSet(task="brand_link", name="P3 120", split="holdout", checks=(Check("P:OK", Decimal(".97")),)),
    EvalSet(
        task="product_match",
        name="P2 blind 40",
        split="holdout",
        checks=(Check("strict", Decimal(".769")),),
        ref_prefix="v2:",
    ),
)


# Observation sets with no baseline. The table has only the two holdout lines, but the first pass has to see
# the tune-set scores as well (what the same rules were fitted to), and for wish the gap against the two
# non-blind sets is the size of the overfitting. BASELINES, the copy of the baseline table, is not touched --
# tests/test_baselines.py compares it with the table down to the row order.
OBSERVED: tuple[EvalSet, ...] = (
    EvalSet(task="polarity", name="sun tune 200", split="tune", checks=(), ref_prefix="sun:"),
    EvalSet(task="polarity", name="p1 crosscat 60", split="tune", checks=(), ref_prefix="p1:"),
    EvalSet(
        task="wish_class",
        name="P9 tune100",
        split="tune",
        checks=(),
        extra_key="set",
        extra_value="tune100",
    ),
    EvalSet(
        task="wish_class",
        name="P9 holdout60",
        split="holdout",
        checks=(),
        extra_key="set",
        extra_value="holdout60",
    ),
)


# interfaces.md §Rule measurement — the adoption condition is the floor the contract demands, and replacing
# an implementation has to beat this number the rules actually produced. When the two tables part,
# tests/test_baselines.py catches it.
RULE_MEASURED: Mapping[str, Mapping[str, Mapping[str, Decimal]]] = {
    "polarity": {
        "sun holdout 100": {"acc": Decimal(".870"), "P:불만": Decimal(".915")},
        "p1 blind40": {"acc": Decimal(".475"), "P:불만": Decimal(".667")},
    }
}


def meets(metric: float, threshold: Decimal) -> bool:
    """It rounds the metric to the decimal places written at the threshold and then compares. The numbers in
    the table are the rounded form of the raw values the rules produced, so measuring the raw value literally
    makes the rules that built the baseline lose to it (2/3 = .6667 < .67). The decimal places are the
    resolution: `.67` is two places so .6667 passes, and `.769` is three places and that much finer. The
    comparison of the gate is this one place, so the baseline table and the measured-rules table use the same
    ruler."""
    # The same rounding as the f"{metric:.3f}" the harness prints -- the value copied into the table is the
    # pass line.
    return Decimal(metric).quantize(threshold, rounding=ROUND_HALF_EVEN) >= threshold


def adoption_misses(task: str, scores: Mapping[str, Mapping[str, float]]) -> tuple[str, ...]:
    """The cells that fell short of the rules. It has to be an empty tuple for a replacement -- a run with a
    whole set missing is an error, not a judgement."""
    wanted = RULE_MEASURED.get(task, {})
    absent = [name for name in wanted if name not in scores]
    if absent:
        raise LookupError(f"{task}: no adoption verdict without {', '.join(absent)} — run --split holdout")
    return tuple(
        f"{name}: {metric} {scores[name].get(metric, 0.0):.3f} < rule {want}"
        for name, wants in wanted.items()
        for metric, want in wants.items()
        if not meets(scores[name].get(metric, 0.0), want)
    )


def for_task(task: str) -> tuple[EvalSet, ...]:
    """The baseline sets come first -- so the adoption decision is not read after the observation sets."""
    return tuple(b for b in BASELINES + OBSERVED if b.task == task)
