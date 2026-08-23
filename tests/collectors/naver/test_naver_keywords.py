"""keywords.json's shape and the brief's own exclusion rule (architect/slice-suncare/README.md §C:
product-exploration queries like '순한 선크림' inflate a group and were dropped for v2)."""

from __future__ import annotations

from collectors.naver import keywords

EXCLUDED_TERMS = ("순한 선크림", "백탁없는 선크림")


def test_seonbeullok_has_the_five_complaint_groups_in_priority_order():
    groups = keywords.groups("선블록")
    assert list(groups) == ["밀림", "눈시림", "백탁", "따가움", "건조"]


def test_no_group_carries_a_product_exploration_query():
    for group_terms in keywords.groups("선블록").values():
        for term in group_terms:
            assert term not in EXCLUDED_TERMS, f"{term!r} is a product-exploration query, not a complaint"


def test_every_group_has_at_least_one_term():
    for group, terms in keywords.groups("선블록").items():
        assert terms, f"group {group!r} names no search term"


def test_queries_flattens_every_category_group_term_triple():
    qs = keywords.queries()
    total_terms = sum(len(terms) for terms in keywords.groups("선블록").values())
    assert len(qs) == total_terms
    assert all(q.category == "선블록" for q in qs)


def test_load_ignores_underscore_prefixed_keys():
    loaded = keywords.load()
    assert "_comment" not in loaded
    assert "선블록" in loaded
