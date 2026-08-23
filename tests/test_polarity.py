"""RulePolarity: aspect 매칭·부정어·중립 명사. 문장은 평가셋 tune 셋에서 옮겼다."""

from __future__ import annotations

import re

import pytest

from analysis.lexicon import DISCOURSE_MARKERS, WISH_MARKERS
from analysis.polarity import RulePolarity, ruleset_for
from analysis.types import AspectLexicon, AspectPattern, Polarity

STICKY = "끈적|유분|번들|번질|기름[지기이]|개기름|기름 ?[돌올]|기름져|미끌|찐득|꾸덕|잔여감|찝찝|겉돌"
TEXTURE = "발림|발리|제형|텍스처|흡수|묽|뻑뻑|퍽퍽|되직|잘 ?펴|안 ?펴|밀착"
FOAM = (
    "세정력(이|도|은|가)? ?(약|떨어|별로|없|안|아쉬)|거품(이|도|은)? ?(잘 ?)?(안|적|없|부족)"
    "|안 ?씻|덜 ?씻|잔여감이? ?(남|있)"
)
FOAM_NOUN = "세정력|거품|잔여|뽀득|헹굼"


def _pattern(aspect, pattern, *, category="선블록", neutral=False, priority=0, ruleset="suncare-v2.2"):
    return AspectPattern(
        aspect=aspect,
        scope="category" if category else "generic",
        category=category,
        pattern=re.compile(pattern),
        is_neutral_noun=neutral,
        priority=priority,
        ruleset=ruleset,
    )


def _lexicon(*patterns, ruleset="suncare-v2.2"):
    return AspectLexicon(
        version=1,
        ruleset=ruleset,
        patterns=patterns,
        discourse_marker_re=re.compile(DISCOURSE_MARKERS),
        wish_marker_re=re.compile(WISH_MARKERS),
    )


SUN = _lexicon(_pattern("끈적유분", STICKY), _pattern("발림텍스처", TEXTURE, neutral=True))
FOAM_LEXICON = _lexicon(
    _pattern("세정력거품", FOAM, category="클렌징폼", ruleset="p1-v2.2"),
    _pattern("세정력거품", FOAM_NOUN, category="클렌징폼", neutral=True, ruleset="p1-v2.2"),
    ruleset="p1-v2.2",
)


def test_the_rule_polarity_is_the_contract_protocol():
    found: Polarity = RulePolarity()
    assert found.version == "rule-v2.2"


def test_the_suncare_dictionary_is_asked_for_only_where_it_has_rows():
    assert ruleset_for("선블록") == "suncare-v2.2"
    assert ruleset_for("클렌징폼") == "p1-v2.2"
    assert ruleset_for(None) == "p1-v2.2"


def test_a_raw_aspect_word_is_a_complaint_and_a_negated_one_is_satisfaction():
    rule = RulePolarity()
    raw = rule.classify("끈적임이 너무 심해서 못 쓰겠어요", 1.0, "선블록", SUN)
    assert (raw.aspect, raw.polarity, raw.version) == ("끈적유분", "불만", "rule-v2.2")
    negated = rule.classify("바르고 나면 물처럼 촉촉한데 끈적임 없어서 너무 좋습니다.", 5.0, "선블록", SUN)
    assert (negated.aspect, negated.polarity, negated.reason) == ("끈적유분", "만족", "aspect-negated")


def test_a_neutral_noun_alone_is_neutral_and_takes_its_sign_from_the_sentence():
    rule = RulePolarity()
    assert rule.classify("발림성은 이렇습니다", None, "선블록", SUN).polarity == "중립"
    assert rule.classify("발림성이 진짜 좋아요", None, "선블록", SUN).polarity == "만족"
    assert rule.classify("발림성이 최악이에요", None, "선블록", SUN).polarity == "불만"


def test_a_sentence_about_someone_elses_skin_type_is_not_this_products_complaint():
    found = RulePolarity().classify(
        "건성이신 분들은 끈적임이 덜하다고 느끼실 수 있는 피부 타입이에요", None, "선블록", SUN
    )
    assert found.polarity == "중립" and found.reason.startswith("skin-c")


def test_no_aspect_at_all_still_lands_a_polarity_and_an_empty_need_key_upstream():
    found = RulePolarity().classify("배송이 너무 늦어서 최악이에요", None, "선블록", SUN)
    assert (found.aspect, found.polarity, found.reason) == (None, "불만", "neg-only")


def test_the_category_dictionary_decides_which_aspect_a_sentence_gets():
    found = RulePolarity().classify(
        "그러다가 추천받아서 사용하게 되었는데 뽀득뽀득한 느낌 너무 좋네요!!", 2.0, "클렌징폼", FOAM_LEXICON
    )
    assert (found.aspect, found.polarity) == ("세정력거품", "만족")


@pytest.mark.parametrize("category", ["선블록", None])
def test_an_empty_dictionary_never_invents_an_aspect(category: str | None):
    empty = _lexicon()
    assert RulePolarity().classify("끈적임이 심해요", None, category, empty).aspect is None


@pytest.mark.postgres
def test_the_one_registry_line_carries_both_of_this_units_tasks(needs_runtime_url: str, monkeypatch, capsys):
    """유닛은 IMPLEMENTATIONS 에 자기 모듈 한 줄만 더한다 — 그 import 가 두 task 를 등록한다."""
    from analysis import predictors, registry
    from cosmai.cli import main
    from db import seed
    from db.seed._common import connect

    assert "analysis.predictors" in registry.IMPLEMENTATIONS
    registry.load_implementations()
    found = {task: registry.get(task) for task in ("polarity", "wish_class")}
    assert all(impl is not None and impl.version == "rule-v2.2" for impl in found.values())

    seed.run_all(needs_runtime_url, only=("lexicon", "labeled"))
    # Predictor 계약이 연결을 주지 않아 구현체가 사전 접속을 스스로 연다 (#12 이월).
    monkeypatch.setattr(predictors, "LEXICON_URL", needs_runtime_url)
    assert main(["eval", "polarity", "--url", needs_runtime_url]) == 0
    assert main(["eval", "wish_class", "--url", needs_runtime_url]) == 0
    out = capsys.readouterr().out
    for name in ("sun holdout 100", "p1 blind40", "sun tune 200", "p1 crosscat 60", "P9 blind60_v2"):
        assert name in out

    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT note, versions FROM analysis_run ORDER BY run_id")
        rows = cur.fetchall()
    assert [note for note, _ in rows] == ["eval:polarity:rule-v2.2", "eval:wish_class:rule-v2.2"]
    scores = rows[0][1]["scores"]
    assert scores["sun holdout 100"]["acc"] > 0.77
    assert rows[1][1]["scores"]["P9 blind60_v2"]["P:a"] > 0
