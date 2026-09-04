"""BM25 tokenization, expansion and scoring. The demo() assertions of slices/ydc/bm25.py were carried over.

토큰화가 조용히 깨지는 것이 이 유닛의 주된 고장 모드다 -- 사전이 안 얹히면 `백탁` 이
`백`+`탁` 으로 갈리고, 태그 접미(`VA-I`)를 놓치면 서술어 질의의 토큰이 0개가 된다.
Neither raises an exception and only the ranking goes wrong, so it is pinned down here."""

from __future__ import annotations

from pathlib import Path

from analysis.retrieval import bm25
from analysis.retrieval.bm25 import (
    DICTIONARIES,
    Index,
    expand,
    expand_words,
    is_korean,
    tokenize,
    topic_words,
)


def test_the_kiwi_dictionaries_ship_with_the_package():
    # Without the dictionary an ingredient name splits into morphemes and ingredient search goes empty.
    for path in DICTIONARIES:
        assert path.exists(), path


def test_the_dictionaries_read_at_runtime_are_the_packaged_copies_not_the_slice():
    """A copy with the same md5 sat in analysis/slices/ydc/seeds/ and made the canonical copy look like two
    (#9 deleted it). That the path the tokenizer reads and the path the cache signature hashes are both the
    package side is nailed down here -- the day the copy comes back, "it is a duplicate, let us merge them"
    picking that side makes a read-only file canonical quietly (#18 M15)."""
    package = Path(bm25.__file__).resolve().parent
    for path in (*DICTIONARIES, *bm25.TOKENIZER_INPUTS):
        assert path.resolve().is_relative_to(package), path


def test_is_korean_needs_more_than_five_percent_hangul():
    assert is_korean("하얗게 떠서 싫다")
    assert not is_korean("sunscreen review")
    assert not is_korean("")


def test_a_latin_document_tokenizes_without_kiwi():
    assert tokenize("SPF50+ sunscreen") == ["spf50+", "sunscreen"]


def test_spf_keeps_its_plus_as_one_token():
    # Split into `spf` + `50`, every search for a protection factor goes wrong.
    assert "spf50+" in tokenize("이 제품은 SPF50+ 입니다")


def test_a_predicate_query_yields_tokens():
    # Without stripping the tag suffix (`VA-I`) this comes back an empty list.
    assert tokenize("하얗게 떠서 싫다")


def test_an_ingredient_name_stays_one_token():
    assert "에칠헥실트리아존" in tokenize("에칠헥실트리아존 함유")


def test_registered_words_and_expansion_words_are_different_sets():
    # Registration says "Kiwi splits it, please keep it together"; expansion says "it comes back as one word
    # but there is an alias inside it". Mixing the two breaks one of them.
    assert set(topic_words()) != set(expand_words())
    assert set(topic_words()) <= set(expand_words())


def test_expansion_is_symmetric_between_query_and_document():
    assert "끈적" in expand("끈적임")
    assert expand("끈적임")[0] == "끈적임"
    assert expand("무관한말") == ("무관한말",)


def test_index_ranks_the_document_that_contains_the_query():
    index = Index(["a", "b"], ["백탁이 심하다", "발림성이 좋다"])
    hits = index.search("백탁")
    assert hits and hits[0][0] == "a"


def test_a_common_term_weighs_less_than_a_rare_one_and_never_negative():
    # A negative idf lets a common word turn the ranking over. The commoner it is the lighter it gets, but it
    # never goes below 0.
    index = Index(["a", "b", "c"], ["백탁 끈적임", "백탁", "백탁"])
    assert 0.0 < index.idf("끈적임")
    assert 0.0 <= index.idf("백탁") < index.idf("끈적임")


def test_an_unknown_term_scores_zero():
    index = Index(["a"], ["백탁"])
    assert index.idf("없는말") == 0.0


def test_skip_removes_a_document_from_the_candidates():
    # The heldout evaluation stands on this -- does it still find them with the documents carrying the
    # query's characters taken out.
    index = Index(["a", "b"], ["백탁이 심하다", "백탁 없음"])
    assert [d for d, _ in index.search("백탁", skip={"a"})] == ["b"]


def test_k_none_returns_every_hit():
    index = Index(["a", "b", "c"], ["백탁", "백탁 조금", "무관"])
    assert len(index.search("백탁", k=None)) == 2


def test_state_round_trips_through_from_state():
    # The cache travels as a dict rather than a class. A broken round trip makes the cache give a different
    # answer quietly.
    index = Index(["a", "b"], ["백탁이 심하다", "발림성이 좋다"])
    revived = Index.from_state(index.state())
    assert revived.search("백탁") == index.search("백탁")
    assert revived.n == index.n and revived.avg_len == index.avg_len


def test_mismatched_lengths_are_refused():
    try:
        Index(["a"], ["x", "y"])
    except ValueError:
        return
    raise AssertionError("doc_ids 와 texts 길이가 달라도 통과했다")
