"""task → 구현체. 후속 유닛(#2·#3·#4)이 자기 모듈에서 register() 로 자기 구현을 꽂는다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

# contracts/entrypoints.md. B11: aspect 는 평가셋도 기준선도 0행이라 빠졌다.
TASKS = ("polarity", "wish_class", "brand_link", "product_match")


@dataclass(frozen=True)
class LabeledRow:
    task: str
    ref: str
    split: str
    gold: str
    text: str
    extra: dict[str, object]


class Predictor(Protocol):
    def __call__(self, rows: Sequence[LabeledRow]) -> Sequence[str]: ...


@dataclass(frozen=True)
class Implementation:
    version: str
    predict: Predictor


_REGISTRY: dict[str, Implementation] = {}


def register(task: str, version: str, predict: Predictor) -> None:
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}; expected one of {', '.join(TASKS)}")
    _REGISTRY[task] = Implementation(version=version, predict=predict)


def get(task: str) -> Implementation | None:
    return _REGISTRY.get(task)


def unregister(task: str) -> None:
    _REGISTRY.pop(task, None)
