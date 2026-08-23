"""origin: playbook/snippets/test_scope_is_derived.py (service/trend-radar tests/test_scope_is_declared.py:
131-180, the derivation half). scope.json is `collectors/commerce/scope.json`'s copy of each source's
`scope` -- this proves the two cannot drift: same constants, same values, every dataset."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import collectors.commerce.sources  # noqa: F401 -- registers every source
from collectors.commerce.registry import SOURCES

CONSTANT_FOR = {
    "boards": None,  # boards is len(_BOARDS)/len(BOARDS); no single module constant to compare
    "page_size": "PAGE_SIZE",
    "review_pages": "REVIEW_PAGES",
    "review_products": "REVIEW_PRODUCTS",
    "review_page_size": "REVIEW_PAGE_SIZE",
    "product_products": "PRODUCT_PRODUCTS",
    "review_stats_products": "REVIEW_STATS_PRODUCTS",
    "month_term_months": "MONTH_TERM",
    "low_products": "LOW_PRODUCTS",
    "low_asc_pages_max": "LOW_ASC_PAGES_MAX",
    "low_desc_pages": "LOW_DESC_PAGES",
}
CLASSES = [SOURCES[k] for k in sorted(SOURCES)]
SCOPE_JSON = Path(__file__).resolve().parents[3] / "collectors" / "commerce" / "scope.json"


def _pairs(cls):
    module = sys.modules[cls.__module__]
    return [
        (ds, k)
        for ds, knobs in cls.scope.items()
        for k in knobs
        if CONSTANT_FOR.get(k) and hasattr(module, CONSTANT_FOR[k])
    ]


def test_there_is_something_to_check():
    assert CLASSES
    assert sum(len(_pairs(c)) for c in CLASSES) >= 3


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
        assert cls.scope[ds][k] == getattr(module, CONSTANT_FOR[k]), (
            f"{cls.key}.scope[{ds.value}][{k}] was typed out, not derived"
        )


def test_scope_json_is_a_copy_of_every_sources_declared_scope():
    on_disk = json.loads(SCOPE_JSON.read_text(encoding="utf-8"))
    for cls in CLASSES:
        site = on_disk.get(cls.key)
        assert site is not None, f"scope.json has no entry for {cls.key!r}"
        for dataset, knobs in cls.scope.items():
            on_disk_knobs = site.get(dataset.value)
            assert on_disk_knobs is not None, f"scope.json[{cls.key}] has no {dataset.value!r}"
            for name, value in knobs.items():
                assert on_disk_knobs.get(name) == value, (
                    f"scope.json[{cls.key}][{dataset.value}][{name}] "
                    f"= {on_disk_knobs.get(name)!r}, source says {value!r}"
                )
