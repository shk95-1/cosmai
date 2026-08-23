"""origin: service/trend-radar/tests/test_scope_is_declared.py:131-180 (the derivation half only)
reuse: CONSTANT_FOR maps a scope key to the module constant the parser actually uses. The lock file and
CHANGELOG coupling of the original are deliberately left out; the run row stores scope instead.
"""

from __future__ import annotations

import json
import sys

import pytest

from cosmai.collectors.registry import SOURCES

CONSTANT_FOR = {"page_size": "PAGE_SIZE", "review_pages": "REVIEW_PAGES", "review_products": "REVIEW_PRODUCTS"}
CLASSES = [SOURCES[k] for k in sorted(SOURCES)]


def _pairs(cls):
    module = sys.modules[cls.__module__]
    return [(ds, k) for ds, knobs in cls.scope.items() for k in knobs if k in CONSTANT_FOR and hasattr(module, CONSTANT_FOR[k])]


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.key)
def test_scope_is_json_of_ints_keyed_by_dataset(cls):
    assert set(cls.scope) == set(cls.datasets), f"{cls.key}: scope and datasets disagree"
    json.dumps({d.value: dict(v) for d, v in cls.scope.items()})
    for knobs in cls.scope.values():
        assert knobs and all(isinstance(v, int) and not isinstance(v, bool) for v in knobs.values())


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.key)
def test_scope_agrees_with_the_constants_the_parser_uses(cls):
    module = sys.modules[cls.__module__]
    for ds, k in _pairs(cls):
        assert cls.scope[ds][k] == getattr(module, CONSTANT_FOR[k]), f"{cls.key}.scope[{ds.value}][{k}] was typed out, not derived"


def test_the_derivation_check_actually_checked_something():
    assert sum(len(_pairs(c)) for c in CLASSES) >= 3, "no scope value was compared to a constant (0 == 0 is not green)"
