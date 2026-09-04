"""normalize_text is the contract that makes the same surface form whatever the source is -- with rules that
differ per source, BM25 scores cannot be compared across sources. What NormalizationFaults in
slices/ydc/tests/test_fault_injection.py caught with adversarial input was carried over here."""

from __future__ import annotations

from analysis.retrieval.normalize import normalize_text


def test_none_and_empty_become_the_empty_string():
    assert normalize_text(None) == ""
    assert normalize_text("") == ""


def test_html_entities_are_unescaped():
    assert normalize_text("&quot; &lt;b&gt;") == '" <b>'


def test_a_double_escaped_entity_is_unwound_all_the_way():
    # Unescaped once, `&lt;` is left and check_rows catches that chunk as a violation forever.
    assert normalize_text("&amp;lt;b&amp;gt;") == "<b>"


def test_control_characters_are_removed_not_replaced():
    # 제어문자를 공백으로 바꾸면 `백탁` 이 `백 탁` 이 되어 부분문자열 사전이 놓친다.
    assert normalize_text("백\x00탁") == "백탁"


def test_whitespace_collapses_and_trims():
    assert normalize_text("  하얗게   떠서\n\t싫다  ") == "하얗게 떠서 싫다"


def test_nfkc_folds_compatibility_forms():
    # Without full-width letters folding to half-width, an SPF search differs from source to source.
    assert normalize_text("ＳＰＦ５０") == "SPF50"


def test_normalizing_twice_changes_nothing():
    # check_rows catches a contract violation with `text != normalize_text(text)`, so it has to be idempotent.
    for raw in ("&amp;lt; 백\x07탁  ", "ＳＰＦ５０+", "", "  "):
        once = normalize_text(raw)
        assert normalize_text(once) == once


def test_a_lone_surrogate_does_not_raise():
    # It really did arrive in a YouTube comment.
    assert isinstance(normalize_text("\ud83d"), str)
