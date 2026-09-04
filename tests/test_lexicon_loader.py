"""The dictionary loader: it reads one version and returns a compiled Lexicon/AspectLexicon
(contracts/formats.md §ruleset)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from analysis.lexicon import load_aspects, load_lexicon
from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres


@pytest.fixture
def seeded(needs_runtime_url: str) -> Iterator[object]:
    seed.run_all(needs_runtime_url, only=("lexicon",))
    with connect(needs_runtime_url) as conn:
        yield conn


def test_the_active_entity_version_loads_every_surface(seeded):
    lex = load_lexicon(seeded)
    assert lex.version == 1
    assert len(lex.surfaces) == 992
    assert lex.surface_to_canonical["3CE"] == "3CE"
    assert lex.surface_to_canonical["무기자차"] == "ZINC_OXIDE"


def test_the_surface_regex_allows_a_particle_and_skips_stopped_brands(seeded):
    lex = load_lexicon(seeded)
    hits = [m.group(1) for m in lex.surface_re.finditer("어제 산 3CE 틴트랑 라네즈는 좋다")]
    assert hits == ["3CE", "라네즈"]
    # The stop tier takes the surface out of the regex altogether (slice-p3 link_brands.py --stop).
    assert "포인트" in lex.stop
    assert not lex.surface_re.search("포인트 컬러가 예쁘다")
    assert "카이" in lex.cooc_required
    assert lex.cooc_window == 25
    assert lex.product_word_re.search("선크림")


def test_an_aspect_ruleset_reads_its_own_rows_plus_shared(seeded):
    suncare = load_aspects(seeded, "suncare-v2.2")
    assert suncare.version == 1
    assert len(suncare.patterns) == 15
    assert {p.category for p in suncare.patterns} == {"선블록"}
    assert len(load_aspects(seeded, "p1-v2.2").patterns) == 57


def test_patterns_come_back_in_priority_then_id_order(seeded):
    p1 = load_aspects(seeded, "p1-v2.2")
    priorities = [p.priority for p in p1.patterns]
    assert priorities == sorted(priorities)
    assert [p.scope for p in p1.patterns[:35]] == ["category"] * 35
    # 쌍둥이(중립 명사)는 같은 이름으로 바로 뒤에 온다 (formats.md).
    generic = [p for p in p1.patterns if p.scope == "generic"]
    assert (generic[0].aspect, generic[0].is_neutral_noun) == ("효과없음", False)
    assert (generic[1].aspect, generic[1].is_neutral_noun) == ("효과없음", True)


def test_a_category_pattern_hides_the_generic_of_the_same_name(seeded):
    p1 = load_aspects(seeded, "p1-v2.2")
    cream = p1.for_category("크림")
    assert [p.scope for p in cream if p.aspect == "눈시림"] == ["category"]
    assert len(cream) == 20
    # With no category known, only generic is looked at.
    assert {p.scope for p in p1.for_category(None)} == {"generic"}


def test_the_complaint_marker_regex_is_the_discourse_markers_plus_that_category(seeded):
    p1 = load_aspects(seeded, "p1-v2.2")
    marker = p1.complaint_marker_re("선블록")
    assert marker.search("근데 좀 아쉬워요")
    assert marker.search("백탁이 심해요")
    assert not p1.complaint_marker_re(None).search("백탁이 심해요")
    assert p1.wish_marker_re.search("나왔으면 좋겠어요")
