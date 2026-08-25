"""등록이 언제 일어나는가 — import 순간인가, load_implementations() 안인가 (#99).

import 부수효과로 등록하면 import 캐시 순서가 곧 레지스트리 상태다: 알파벳순으로 앞선 파일이
`analysis.predictors` 를 이미 올려 두었으면 뒤 파일이 등록 목록을 비워도 등록은 남아 있고, 혼자
돌린 그 파일만 다르게 끝난다(tests/test_cli_eval.py 의 harness_only 가 그렇게 지나쳐졌다).
여기는 그 시점 하나만 재는 자리다 — conftest.py 의 세션 끝 가드는 반대 방향(끝에 기본 등록이
남아 있는가)을 재고, 이 파일은 그 전에 등록이 저절로 생기지 않는가를 잰다.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator

import pytest

from analysis import registry


@pytest.fixture(autouse=True)
def _restore_default_registrations() -> Iterator[None]:
    yield
    registry.load_implementations()


def test_importing_an_implementation_module_does_not_register_on_its_own():
    for task in registry.TASKS:
        registry.unregister(task)
    # reload 는 "이 모듈을 처음 import 하는 프로세스" 를 재현한다 — 캐시된 모듈은 그냥 import 해도
    # 아무 줄도 다시 돌지 않아 검사가 공회전한다.
    for module in registry.IMPLEMENTATIONS:
        importlib.reload(importlib.import_module(module))
    assert [task for task in registry.TASKS if registry.get(task) is not None] == []


def test_load_implementations_registers_every_task_and_repeats_without_a_reload():
    """같은 프로세스에서 두 번 불러도 두 번 다 등록되어야 한다 — import 캐시가 등록을 삼키면
    되돌리는 방법이 importlib.reload 밖에 없어지고, 그 우회가 파일마다 번졌다."""
    for task in registry.TASKS:
        registry.unregister(task)
    registry.load_implementations()
    assert [task for task in registry.TASKS if registry.get(task) is None] == []
