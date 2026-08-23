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


def collapsed_accuracy(pairs: Sequence[tuple[str, str]], positive: Collection[str]) -> float:
    """라벨을 참/거짓으로 접어 재는 정확도 — product_match 의 strict 와 변형허용이 이것 하나로 갈린다."""
    return _ratio(sum(1 for gold, pred in pairs if (gold in positive) == (pred in positive)), len(pairs))
