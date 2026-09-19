"""The latin boundary guards every alternative on both sides (fork #97).

Ungrouped, `(?<![A-Za-z])SPF|UVA|UVB|PA(?![A-Za-z])` binds the lookbehind to the first term and the lookahead
to the last, so `CAPA` and `SPA day` matched the sunscreen-rating topic: 101 of 382 archive documents.
"""

from __future__ import annotations

import pytest

from analysis.retrieval import topics
from tests.retrieval.conftest import csv_topics

DICTIONARY = csv_topics(3)
MULTI = [entry for entry in DICTIONARY.entries if len(entry["latin"]) > 1]


def _rating_topic() -> str:
    [topic] = [entry["topic"] for entry in DICTIONARY.entries if {"SPF", "PA"} <= set(entry["latin"])]
    return topic


@pytest.mark.parametrize("probe", ["CAPA", "SPA day", "xUVAx", "xPA", "SPFx"])
def test_the_rating_topic_does_not_match_a_term_inside_a_word(probe: str):
    pattern = DICTIONARY.latin(_rating_topic())
    assert pattern is not None
    assert pattern.search(probe) is None


@pytest.mark.parametrize("probe", ["SPF50+ PA++++", "UVA/UVB", "pa rating", "(uvb)"])
def test_the_rating_topic_still_matches_a_standalone_term(probe: str):
    pattern = DICTIONARY.latin(_rating_topic())
    assert pattern is not None
    assert pattern.search(probe) is not None


def test_the_dictionary_has_topics_with_more_than_one_latin_term():
    assert len(MULTI) >= 2


@pytest.mark.parametrize("entry", MULTI, ids=lambda entry: str(len(entry["latin"])))
def test_every_latin_term_of_a_multi_term_topic_is_guarded_on_both_sides(entry: dict):
    pattern = DICTIONARY.latin(entry["topic"])
    assert pattern is not None
    for term in entry["latin"]:
        for inside in (f"x{term}x", f"x{term}", f"{term}x"):
            assert pattern.search(inside) is None, (term, inside)
        assert pattern.search(f"1 {term} 2") is not None, term


def test_the_pattern_the_loader_compiles_is_latin_pattern():
    entry = MULTI[0]
    compiled = DICTIONARY.latin(entry["topic"])
    assert compiled is not None
    assert compiled.pattern == topics.latin_pattern(entry["latin"]).pattern  # pyright: ignore[reportOptionalMemberAccess]
