"""The crosscheck rules (fork #7, the contract `contracts/interfaces.md` §Crosscheck).

ydc 세 스크립트의 `demo()` 가 못 박은 입력·출력이 여기 그대로 온다 -- 승격이 답을 바꾸지 않았다는
말은 같은 입력에 같은 답이 나온다는 뜻이고, 우리 원천으로는 그 입력을 재현할 수 없으므로 출처가
자기 파일에 적어 둔 그 입력을 든다.
"""

from __future__ import annotations

import pytest

from analysis import crosscheck
from analysis.trend import MIN_MENTIONS


def test_ranks_puts_the_largest_first():
    # ydc cross_source.demo() 가 못 박은 그대로.
    assert crosscheck.ranks({"a": 3.0, "b": 1.0, "c": 2.0}) == {"a": 1, "c": 2, "b": 3}


def test_each_source_carries_its_own_denominator():
    """Document counts are not summed across sources. Each computes with its own denominator and they are read
    side by side (the contract's §Composition)."""
    mentions = {
        crosscheck.COMMENT: {"백탁": 1, "발림성": 3},
        crosscheck.COMMERCE_REVIEW: {"백탁": 30, "발림성": 10},
    }
    rows = crosscheck.composition(mentions, ("백탁", "발림성"))
    shares = {row.topic_key: row.shares for row in rows}
    assert shares["백탁"][crosscheck.COMMENT] == pytest.approx(25.0)
    assert shares["백탁"][crosscheck.COMMERCE_REVIEW] == pytest.approx(75.0)
    for source in (crosscheck.COMMENT, crosscheck.COMMERCE_REVIEW):
        assert sum(row.shares[source] for row in rows) == pytest.approx(100.0)


def test_a_topic_outside_the_dictionary_axis_never_enters_the_denominator():
    """분모는 `trend_use` 주제의 합이다. 축 밖 주제가 세지면 모든 구성비가 조용히 작아진다."""
    mentions = {crosscheck.COMMENT: {"백탁": 1, "추천_재구매": 99}}
    (row,) = crosscheck.composition(mentions, ("백탁",))
    assert row.shares[crosscheck.COMMENT] == pytest.approx(100.0)


def test_a_source_with_no_mention_is_zero_not_a_hole():
    mentions = {crosscheck.COMMENT: {}, crosscheck.COMMERCE_REVIEW: {"백탁": 4}}
    (row,) = crosscheck.composition(mentions, ("백탁",))
    assert row.shares[crosscheck.COMMENT] == 0.0
    assert row.documents[crosscheck.COMMENT] == 0


def _reading(creator: float, consumer: float, comment: float = 0.0) -> str:
    return crosscheck.share_reading(
        {
            crosscheck.CREATOR: creator,
            crosscheck.CONSUMER: consumer,
            crosscheck.COMMENT: comment,
            crosscheck.VIDEO_TITLE: 0.0,
        }
    )


def test_the_three_composition_readings_are_ydcs_own_examples():
    # ydc source_composition.demo() 의 세 줄. 인자 이름만 우리 것(제작자=자막)이다.
    assert _reading(0.3, 12.1) == crosscheck.READ_CONSUMER_ONLY
    assert _reading(8.9, 0.2) == crosscheck.READ_CREATOR_LEAD
    assert _reading(5.0, 5.2) == ""


def test_the_consumer_lead_needs_the_full_width():
    assert _reading(3.0, 8.0) == crosscheck.READ_CONSUMER_LEAD
    assert _reading(3.0, 7.9) == ""


def test_the_creator_blind_spot_rule_survives_without_naver():
    """ydc cross_source 의 "영상은 안 다루는데 댓글·리뷰에는 있음". 세 조건이 다 서야 붙는다."""
    assert _reading(0.4, 1.0, comment=1.3) == crosscheck.READ_COMMENT_ONLY
    # 댓글이 제작자의 세 배에 못 미치면 붙지 않는다.
    assert _reading(0.4, 1.0, comment=1.1) == ""
    # 실사용 쪽이 0 이면 붙지 않는다 -- 아무도 말하지 않는 주제는 사각이 아니다.
    assert _reading(0.4, 0.0, comment=9.9) == ""


def test_polarity_reads_the_choice_wording():
    # ydc commerce_crosscheck.demo() 그대로.
    assert crosscheck.polarity("자극없이 순해요") == "positive"
    assert crosscheck.polarity("자극이 느껴져요") == "negative"
    assert crosscheck.polarity("보통이에요") == "neutral"


def test_positive_rate_is_the_share_within_one_topic_group():
    # ydc 가 주석에 적은 자극도 실측: 순해요 70 / 보통 29 / 느껴져요 1 -> 긍정 70%.
    assert crosscheck.positive_rate(
        [("자극없이 순해요", 70), ("보통이에요", 29), ("자극이 느껴져요", 1)]
    ) == pytest.approx(70.0)
    assert crosscheck.positive_rate([]) is None
    assert crosscheck.positive_rate([("보통이에요", 100)]) == 0.0, "중립만 있으면 긍정 0"


def test_the_four_rating_readings():
    high, low = crosscheck.POSITIVE_RATE_HIGH + 5, crosscheck.POSITIVE_RATE_HIGH - 5
    wide, narrow = crosscheck.GAP_PP_MATERIAL + 0.5, crosscheck.GAP_PP_MATERIAL
    assert crosscheck.rating_reading(low, wide) == crosscheck.READ_GAP_UNHAPPY
    assert crosscheck.rating_reading(high, wide) == crosscheck.READ_GAP_HAPPY
    assert crosscheck.rating_reading(low, narrow) == crosscheck.READ_QUIET_UNHAPPY
    assert crosscheck.rating_reading(high, narrow) == crosscheck.READ_SATURATED
    # 판정 행이 없어 갭을 모르는 셀은 갭이 큰 쪽으로 읽지 않는다.
    assert crosscheck.rating_reading(low, None) == crosscheck.READ_QUIET_UNHAPPY


def test_two_commerce_groups_land_on_one_topic_and_are_both_named():
    """`보습력`·`수분감` 은 우리 `촉촉함_건조함` 하나로 온다. 어느 그룹이 왔는지가 행에 남아야
    "커머스가 그 주제를 뭐라고 부르나" 를 되짚을 수 있다."""
    rated = {
        ("oliveyoung", "p1", "보습력"): [("촉촉해요", 90.0), ("보통이에요", 10.0)],
        ("oliveyoung", "p2", "수분감"): [("수분감 부족해요", 60.0), ("촉촉해요", 40.0)],
    }
    (row,) = crosscheck.ratings(rated, {})
    assert row.topic_key == "촉촉함_건조함"
    assert row.commerce_groups == ("보습력", "수분감")
    assert row.products_rated == 2
    assert row.positive_rate_mean == pytest.approx(65.0)


def test_a_group_outside_the_map_is_dropped_rather_than_guessed():
    """전량에서 `피부타입` 이 그 자리다 -- 우리 주제 축에 대응이 없다."""
    rated = {("oliveyoung", "p1", "피부타입"): [("건성", 100.0)]}
    assert crosscheck.ratings(rated, {}) == ()


def test_a_thin_topic_keeps_its_numbers_but_loses_its_reading():
    rated = {
        ("oliveyoung", f"p{n}", "발림성"): [("잘 발려요", 100.0)] for n in range(crosscheck.MIN_PRODUCTS - 1)
    }
    (row,) = crosscheck.ratings(rated, {"발림성": (1, 3.0, 2.0, "급상승")})
    assert row.thin and row.reading == ""
    assert row.products_rated == crosscheck.MIN_PRODUCTS - 1
    assert row.positive_rate_mean == pytest.approx(100.0)


def test_the_sample_gate_is_the_same_number_the_verdict_uses():
    """Requiring 5 for the verdict while making an exception for this crosscheck alone is a double standard
    (the contract's §Rating)."""
    assert crosscheck.MIN_PRODUCTS == MIN_MENTIONS


def test_rows_are_ordered_by_how_much_evidence_stands_behind_them():
    rated = {("oliveyoung", "p1", "자극도"): [("순해요", 100.0)]}
    rated |= {("oliveyoung", f"q{n}", "발림성"): [("잘 발려요", 100.0)] for n in range(3)}
    rows = crosscheck.ratings(rated, {})
    assert [row.topic_key for row in rows] == ["발림성", "자극_눈시림"]


def test_every_mapped_topic_exists_on_the_dictionary_axis():
    """커머스 그룹이 가리키는 주제가 사전에서 사라지면 그 행의 대조는 뜻이 없다. 사전 판본이 움직이는
    날 파이프라인의 `group_map_drift` 보다 먼저 깨지라고 여기 둔다."""
    import csv

    from analysis.retrieval.topics import DICTIONARY_CSV

    with DICTIONARY_CSV.open(encoding="utf-8-sig", newline="") as handle:
        axis = {row["aspect"] for row in csv.DictReader(handle) if row["trend_use"] == "true"}
    assert set(crosscheck.GROUP_MAP.values()) <= axis, sorted(set(crosscheck.GROUP_MAP.values()) - axis)


def test_the_confirmed_table_answers_before_the_hints():
    """The hint list is a substring over a vendor string too, so it has the same illness as an ingredient key.
    Fed the 23 production terms, five flipped (2026-08-27), and that is why the canonical form is the table a
    person confirmed (the contract's §Rating)."""
    flipped = {
        ("자극도", "자극이 있어요"),
        ("보습력", "약간 건조해요"),
        ("지속력", "예상보다 짧아요"),
        ("커버력", "예상보다 짧아요"),
        ("수분감", "매트해요"),
    }
    for group, name in flipped:
        assert crosscheck.polarity(name) == "positive", f"{name}: 힌트가 뒤집는 것이 이 테스트의 전제다"
        assert crosscheck.polarity(name, topic_group=group) == "negative", f"{group}/{name}"
    # `없어요` 힌트가 부정으로 끌어당기는 자리도 같은 병이다.
    assert crosscheck.polarity("날림이 없어요") == "negative"


def test_a_group_the_table_does_not_know_still_falls_back_to_the_hints():
    """확인된 표는 오늘 아는 어휘뿐이다. 모르는 문구에 답을 안 하면 그 제품이 통째로 사라진다 --
    답은 하되, 그 문구가 왔다는 사실을 `tool/measure-crosscheck-keys` 가 말한다."""
    assert crosscheck.polarity("처음 보는 문구", topic_group="자극도") == "positive"
    assert crosscheck.polarity("자극이 느껴져요", topic_group="없는그룹") == "negative"


def test_the_confirmed_table_covers_every_mapped_group():
    known = crosscheck.confirmed_polarity()
    assert {group for group, _name in known} == set(crosscheck.GROUP_MAP)
    assert set(known.values()) <= {"positive", "negative", "neutral"}
    # 그룹마다 세 극성이 다 있어야 긍정률이 0~100 을 실제로 가로지른다.
    for group in crosscheck.GROUP_MAP:
        polarities = {value for (grp, _name), value in known.items() if grp == group}
        assert polarities == {"positive", "negative", "neutral"}, group


def test_the_rate_uses_the_group_so_a_flipped_label_cannot_inflate_it():
    """`보습력` 이 오는 날 힌트만 보면 긍정률이 100% 로 선다 -- 실제로는 40% 다."""
    choices = [("촉촉해요", 40.0), ("보통이에요", 20.0), ("약간 건조해요", 40.0)]
    assert crosscheck.positive_rate(choices) == pytest.approx(80.0), "힌트만 보면 이렇게 부푼다"
    assert crosscheck.positive_rate(choices, topic_group="보습력") == pytest.approx(40.0)
