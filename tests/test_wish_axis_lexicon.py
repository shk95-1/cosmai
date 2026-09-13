"""The two wish axes of `wish_mention` (#124): what the seed puts in `entity_lexicon` under
`kind='format'`·`'attribute'`, and the boundary rule each kind matches by.

Every Korean string here is read off the seed files -- `tool/checks/lang` bars a Hangul literal in a
test, and a fixture copy of the vocabulary would drift away from the file the seed loads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis.lexicon import compile_lexicon
from analysis.types import EntitySurface
from db.seed.lexicon import WISH_AXIS_FILES, wish_axis_rows

EVAL = Path(__file__).resolve().parents[1] / "eval"
# Escaped codepoints: tool/checks/lang bars a staged Hangul character outside the data files.
HANGUL_FIRST, HANGUL_LAST = "\uac00", "\ud7a3"


@pytest.fixture(scope="module")
def rows() -> list[EntitySurface]:
    return [EntitySurface(*row[:5]) for row in wish_axis_rows(EVAL)]


@pytest.fixture(scope="module")
def patterns(rows: list[EntitySurface]) -> dict[str, tuple[tuple[str, object], ...]]:
    lexicon = compile_lexicon(rows, 1)
    return {"format": lexicon.format_patterns, "attribute": lexicon.attribute_patterns}


def answers(patterns: dict, kind: str, text: str) -> set[str]:
    return {name for name, rx in patterns[kind] for _ in [0] if rx.search(text)}


def syllable(rows: list[EntitySurface]) -> str:
    """One Hangul syllable lifted out of the seed, to stand in front of a surface as a compound would."""
    for row in rows:
        if HANGUL_FIRST <= row.surface[0] <= HANGUL_LAST:
            return row.surface[0]
    raise AssertionError("the seed carries no Hangul surface")


def test_both_kinds_are_seeded(rows: list[EntitySurface]) -> None:
    by_kind = {kind: [r for r in rows if r.kind == kind] for kind in WISH_AXIS_FILES}
    assert all(by_kind[kind] for kind in WISH_AXIS_FILES), "an empty axis leaves the panel blank"
    # UNIQUE (kind, surface, version) carries no canonical, so a repeated surface is dropped at load
    # without an error (formats.md) -- the file has to make the choice instead.
    for kind, kind_rows in by_kind.items():
        surfaces = [r.surface for r in kind_rows]
        assert len(surfaces) == len(set(surfaces)), f"{kind} gives one surface to two canonicals"


def test_every_surface_answers_its_own_canonical(rows, patterns) -> None:
    for row in rows:
        assert row.canonical in answers(patterns, row.kind, row.surface), row.surface


def test_a_format_surface_does_not_answer_from_inside_a_longer_word(rows, patterns) -> None:
    """A format surface names the product the sentence is about, so it has to stand as a word: the
    seed's own longer surfaces are the counter-examples -- "cream" must not answer for
    "sunscreen", which is written as a compound ending in it."""
    formats = [r for r in rows if r.kind == "format"]
    listed = {r.surface for r in formats}
    pairs = [
        (short, long)
        for short in formats
        for long in formats
        if short.canonical != long.canonical
        and long.surface.endswith(short.surface)
        and long.surface != short.surface
    ]
    assert pairs, "the seed no longer contains a shorter surface inside a longer one"
    for short, long in pairs:
        assert short.canonical not in answers(patterns, "format", long.surface), long.surface
    prefix = syllable(formats)
    for row in formats:
        if prefix + row.surface in listed:
            continue
        assert row.canonical not in answers(patterns, "format", prefix + row.surface), row.surface


def test_an_attribute_surface_still_answers_on_a_compound_tail(rows, patterns) -> None:
    """An attribute surface is a property riding on the tail of a compound, which is the whole of the
    texture axis: "... in a stick too" is written as one word with the particle glued on."""
    attributes = [r for r in rows if r.kind == "attribute"]
    prefix = syllable([r for r in rows if r.kind == "format"])
    for row in attributes:
        assert row.canonical in answers(patterns, "attribute", prefix + row.surface), row.surface
