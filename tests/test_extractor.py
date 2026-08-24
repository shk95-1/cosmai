"""RuleExtractor: 문장 분할·후보 표지·바람 분류. 문장은 평가셋 tune 셋에서 그대로 옮겼다."""

from __future__ import annotations

import re
from datetime import date

import pytest

from analysis.extractor import RuleExtractor, sentences
from analysis.lexicon import DISCOURSE_MARKERS, WISH_MARKERS
from analysis.types import AspectLexicon, AspectPattern, EntitySurface, Extractor, Lexicon, TextUnit

STICKY = AspectPattern(
    aspect="끈적유분",
    scope="category",
    category="선블록",
    pattern=re.compile("끈적|유분|번들|번질"),
    is_neutral_noun=False,
    priority=0,
    ruleset="suncare-v2.2",
)
ASPECTS = AspectLexicon(
    version=1,
    ruleset="suncare-v2.2",
    patterns=(STICKY,),
    discourse_marker_re=re.compile(DISCOURSE_MARKERS),
    wish_marker_re=re.compile(WISH_MARKERS),
)
BRANDS = (
    EntitySurface(kind="brand", canonical="라네즈", surface="라네즈", tier="normal", source="manual"),
    EntitySurface(kind="brand", canonical="다이소", surface="다이소", tier="stop", source="manual"),
)
LEXICON = Lexicon(
    version=1,
    surfaces=BRANDS,
    surface_to_canonical={s.surface: s.canonical for s in BRANDS},
    surface_re=re.compile(r"(라네즈|다이소)"),
    stop=frozenset({"다이소"}),
    cooc_required=frozenset(),
    product_word_re=re.compile("크림|선크림"),
)


def review(text: str, rating: float | None = 5.0) -> TextUnit:
    return TextUnit(
        src="review",
        site="oliveyoung",
        ref="A1/R1",
        text=text,
        observed_at=date(2026, 3, 1),
        observed_at_resolution="day",
        rating=rating,
        product_key="A1",
        category="선블록",
    )


def comment(text: str) -> TextUnit:
    return TextUnit(
        src="yt_comment",
        site="youtube",
        ref="V1/C1",
        text=text,
        observed_at=date(2026, 3, 1),
        observed_at_resolution="month",
        like_count=3,
        channel_id="UC1",
    )


def test_a_run_on_sentence_is_split_where_the_slices_split_it():
    assert sentences("많이 촉촉해요 근데 끈적임이 심해요. 재구매는 안 할래요") == [
        "많이 촉촉해요",
        "근데 끈적임이 심해요.",
        "재구매는 안 할래요",
    ]


def test_a_sentence_too_short_to_judge_is_dropped_but_a_short_text_survives_whole():
    assert sentences("정말 좋아요. 굿") == ["정말 좋아요."]
    assert sentences("굿") == ["굿"]


def test_a_wish_marker_beats_a_complaint_marker_in_the_same_sentence():
    found = RuleExtractor().candidates(review("끈적임이 덜한 제품도 나왔으면 좋겠어요"), ASPECTS)
    assert [(c.kind, c.marker) for c in found] == [("wish", "좋겠")]


def test_a_category_pattern_makes_a_candidate_the_discourse_markers_would_miss():
    extractor = RuleExtractor()
    assert extractor.candidates(review("번들거림이 하루종일 올라옵니다"), ASPECTS)[0].kind == "complaint"
    empty = AspectLexicon(
        version=1,
        ruleset="suncare-v2.2",
        patterns=(),
        discourse_marker_re=ASPECTS.discourse_marker_re,
        wish_marker_re=ASPECTS.wish_marker_re,
    )
    assert extractor.candidates(review("번들거림이 하루종일 올라옵니다"), empty) == []


def test_a_low_rated_review_yields_candidates_even_without_a_marker():
    found = RuleExtractor().candidates(review("그냥 무난하게 발라요", rating=2.0), ASPECTS)
    assert [(c.kind, c.marker) for c in found] == [("low_rating", "rating=2.0")]
    assert RuleExtractor().candidates(review("그냥 무난하게 발라요", rating=5.0), ASPECTS) == []


def test_a_comment_never_gets_a_low_rating_candidate_because_it_has_no_rating():
    assert RuleExtractor().candidates(comment("그냥 무난하게 발라요"), ASPECTS) == []


def test_the_same_sentence_twice_in_one_unit_is_one_candidate_and_carries_the_subject():
    found = RuleExtractor().candidates(review("끈적임 심해요. 끈적임 심해요."), ASPECTS)
    assert len(found) == 1
    assert found[0].unit_ref == "A1/R1" and found[0].subject == "A1"


@pytest.mark.parametrize(
    ("text", "wish_class"),
    [
        ("쿠션형으로도 출시해주세요", "a"),
        ("모공팩도 해주세요~", "b"),
        ("이번 겨울엔 피부가 좀 나아졌으면 좋겠어요", "c"),
    ],
)
def test_the_wish_classes_the_p9_rules_separate(text: str, wish_class: str):
    found = RuleExtractor().wishes(comment(text), LEXICON)
    assert found is not None and found.wish_class == wish_class
    assert found.sentence and found.marker


@pytest.mark.parametrize(
    "text",
    [
        "요즘 많이 나오길래 저도 하나 사봤어요",
        "쿠팡입점해있는 업체가 엄청 많다고 들었어요",
    ],
)
def test_a_launch_marker_buried_in_another_word_is_not_a_request(text: str):
    """'나오길래'·'입점해있는' 은 출시 요청이 아니라 서술이다 — 표지가 낱말 안에 박혔을 뿐이다."""
    assert RuleExtractor().wishes(comment(text), LEXICON) is None


@pytest.mark.parametrize(
    "text",
    [
        "유분이 조금이라도 적게 나왔으면 좋겠어요",
        "이번 여름엔 여드름이 안 나오면 좋겠어요",
    ],
)
def test_a_hope_that_less_of_something_appears_is_a_plain_hope_not_a_launch(text: str):
    """'적게/안 나왔으면' 의 주어는 제품이 아니라 증상이다 — a 는 브랜드에 대한 제품 요청뿐이다."""
    found = RuleExtractor().wishes(comment(text), LEXICON)
    assert found is not None and found.wish_class == "c"


def test_a_comment_with_no_wish_at_all_is_not_a_wish_row():
    assert RuleExtractor().wishes(comment("항상 잘 보고 있습니다 감사합니다"), LEXICON) is None


def test_the_brand_comes_from_the_lexicon_and_a_stop_brand_is_not_one():
    found = RuleExtractor().wishes(comment("라네즈 선크림도 출시해주세요"), LEXICON)
    assert found is not None and found.brand == "라네즈"
    stopped = RuleExtractor().wishes(comment("다이소 선크림도 출시해주세요"), LEXICON)
    assert stopped is not None and stopped.brand is None


def test_format_and_attribute_stay_empty_while_those_lexicon_kinds_have_no_rows():
    found = RuleExtractor().wishes(comment("대용량 쿠션으로도 출시해주세요"), LEXICON)
    assert found is not None and (found.format, found.attribute) == (None, None)


def test_the_rule_extractor_is_the_contract_protocol():
    """candidates 의 lexicon_category 는 기본값이 있는 추가 인자다 — Protocol 호출은 그대로 맞는다."""
    found: Extractor = RuleExtractor()
    assert found.version == "rule-v2.3"
