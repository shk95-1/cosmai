"""task → 구현체. 후속 유닛(#2·#3·#4·#6)이 자기 모듈에서 register() 로 자기 구현을 꽂는다."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass

from analysis.types import LabeledRow, Predictor

__all__ = [
    "IMPLEMENTATIONS",
    "Implementation",
    "LabeledRow",
    "Predictor",
    "TASKS",
    "build",
    "get",
    "register",
    "register_factory",
]

# contracts/entrypoints.md. B11: aspect 는 평가셋도 기준선도 0행이라 빠졌다.
TASKS = ("polarity", "wish_class", "brand_link", "product_match")

# 구현체를 등록하는 모듈 경로. 유닛은 여기에 자기 줄 하나만 더한다 — cli.py 에 import 를 끼워 넣으면
# 네 유닛이 같은 줄에서 충돌하고, 그 충돌을 없애는 것이 이 이슈의 목적이었다.
IMPLEMENTATIONS: tuple[str, ...] = (
    "analysis.linker.evaluators",
    "analysis.predictors",
    "analysis.polarity.predictor",
)


@dataclass(frozen=True)
class Implementation:
    version: str
    predict: Predictor


_REGISTRY: dict[str, Implementation] = {}
# `--impl <name>:<argument>` 로 고르는 구현체. 모델 이름이 인자라 미리 등록해 둘 수 없다 (#6 의 llm:<model>).
_FACTORIES: dict[tuple[str, str], Callable[[str], Implementation]] = {}


def register(task: str, version: str, predict: Predictor) -> None:
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}; expected one of {', '.join(TASKS)}")
    _REGISTRY[task] = Implementation(version=version, predict=predict)


def register_factory(task: str, name: str, factory: Callable[[str], Implementation]) -> None:
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}; expected one of {', '.join(TASKS)}")
    _FACTORIES[(task, name)] = factory


def build(task: str, spec: str) -> Implementation:
    """이름만 만든다 — 연결도 첫 API 호출도 predict 가 불릴 때 일어난다."""
    name, _, argument = spec.partition(":")
    factory = _FACTORIES.get((task, name))
    if factory is None:
        known = ", ".join(sorted(n for t, n in _FACTORIES if t == task)) or "(none)"
        raise LookupError(f"no implementation factory for --impl {spec!r} on {task}; registered: {known}")
    return factory(argument)


def load_implementations() -> None:
    """등록 모듈을 한 번씩 import 한다 — register() 는 그 import 의 부작용으로 돈다."""
    for module in IMPLEMENTATIONS:
        importlib.import_module(module)


def get(task: str) -> Implementation | None:
    return _REGISTRY.get(task)


def unregister(task: str) -> None:
    _REGISTRY.pop(task, None)
