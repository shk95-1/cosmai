"""BM25 토큰화·확장·점수. slices/ydc/bm25.py 의 demo() 단언을 옮겼다.

토큰화가 조용히 깨지는 것이 이 유닛의 주된 고장 모드다 -- 사전이 안 얹히면 `백탁` 이
`백`+`탁` 으로 갈리고, 태그 접미(`VA-I`)를 놓치면 서술어 질의의 토큰이 0개가 된다.
어느 쪽도 예외를 던지지 않고 순위만 엉뚱해지므로 여기서 세운다."""

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
    # 사전이 빠지면 성분명이 형태소로 쪼개져 성분 검색이 조용히 빈다.
    for path in DICTIONARIES:
        assert path.exists(), path


def test_the_dictionaries_read_at_runtime_are_the_packaged_copies_not_the_slice():
    """analysis/slices/ydc/seeds/ 에 md5 가 같은 사본이 있어 정본이 둘로 보인다(#9 가 지운다).
    토크나이저가 읽는 경로도 캐시 서명이 해시하는 경로도 패키지 쪽이라는 것을 여기서 못박는다 --
    "중복이니 합치자" 가 슬라이스 쪽을 정본으로 삼는 날 조용히 읽기 전용 파일이 정본이 된다(#18 M15)."""
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
    # `spf` + `50` 으로 갈리면 차단지수 검색이 전부 어긋난다.
    assert "spf50+" in tokenize("이 제품은 SPF50+ 입니다")


def test_a_predicate_query_yields_tokens():
    # 태그 접미(`VA-I`)를 안 벗기면 여기가 빈 리스트가 된다.
    assert tokenize("하얗게 떠서 싫다")


def test_an_ingredient_name_stays_one_token():
    assert "에칠헥실트리아존" in tokenize("에칠헥실트리아존 함유")


def test_registered_words_and_expansion_words_are_different_sets():
    # 등록은 "Kiwi 가 쪼개니 붙여 달라", 확장은 "한 낱말로 잘 주는데 그 안에 별칭이 있다".
    # 둘을 섞으면 한쪽이 깨진다.
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
    # idf 가 음수가 되면 흔한 말이 순위를 뒤집는다. 흔할수록 가벼워지되 0 밑으로는 안 간다.
    index = Index(["a", "b", "c"], ["백탁 끈적임", "백탁", "백탁"])
    assert 0.0 < index.idf("끈적임")
    assert 0.0 <= index.idf("백탁") < index.idf("끈적임")


def test_an_unknown_term_scores_zero():
    index = Index(["a"], ["백탁"])
    assert index.idf("없는말") == 0.0


def test_skip_removes_a_document_from_the_candidates():
    # heldout 평가가 이것 위에 선다 -- 질의 글자가 든 문서를 빼고도 찾아내는가.
    index = Index(["a", "b"], ["백탁이 심하다", "백탁 없음"])
    assert [d for d, _ in index.search("백탁", skip={"a"})] == ["b"]


def test_k_none_returns_every_hit():
    index = Index(["a", "b", "c"], ["백탁", "백탁 조금", "무관"])
    assert len(index.search("백탁", k=None)) == 2


def test_state_round_trips_through_from_state():
    # 캐시가 클래스가 아니라 dict 로 오간다. 왕복이 깨지면 캐시가 조용히 다른 답을 준다.
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
