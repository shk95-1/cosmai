"""registry.load_implementations() 가 구현체를 꽂는지 보이는 최소 모듈 (#2·#3·#4 의 모양)."""

from __future__ import annotations

from collections.abc import Sequence

from analysis.registry import LabeledRow, register


def predict(rows: Sequence[LabeledRow]) -> Sequence[str]:
    return ["중립"] * len(rows)


def register_implementations() -> None:
    register("polarity", "fake-v0", predict)
