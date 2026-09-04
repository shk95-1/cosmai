"""normalize_text 는 소스가 달라도 같은 표면형을 만드는 계약이다 -- 규칙이 소스마다 갈리면
BM25 점수를 소스 간에 비교할 수 없다. slices/ydc/tests/test_fault_injection.py 의
NormalizationFaults 가 적대적 입력으로 걸던 것을 여기로 옮겼다."""

from __future__ import annotations

from analysis.retrieval.normalize import normalize_text


def test_none_and_empty_become_the_empty_string():
    assert normalize_text(None) == ""
    assert normalize_text("") == ""


def test_html_entities_are_unescaped():
    assert normalize_text("&quot; &lt;b&gt;") == '" <b>'


def test_a_double_escaped_entity_is_unwound_all_the_way():
    # 한 번만 풀면 `&lt;` 가 남고, check_rows 가 그 청크를 영구히 위반으로 잡는다.
    assert normalize_text("&amp;lt;b&amp;gt;") == "<b>"


def test_control_characters_are_removed_not_replaced():
    # 제어문자를 공백으로 바꾸면 `백탁` 이 `백 탁` 이 되어 부분문자열 사전이 놓친다.
    assert normalize_text("백\x00탁") == "백탁"


def test_whitespace_collapses_and_trims():
    assert normalize_text("  하얗게   떠서\n\t싫다  ") == "하얗게 떠서 싫다"


def test_nfkc_folds_compatibility_forms():
    # 전각 영문이 반각으로 접히지 않으면 SPF 검색이 소스에 따라 갈린다.
    assert normalize_text("ＳＰＦ５０") == "SPF50"


def test_normalizing_twice_changes_nothing():
    # check_rows 가 `text != normalize_text(text)` 로 계약 위반을 잡으므로 멱등이어야 한다.
    for raw in ("&amp;lt; 백\x07탁  ", "ＳＰＦ５０+", "", "  "):
        once = normalize_text(raw)
        assert normalize_text(once) == once


def test_a_lone_surrogate_does_not_raise():
    # 유튜브 댓글에서 실제로 들어온 적이 있다.
    assert isinstance(normalize_text("\ud83d"), str)
