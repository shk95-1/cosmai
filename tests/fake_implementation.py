"""registry.IMPLEMENTATIONS 가 import 만으로 구현체를 등록하는지 보이는 최소 모듈 (#2·#3·#4 의 모양)."""

from __future__ import annotations

from collections.abc import Sequence

from analysis.registry import LabeledRow, register


def predict(rows: Sequence[LabeledRow]) -> Sequence[str]:
    return ["중립"] * len(rows)


register("polarity", "fake-v0", predict)
