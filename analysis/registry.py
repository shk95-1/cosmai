"""task → 구현체. 후속 유닛(#2·#3·#4·#6)이 자기 모듈에서 register() 로 자기 구현을 꽂는다."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from analysis.types import LabeledRow, Polarity, Predictor

__all__ = [
    "IMPLEMENTATIONS",
    "Implementation",
    "LabeledRow",
    "Predictor",
    "TASKS",
    "build",
    "get",
    "is_paid",
    "open_classifier",
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
# 외부에 돈을 내는 구현. cli 는 이 표시만 보고 --split 을 요구한다 (홀드아웃이 먼저 도는 것을 막는다).
_PAID: set[tuple[str, str]] = set()
# 같은 `--impl` 스펙이 여는 *단계용* 판정자. eval 은 라벨만 받는 Predictor 로 충분하지만 analyze 는
# aspect·reason 까지 필요해 Polarity 그 자체를 쓴다 — 원장 커넥션을 열고 닫아야 해서 컨텍스트 매니저다.
_CLASSIFIERS: dict[str, dict[str, Callable[[str], AbstractContextManager[Polarity]]]] = {}


def register(task: str, version: str, predict: Predictor) -> None:
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}; expected one of {', '.join(TASKS)}")
    _REGISTRY[task] = Implementation(version=version, predict=predict)


def register_factory(
    task: str,
    name: str,
    factory: Callable[[str], Implementation],
    *,
    paid: bool = False,
    classifier: Callable[[str], AbstractContextManager[Polarity]] | None = None,
) -> None:
    """paid=True 는 "이 구현은 외부에 돈을 낸다" 는 표시다 — cli 가 그것만 보고 규율을 강제한다.

    classifier 는 같은 이름·같은 스펙 문법으로 `analyze` 가 여는 단계 판정자다 (없으면 eval 전용).
    """
    if task not in TASKS:
        raise ValueError(f"unknown task {task!r}; expected one of {', '.join(TASKS)}")
    _FACTORIES[(task, name)] = factory
    if paid:
        _PAID.add((task, name))
    if classifier is not None:
        _CLASSIFIERS.setdefault(task, {})[name] = classifier


def open_classifier(task: str, spec: str) -> AbstractContextManager[Polarity]:
    """`--impl <name>:<argument>` → 단계가 쓸 판정자. 여는 것은 커넥션뿐, 첫 호출은 classify 가 낸다."""
    name, _, argument = spec.partition(":")
    factory = _CLASSIFIERS.get(task, {}).get(name)
    if factory is None:
        known = ", ".join(sorted(_CLASSIFIERS.get(task, {}))) or "(none)"
        raise LookupError(f"no stage classifier for --impl {spec!r} on {task}; registered: {known}")
    return factory(argument)


def is_paid(task: str, spec: str) -> bool:
    return (task, spec.partition(":")[0]) in _PAID


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
