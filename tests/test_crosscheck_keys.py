"""성분 키가 실제로 무엇을 잡는가 (포크 #7, 계약 `contracts/interfaces.md` §성분).

**이 파일이 이 이슈의 가장 큰 위험을 진다.** ydc 는 `시카` 263행이 전부 트라이에톡시카프릴릴실레인
(실리콘 분산제)인 것을 모르고 채택률 41.1%를 발표할 뻔했다. 우리 성분 원천은 다르므로 키 목록을
그대로 믿지 않고 우리 표에서 다시 감사했고, 아래 성분명은 전부 `trend_radar.product.ingredients`
에서 실제로 나온 값이다 (2026-08-27, 180제품 · 성분행 22,705 · 고유명 2,051).
"""

from __future__ import annotations

from analysis import crosscheck

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
    """되살리면 무엇이 잡히는지가 REJECTED_TERMS 에 적혀 있고, 그 셋은 어느 키에도 없어야 한다."""
    terms = {term for group in crosscheck.INGREDIENT_KEYS.values() for term in group}
    assert set(crosscheck.REJECTED_TERMS) == {"시카", "센텔라", "레티놀"}
    assert terms.isdisjoint(crosscheck.REJECTED_TERMS)
    assert all(crosscheck.REJECTED_TERMS.values()), "버린 이유가 없는 별칭은 다시 살아난다"


def test_the_audit_flags_a_key_whose_hits_never_contain_it():
    """`시카` 를 되살렸다고 치고 감사가 잡는지 본다. 눈으로는 못 잡는 자리다."""
    (bad,) = crosscheck.audit(_rows(), keys={"시카": ("시카",)})
    assert bad.suspect and bad.rows == 2
    assert bad.names[0] == ("트라이에톡시카프릴릴실레인", 1)


def test_a_key_that_catches_nothing_is_absence_not_a_mismatch():
    (none,) = crosscheck.audit(_rows(), keys={"PDRN": ("피디알엔", "pdrn")})
    assert none.rows == 0 and not none.suspect


def test_the_corrected_keys_pass_the_audit_on_our_own_ingredient_names():
    audits = crosscheck.audit(_rows())
    assert [row.key for row in audits] == list(crosscheck.INGREDIENT_KEYS)
    assert not [row.key for row in audits if row.suspect]
    caught = {row.key: row.rows for row in audits}
    assert caught["시카센텔라"] == 5, "병풀 2 · 마데카 1 · 아시아티코 1 · 아시아틱 1"
    assert caught["레티날"] == 0


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
    """담론 수를 "선크림 담론" 으로 읽으면 안 된다 -- 전량에서 PDRN 은 960문서 중 150문서였다."""
    pdrn = crosscheck.IngredientRow("PDRN", talk_youtube=960, talk_youtube_sun=150, talk_commerce=56)
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
    row = crosscheck.IngredientRow("PDRN", 960, 150, 56)
    assert row.formula_products is None and row.formula_pct is None
    assert row.median_order is None and row.high_dose_pct is None
