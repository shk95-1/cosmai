"""Score computation. Pure functions that know neither the DB nor the CLI, so they are checked with fixed
vectors."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ClassScore:
    label: str
    support: int  # how many of this label there are in gold
    predicted: int
    hit: int
    precision: float
    recall: float


@dataclass(frozen=True)
class Scores:
    n: int
    accuracy: float
    classes: tuple[ClassScore, ...]


def _ratio(hit: int, total: int) -> float:
    """A denominator of 0 is 0.0 -- the precision of a label nobody predicted has to be 0 rather than a blank
    in the table for it to be comparable."""
    return hit / total if total else 0.0


def score(pairs: Sequence[tuple[str, str]]) -> Scores:
    """pairs = (gold, pred)."""
    labels = sorted({label for pair in pairs for label in pair})
    classes = tuple(
        ClassScore(
            label=label,
            support=sum(1 for gold, _ in pairs if gold == label),
            predicted=sum(1 for _, pred in pairs if pred == label),
            hit=sum(1 for gold, pred in pairs if gold == pred == label),
            precision=_ratio(
                sum(1 for gold, pred in pairs if gold == pred == label),
                sum(1 for _, pred in pairs if pred == label),
            ),
            recall=_ratio(
                sum(1 for gold, pred in pairs if gold == pred == label),
                sum(1 for gold, _ in pairs if gold == label),
            ),
        )
        for label in labels
    )
    return Scores(
        n=len(pairs),
        accuracy=_ratio(sum(1 for gold, pred in pairs if gold == pred), len(pairs)),
        classes=classes,
    )


def precision_over(
    pairs: Sequence[tuple[str, str]], accepted: Collection[str], correct: Collection[str]
) -> float:
    """Precision counting only the rows predicted accepted in the denominator -- the denominator is the
    accepted set, not the 40 rows (interfaces.md)."""
    chosen = [gold for gold, pred in pairs if pred in accepted]
    return _ratio(sum(1 for gold in chosen if gold in correct), len(chosen))
