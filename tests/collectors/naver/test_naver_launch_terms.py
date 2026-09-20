"""The term rules of the launch-onset axis (#285): what is a category word, what is a product line,
which products are refused, and when a join is `exact`.

The catalogue is `fixtures/launch_catalogue.json` -- six `needs.product_ref` rows over four brands,
shaped after the seven sunscreens of the 2026-09-20 experiment. The Korean lives in the fixture and
not in this file, which is where `tool/checks/lang` allows a test's data to be.
"""

from __future__ import annotations

import json
from pathlib import Path

from collectors.naver import launch_terms, scope
from collectors.naver.launch_terms import Ref

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CATALOGUE = FIXTURES / "launch_catalogue.json"
TERMS_FIXTURE = FIXTURES / "launch_terms.json"


def _refs() -> list[Ref]:
    raw = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    return [Ref(r["product_ref"], r["brand"], r["name_norm"]) for r in raw["refs"]]


def _by_ref() -> dict[str, Ref]:
    return {r.product_ref: r for r in _refs()}


def test_a_word_carried_by_enough_brands_is_a_category_word():
    generic = launch_terms.generic_tokens(_refs())
    catalogue = _by_ref()
    # The category word ends every name in the fixture, across all four brands.
    category = launch_terms.tokens(catalogue["oy:D1"].name_norm)[-1]
    assert category in generic
    # A line name carried by one brand alone is not generic, however often that brand repeats it.
    line = launch_terms.tokens(catalogue["oy:B1"].name_norm)[1]
    assert line not in generic
    assert scope.LAUNCH_GENERIC_MIN_BRANDS == 3


def test_a_brands_own_name_is_never_a_category_word():
    # Every name in the fixture starts with its brand, so a rule counting tokens without removing
    # the brand would make a four-brand catalogue call nothing generic -- or, on a one-brand
    # catalogue, call the brand itself the category.
    refs = _refs()
    generic = launch_terms.generic_tokens(refs)
    for ref in refs:
        assert ref.brand not in generic


def test_the_line_tokens_are_the_name_without_the_brand_and_the_category_word():
    generic = launch_terms.generic_tokens(_refs())
    ref = _by_ref()["oy:C1"]
    name = launch_terms.tokens(ref.name_norm)
    line = launch_terms.line_tokens(ref, generic)
    assert line == name[1:-1]  # <brand> <line...> <category>
    assert ref.brand not in line


def test_the_candidate_terms_are_the_three_shapes_the_experiment_used():
    generic = launch_terms.generic_tokens(_refs())
    ref = _by_ref()["oy:C1"]
    name = launch_terms.tokens(ref.name_norm)
    brand, category = name[0], name[-1]
    line = " ".join(name[1:-1])
    assert launch_terms.candidate_terms(ref, generic) == (
        f"{brand} {line} {category}",
        f"{line} {category}",
        f"{brand} {line}",
    )


def test_no_product_gets_more_terms_than_the_cap():
    generic = launch_terms.generic_tokens(_refs())
    for ref in _refs():
        assert len(launch_terms.candidate_terms(ref, generic)) <= scope.LAUNCH_MAX_TERMS_PER_PRODUCT


def test_a_product_whose_only_terms_are_its_brand_and_a_category_word_is_refused():
    # Round one of the 2026-09-20 experiment: a brand plus a category word answers for the brand's
    # whole shelf, so its onset is the brand's and not the product's. No terms means no request.
    generic = launch_terms.generic_tokens(_refs())
    assert launch_terms.candidate_terms(_by_ref()["oy:D1"], generic) == ()


def test_a_term_shared_with_another_line_of_the_same_brand_is_a_partial_join():
    # oy:B1's line name is contained in oy:B2's, so every term built from it also names oy:B2 and
    # the series rises when whichever of the pair launched first did. Under `rule-v1.1` row 3 reads
    # `latest_exact`, so a `partial` upper bound cannot declare a product `not_new` on its own --
    # it still reaches row 2 `conflict`, which is the MFDS cross-check this axis is kept for. That
    # is what the word buys, and why it may not be generous.
    generic = launch_terms.generic_tokens(_refs())
    refs = _refs()
    b1 = _by_ref()["oy:B1"]
    assert launch_terms.match_strength(launch_terms.candidate_terms(b1, generic), b1, refs) == "partial"


def test_a_line_no_other_product_of_the_brand_holds_is_an_exact_join():
    generic = launch_terms.generic_tokens(_refs())
    refs = _refs()
    for key in ("oy:A1", "oy:A2", "oy:B2", "oy:C1"):
        ref = _by_ref()[key]
        terms = launch_terms.candidate_terms(ref, generic)
        assert launch_terms.match_strength(terms, ref, refs) == "exact", key


def test_a_line_shared_with_another_brand_does_not_make_the_join_partial():
    # The gate is about variants of the same brand: a different brand's product cannot be the
    # variant an MFDS-style join took, and the terms all carry their own brand anyway.
    generic = launch_terms.generic_tokens(_refs())
    refs = _refs()
    c1 = _by_ref()["oy:C1"]
    other_brand = Ref("oy:Z1", "zzz", f"zzz {' '.join(launch_terms.line_tokens(c1, generic))}")
    terms = launch_terms.candidate_terms(c1, generic)
    assert launch_terms.match_strength(terms, c1, [*refs, other_brand]) == "exact"


def test_the_shipped_term_file_is_empty_and_says_why():
    # #285 ships it empty on purpose: a term is the axis, and an invented one would put a claim
    # about a product's launch into the ledger with nothing behind it.
    raw = json.loads(launch_terms.TERMS_PATH.read_text(encoding="utf-8"))
    assert raw["products"] == {}
    assert raw["generic"] == []
    assert "tool/derive-naver-launch-terms" in raw["_comment"]
    assert launch_terms.load() == {}
    assert launch_terms.generic_extra() == frozenset()


def test_a_filled_term_file_reads_back_as_the_reviewed_terms():
    loaded = launch_terms.load(TERMS_FIXTURE)
    assert set(loaded) == {"oy:A1", "oy:B1", "oy:C1"}
    generic = launch_terms.generic_tokens(_refs())
    assert loaded["oy:C1"] == launch_terms.candidate_terms(_by_ref()["oy:C1"], generic)
    # The refused product is absent from the file, not present with an empty list.
    assert "oy:D1" not in loaded


def test_an_extra_generic_word_is_honoured():
    # A word one brand happens to own today is invisible to the frequency rule, so the file can
    # name it -- and then a product whose line is only that word is refused like any other.
    refs = _refs()
    c1 = _by_ref()["oy:C1"]
    line = launch_terms.line_tokens(c1, launch_terms.generic_tokens(refs))
    widened = launch_terms.generic_tokens(refs, extra=line)
    assert launch_terms.candidate_terms(c1, widened) == ()


def test_an_empty_or_oversized_entry_in_the_file_is_an_error_rather_than_a_silent_truncation(
    tmp_path: Path,
):
    over = tmp_path / "launch_terms.json"
    over.write_text(
        json.dumps({"generic": [], "products": {"oy:A1": ["a", "b", "c", "d"]}}), encoding="utf-8"
    )
    try:
        launch_terms.load(over)
    except ValueError:
        pass
    else:
        raise AssertionError("a reviewed file over the cap must not be truncated in silence")

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"generic": [], "products": {"oy:A1": []}}), encoding="utf-8")
    assert launch_terms.load(empty) == {}, "an empty list is a refusal, not a product to ask about"
