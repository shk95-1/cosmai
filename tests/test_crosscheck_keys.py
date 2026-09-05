"""What an ingredient key actually catches (fork #7, the contract `contracts/interfaces.md` §Ingredients).

**이 파일이 이 이슈의 가장 큰 위험을 진다.** ydc 는 `시카` 263행이 전부 트라이에톡시카프릴릴실레인
(실리콘 분산제)인 것을 모르고 채택률 41.1%를 발표할 뻔했다. 우리 성분 원천은 다르므로 키 목록을
그대로 믿지 않고 우리 표에서 다시 감사했고, 아래 성분명은 전부 `trend_radar.product.ingredients`
에서 실제로 나온 값이다 (2026-08-27, 180제품 · 성분행 22,705 · 고유명 2,051).
"""

from __future__ import annotations

from analysis import crosscheck

ANY_REASON = crosscheck.DENIED_FOR["레티날"]["레티놀"]

# 우리 표에서 그대로 뜬 성분명들. 오매칭을 재현하는 데 필요한 만큼만 든다.
OURS = (
    "트라이에톡시카프릴릴실레인",
    "트리에톡시카프릴릴실란",
    "병풀추출물",
    "병풀잎추출물",
    "마데카소사이드",
    "아시아티코사이드",
    "아시아틱애씨드",
    "레티놀",
    "소듐하이알루로네이트",
    "하이드롤라이즈드콜라겐",
    "카퍼트라이펩타이드-1",
    "나이아신아마이드",
    "판테놀",
)


def _rows(names=OURS):
    return [(f"p{n}", name) for n, name in enumerate(names)]


def test_the_two_letter_alias_catches_a_silicone_dispersant():
    """`시카` 를 성분명 부분문자열로 쓰면 시카가 아니라 실리콘 분산제가 잡힌다."""
    caught = [name for name in OURS if crosscheck.matches(name, ("시카",))]
    assert caught == ["트라이에톡시카프릴릴실레인", "트리에톡시카프릴릴실란"]
    assert not any(crosscheck.matches(name, ("시카",)) for name in ("병풀추출물", "마데카소사이드"))


def test_centella_is_written_byeongpul_in_our_table():
    """`센텔라` 는 0행이다. 성분표는 센텔라를 병풀로 적는다."""
    assert not [name for name in OURS if crosscheck.matches(name, ("센텔라",))]
    assert crosscheck.matches("병풀추출물", crosscheck.INGREDIENT_KEYS["시카센텔라"])
    assert crosscheck.matches("마데카소사이드", crosscheck.INGREDIENT_KEYS["시카센텔라"])


def test_retinol_is_not_retinal():
    """레티놀까지 세는 것은 후한 것이 아니라 전부 다른 성분을 세는 것이다."""
    assert not crosscheck.matches("레티놀", crosscheck.INGREDIENT_KEYS["레티날"])
    assert crosscheck.matches("레티날프로피오네이트", crosscheck.INGREDIENT_KEYS["레티날"])


def test_every_rejected_alias_stays_out_of_the_key_table():
    """되살리면 무엇이 잡히는지가 REJECTED_TERMS 에 적혀 있고, 그 둘은 어느 키에도 없어야 한다."""
    terms = {term for group in crosscheck.INGREDIENT_KEYS.values() for term in group}
    assert set(crosscheck.REJECTED_TERMS) == {"시카", "레티놀"}
    assert terms.isdisjoint(crosscheck.REJECTED_TERMS)
    assert all(crosscheck.REJECTED_TERMS.values()), "버린 이유가 없는 별칭은 다시 살아난다"
    # `센텔라` 는 버린 것이 아니라 키에 남아 0행일 뿐이다 -- 그래서 `병풀` 이 필요했다.
    assert "센텔라" in crosscheck.INGREDIENT_KEYS["시카센텔라"]


def test_ydcs_own_suspect_rule_would_not_have_caught_this():
    """`시카` 는 트라이에톡시카프릴릴실레인 **안에 진짜로 들어 있다.** 그래서 "잡힌 이름에 키가 하나도
    안 들어 있는가" 라는 규칙은 만족되고 아무 말도 하지 않는다 -- 사고를 잡은 것은 규칙이 아니라
    찍힌 이름을 읽은 사람이다. 우리 게이트가 DENIED_NAMES 인 이유가 이 한 줄이다."""
    assert "시카" in "트라이에톡시카프릴릴실레인"


def test_the_audit_flags_a_key_that_catches_a_denied_substance():
    """`시카` 를 되살렸다고 치고 감사가 잡는지 본다."""
    (bad,) = crosscheck.audit(_rows(), keys={"시카": ("시카",)})
    assert bad.rows == 2 and bad.suspect
    assert bad.denied == ("트라이에톡시카프릴릴실레인", "트리에톡시카프릴릴실란")
    assert bad.names[0] == ("트라이에톡시카프릴릴실레인", 1)


def test_a_key_that_catches_nothing_is_absence_not_a_mismatch():
    (none,) = crosscheck.audit(_rows(), keys={"PDRN": ("피디알엔", "pdrn")})
    assert none.rows == 0 and not none.suspect


def test_every_denied_name_carries_the_reason_it_was_denied():
    assert set(crosscheck.DENIED_NAMES) == {"트라이에톡시카프릴릴실레인", "트리에톡시카프릴릴실란"}
    assert all(crosscheck.DENIED_NAMES.values())
    assert crosscheck.DENIED_FOR == {"레티날": {"레티놀": ANY_REASON}} or all(
        reason for names in crosscheck.DENIED_FOR.values() for reason in names.values()
    )


def test_the_gate_is_as_wide_as_the_matcher():
    """완전 일치로 물으면 매처가 부분문자열로 잡은 것을 게이트가 못 본다 -- 운영 표의 `레티놀` 7행 중
    4행이 이미 접미사형이라, 맨 `레티놀` 3행이 사라지는 날 게이트가 조용해진다."""
    for name in ("트라이에톡시카프릴릴실레인 (1%)", "트라이에톡시카프릴릴실레인*"):
        (row,) = crosscheck.audit([("p1", name)], keys={"시카": ("시카",)})
        assert row.suspect and row.denied == ("트라이에톡시카프릴릴실레인",), name


def test_a_denial_belongs_to_a_key_not_to_the_whole_table():
    """`레티놀` 은 `레티날` 키에만 금지다. 전역으로 두면 실측 한 줄이 펩타이드 키를 빨갛게 만든다 --
    공백으로만 나열한 성분표에 `올리고펩타이드-1   * 레티놀 함량 509 IU/g` 가 통째로 한 이름이다."""
    run_on = "벼에스에이치-올리고펩타이드-1   * 레티놀 함량 509 IU/g"
    (peptide,) = crosscheck.audit([("p1", run_on)], keys={"펩타이드": ("펩타이드",)})
    assert peptide.rows == 1 and not peptide.suspect
    (retinal,) = crosscheck.audit([("p1", "레티놀(0.04 ppm)")], keys={"레티날": ("레티놀",)})
    assert retinal.suspect and retinal.denied == ("레티놀",)


def test_the_corrected_keys_pass_the_audit_on_our_own_ingredient_names():
    audits = crosscheck.audit(_rows())
    assert [row.key for row in audits] == list(crosscheck.INGREDIENT_KEYS)
    assert not [row.key for row in audits if row.suspect]
    caught = {row.key: row.rows for row in audits}
    assert caught["시카센텔라"] == 5, "병풀 2 · 마데카 1 · 아시아티코 1 · 아시아틱 1"
    assert caught["레티날"] == 0


def test_what_each_key_catches_is_locked_here_not_left_to_the_exit_code():
    """기계 규칙으로 `시카` 를 잡을 수 없다는 것이 위 두 테스트다. 그래서 **키가 무엇을 잡는지를
    여기서 건다** -- 코퍼스가 자라 새 물질이 어떤 키에 들어오면 이 줄이 먼저 깨지고 사람이 다시 읽는다.
    """
    caught = {row.key: {name for name, _count in row.names} for row in crosscheck.audit(_rows())}
    assert caught["시카센텔라"] == {
        "병풀추출물",
        "병풀잎추출물",
        "마데카소사이드",
        "아시아티코사이드",
        "아시아틱애씨드",
    }
    assert caught["히알루론산"] == {"소듐하이알루로네이트"}
    assert caught["펩타이드"] == {"카퍼트라이펩타이드-1"}
    assert caught["콜라겐"] == {"하이드롤라이즈드콜라겐"}
    assert caught["레티날"] == set()


def test_a_bracketed_section_label_is_not_an_ingredient():
    """기획 세트는 구성품 이름을 대괄호로 앞세운다 -- `[마데카소사이드] 정제수` 는 정제수 한 줄이다."""
    assert crosscheck.parse_ingredients("[마데카소사이드] 정제수, 글리세린") == ["정제수", "글리세린"]
    assert crosscheck.parse_ingredients("[시카에센스]") == []


def test_a_comma_inside_a_percentage_does_not_split_an_ingredient():
    """`나이아신아마이드(20,000 ppm)` 를 쉼표로 자르면 성분 하나가 둘이 되고 배합 순위가 밀린다."""
    assert crosscheck.parse_ingredients("나이아신아마이드(20,000 ppm), 정제수") == [
        "나이아신아마이드(20,000 ppm)",
        "정제수",
    ]


def test_a_star_note_is_dropped_and_newlines_split():
    assert crosscheck.parse_ingredients("* 퍼스트 에센스\n정제수,\n글리세린") == ["정제수", "글리세린"]


def test_the_sun_context_rule_names_what_the_talk_count_is_not():
    """담론 수를 "선크림 담론" 으로 읽으면 안 된다 -- 전량에서 PDRN 은 933문서 중 149문서였다."""
    pdrn = crosscheck.IngredientRow("PDRN", talk_youtube=933, talk_youtube_sun=149, talk_commerce=54)
    assert pdrn.sun_share < crosscheck.SUN_SHARE_LOW
    assert crosscheck.ingredient_reading(pdrn).startswith(crosscheck.READ_NOT_SUNCARE)
    sunny = crosscheck.IngredientRow("x", talk_youtube=100, talk_youtube_sun=60, talk_commerce=0)
    assert crosscheck.ingredient_reading(sunny) == ""
    silent = crosscheck.IngredientRow("y", talk_youtube=0, talk_youtube_sun=0, talk_commerce=0)
    assert crosscheck.ingredient_reading(silent) == "", "담론이 없는 성분은 문맥을 말할 것도 없다"


def test_both_axes_that_divide_by_another_population_stay_locked():
    """선케어 제품 중 성분표가 있는 것은 2개다. 180 으로 나누면 PAPER_HOLD 가 정정한 그 오류다."""
    assert crosscheck.FORMULA_HOLD is True
    assert crosscheck.PAPER_HOLD is True
    row = crosscheck.IngredientRow("PDRN", 933, 149, 54)
    assert row.formula_products is None and row.formula_pct is None
    assert row.median_order is None and row.high_dose_pct is None


def test_talk_is_matched_on_the_raw_text_not_on_folded_words():
    """성분명은 띄어쓰기가 흔들려 공백을 접지만, 담론은 접으면 안 된다 -- 자유 문장에서 공백을 접으면
    낱말 경계를 넘어 붙어 없는 언급이 생긴다 (ydc `count_terms` 와 같은 자리)."""
    assert crosscheck.matches("나이아신아마이드 (20,000 ppm)", ("나이아신아마이드",))
    assert not crosscheck.mentions_term("선크림 콜라 겐 없이", ("콜라겐",))
    assert crosscheck.mentions_term("콜라겐 좋아요", ("콜라겐",))


def test_the_audited_catch_list_is_self_consistent():
    """**What CI carries is the list's own self-consistency alone.** Comparing the list against the real
    tables is something CI cannot do -- it cannot reach the production tables. That path is
    `tool/measure-crosscheck-keys`, and the contract's §Ingredients says so."""
    known = crosscheck.known_names()
    assert set(known) == set(crosscheck.INGREDIENT_KEYS), "목록에 없는 키가 있으면 그 키는 감사되지 않았다"
    for key, names in known.items():
        terms = crosscheck.INGREDIENT_KEYS[key]
        assert all(crosscheck.matches(name, terms) for name in names), key
        assert crosscheck.denied_in(key, names) == (), key
    assert sum(len(names) for names in known.values()) == 190


def test_the_measure_tool_is_what_catches_a_new_mismatch():
    """리뷰가 주입한 변이(`"세라마이드": ("세라",)`)를 이 목록이 실제로 되묻는지 본다. 운영 표 없이
    같은 일을 하려면 그 표에서 나온 이름을 넣고 물으면 된다 -- 카프릴릭/카프릭트라이글리세라이드는
    에몰리언트이고 세라마이드가 아니다."""
    emollient = "카프릴릭/카프릭트라이글리세라이드"
    assert crosscheck.matches(emollient, ("세라",)), "이것이 잡히는 것이 변이의 내용이다"
    assert emollient not in {n for names in crosscheck.known_names().values() for n in names}
