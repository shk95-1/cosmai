"""카드 유형은 규칙이 배정한다 — LLM 이 "이건 기회야"라고 판단하지 않는다 (포크 #6, ydc `cards.py`).

계약 §기회 카드 의 표가 여섯 줄이고 그중 넷만 이 레포에서 설 수 있다. 못 서는 둘을 어휘에서 지우지
않는 것과, 없는 입력을 0 으로 깔지 않는 것이 같은 문장이라 이 파일이 그 둘을 함께 붙든다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analysis import cards
from analysis.types import TopicQuarterJudgementRow

INTERFACES = Path(__file__).resolve().parents[1] / "contracts" / "interfaces.md"
COMMENT = "youtube_comment"
VIDEO = "youtube_video"


def judgement(source: str, trend_type: str, *, gap: float | None = 0.0, score: float | None = None):
    return TopicQuarterJudgementRow(
        run_id=7,
        scope="선블록",
        topic_key="자극_눈시림",
        quarter="2026Q2",
        source=source,
        content_type="long_form",
        panel_version=1,
        panel_role="product",
        trend_type=trend_type,
        judged=trend_type not in ("근거 부족", "판정 보류", "미확정(진행 중)"),
        evidence_strength=90.0,
        single_source=True,
        opportunity_score=score,
        gap_pp=gap,
    )


def facts(comment_type: str, video_type: str, *, gap: float | None = 0.0, composition: float = 0.05, **rest):
    return cards.CellFacts(
        topic_key="자극_눈시림",
        quarter="2026Q2",
        comment=judgement(COMMENT, comment_type, gap=gap, score=rest.get("score")),
        video=judgement(VIDEO, video_type, gap=gap),
        comment_composition=composition,
        video_composition=rest.get("video_composition", 0.05),
        velocity_yoy=rest.get("velocity_yoy"),
        mentions=rest.get("mentions", 14),
    )


def classified(facts: cards.CellFacts) -> tuple[str, str]:
    got = cards.classify(facts)
    assert got is not None, facts
    return got


def test_the_vocabulary_is_six_and_the_contract_table_says_which_four_stand():
    assert len(cards.CARD_TYPES) == 6
    assert set(cards.UNAVAILABLE) == {"표현 공백", "선행 연구 기회"}
    assert set(cards.CARD_TYPES) - set(cards.UNAVAILABLE) == set(cards.IMPLEMENTED)
    body = INTERFACES.read_text(encoding="utf-8")
    for kind in cards.CARD_TYPES:
        assert f"| {kind} |" in body, kind
    # 왜 못 서는지가 어휘 옆에 붙어 있어야 다음 사람이 그 입력을 찾아 나선다.
    for kind, why in cards.UNAVAILABLE.items():
        assert why, kind


def test_a_wide_gap_with_a_judged_comment_cell_is_a_product_gap():
    kind, basis = classified(facts("채널 확산", "근거 부족", gap=19.48))
    assert kind == "제품 공백 기회"
    assert "19.48" in basis and "채널 확산" in basis


def test_the_gap_rule_needs_the_comment_cell_to_be_judged():
    """판정하지 않은 셀의 갭은 "댓글이 더 말한다"가 아니라 "아직 모른다"다."""
    assert cards.classify(facts("근거 부족", "근거 부족", gap=19.48)) is None


def test_a_rising_cell_with_a_narrow_gap_is_verified_growth():
    kind, basis = classified(facts("급상승", "급상승", gap=0.1))
    assert kind == "검증된 성장"
    assert "급상승" in basis


def test_a_rising_cell_with_a_wide_gap_the_other_way_is_a_fad_risk():
    """제품 공백 규칙은 갭이 **양수**일 때만 걸린다 — 영상이 앞선 채로 튀는 것은 다른 이야기다."""
    kind, _ = classified(facts("급상승", "근거 부족", gap=-7.98))
    assert kind == "단기 유행 위험"


def test_both_sides_steady_and_a_thick_share_is_a_saturated_market():
    kind, basis = classified(facts("지속 인기", "채널 확산", gap=0.5, composition=0.2))
    assert kind == "포화 시장"
    assert "20.00" in basis


def test_a_steady_pair_below_the_share_cut_makes_no_card():
    assert cards.classify(facts("지속 인기", "지속 인기", gap=0.5, composition=0.05)) is None


def test_a_cell_that_no_rule_catches_makes_no_card():
    assert cards.classify(facts("판정 보류", "근거 부족", gap=0.4)) is None


def test_the_two_constants_are_the_ydc_values_and_the_contract_says_they_are_not_fitted():
    assert (cards.GAP_PRODUCT_GAP, cards.SATURATED_COMPOSITION) == (2.0, 15.0)
    body = INTERFACES.read_text(encoding="utf-8")
    assert "`GAP_PRODUCT_GAP = 2.0`" in body and "`SATURATED_COMPOSITION = 15.0`" in body
    assert "적합된 값이 아니다" in body


def test_the_strength_of_a_product_gap_card_is_the_gap_not_the_score():
    """전부 점수로 세우면 점수가 NULL 인 셀의 카드가 언제나 밀리는데, 그 카드는 갭으로 서는 것이다."""
    gapped = facts("채널 확산", "근거 부족", gap=19.48, score=3.0)
    rising = facts("급상승", "근거 부족", gap=-7.98, score=100.0)
    assert cards.strength("제품 공백 기회", gapped) == 19.48
    assert cards.strength("단기 유행 위험", rising) == 100.0


def test_a_card_needs_a_quote_or_it_is_not_a_card():
    """설계 원칙 3 — 근거 원문이 없으면 만들지 않는다. 다만 조용히 넘기지는 않는다."""
    made = cards.build([facts("채널 확산", "근거 부족", gap=19.48)], quotes={}, alias_rank={})
    assert made.cards == ()
    # 규칙이 골라 낸 셀이 산출에서 빠진 것이라, 그 사실이 종료 코드까지 간다 (계약 §근거·카드).
    assert made.unquoted == (("자극_눈시림", "2026Q2"),)


def test_a_cell_no_rule_caught_is_not_an_unquoted_cell():
    """ "규칙에 걸린 셀이 없다"는 잘린 산출이 아니라 계산된 답이다 — 둘을 섞으면 종료 코드가 거짓말한다."""
    made = cards.build([facts("판정 보류", "근거 부족", gap=0.4)], quotes={}, alias_rank={})
    assert (made.cards, made.unquoted) == ((), ())


def test_the_cap_on_quotes_is_the_one_the_evidence_rules_own():
    """계약은 "그 수의 자리는 §근거 하나"라고 적는다 — 사본이 있으면 근거만 늘려도 카드는 셋이다."""
    import inspect

    from analysis.evidence import TOP_PER_CELL

    assert cards.TOP_PER_CELL is TOP_PER_CELL
    assert inspect.signature(cards.build).parameters["top"].default is TOP_PER_CELL


def test_one_type_gets_one_card_and_the_strongest_wins():
    """같은 유형 카드 셋은 데모에서 한 장과 같다."""
    weak = facts("채널 확산", "근거 부족", gap=3.0)
    strong = cards.CellFacts(**{**vars(facts("채널 확산", "근거 부족", gap=30.0)), "topic_key": "백탁"})
    quote = cards.Quote(rank=1, like_count=1, matched_term="자극", text="따가워요", parent_video_url="u")
    made = cards.build(
        [weak, strong],
        quotes={("자극_눈시림", "2026Q2"): [quote], ("백탁", "2026Q2"): [quote]},
        alias_rank={},
    )
    assert [(card.topic_key, card.card_type) for card in made.cards] == [("백탁", "제품 공백 기회")]


def test_the_quote_order_is_alias_specificity_then_likes():
    """일반어로 걸린 댓글이 좋아요만으로 맨 앞에 서면 주제와 무관한 문장이 근거의 얼굴이 된다."""
    rank = {"자극_눈시림": {"눈시림": 0, "자극": 1}}
    generic = cards.Quote(rank=1, like_count=99, matched_term="자극", text="a", parent_video_url="u")
    specific = cards.Quote(rank=2, like_count=1, matched_term="눈시림", text="b", parent_video_url="u")
    ordered = sorted([generic, specific], key=lambda q: cards.quote_order("자극_눈시림", q, rank))
    assert [q.matched_term for q in ordered] == ["눈시림", "자극"]


@pytest.mark.parametrize(
    ("kind", "wanted"),
    [("판정 보류", "no_prior_year"), ("급상승", None)],
)
def test_the_limits_carry_the_hold_reason_when_there_is_one(kind: str, wanted: str | None):
    row = judgement(COMMENT, kind, gap=0.0)
    if wanted:
        row = TopicQuarterJudgementRow(**{**vars(row), "hold_reason": wanted})
    made = cards.limits(cards.CellFacts(**{**vars(facts(kind, "근거 부족")), "comment": row}), ())
    assert any(wanted in line for line in made) if wanted else all("no_prior_year" not in x for x in made)


def test_every_card_says_the_recent_quarter_is_structurally_undercounted():
    """§모집단의 한계 의 여덟 문장 중 카드가 늘 지고 가야 하는 하나다."""
    made = cards.limits(facts("급상승", "근거 부족"), ())
    assert any("과소 집계" in line for line in made)


def test_a_generic_alias_in_the_evidence_is_a_limit_the_card_carries():
    quote = cards.Quote(rank=1, like_count=1, matched_term="제형", text="a", parent_video_url="u")
    made = cards.limits(
        cards.CellFacts(**{**vars(facts("급상승", "근거 부족")), "topic_key": "발림성"}), (quote,)
    )
    assert any("제형" in line for line in made)


def test_the_card_is_rendered_without_a_language_model():
    """요약 문장 자리에는 근거 원문을 그대로 싣는다 — 설계 원칙 1."""
    source = Path(cards.__file__).read_text(encoding="utf-8")
    for forbidden in ("anthropic", "ollama", "openai", "prompt"):
        assert forbidden not in source.lower(), forbidden
