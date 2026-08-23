"""점수 계산. DB 도 CLI 도 모르는 순수 함수라 고정 벡터로 검사한다."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ClassScore:
    label: str
    support: int  # gold 에서 이 라벨의 개수
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
    """분모 0 은 0.0 — 아무도 예측하지 않은 라벨의 정밀도는 표에서 빈칸이 아니라 0 이어야 비교가 된다."""
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
    """예측이 accepted 인 행만 분모로 세는 정밀도 — 분모가 40행이 아니라 채택 집합이다 (interfaces.md)."""
    chosen = [gold for gold, pred in pairs if pred in accepted]
    return _ratio(sum(1 for gold in chosen if gold in correct), len(chosen))
