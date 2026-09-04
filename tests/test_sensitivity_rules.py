"""민감도 세 측정의 규칙을 DB 없이 묻는다 (포크 #41).

`tests/test_sensitivity_golden.py` 가 표본 위에서 ydc 와 1:1 을 지킨다면, 여기는 **그 표본이 못 밟는
갈래**를 진다: 뒤집힘 판정(표본은 `sample_ok` 가 한 셀도 서지 않는다) · 후향 검증의 세 적중 기준 ·
버린 정규식 규칙의 오검출 · 제외가 묶음 단위라는 것. 계약 문장의 자리는 `contracts/interfaces.md`
§민감도 다.
"""

from __future__ import annotations

import re
from pathlib import Path

from analysis import sensitivity
from analysis.judge import COMMENT, VIDEO
from analysis.sensitivity import Frame, Population, Reaction, Video
from analysis.trend import MIN_MENTIONS
from analysis.types import PanelSensitivityRow

DDL = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "needs" / "022_panel_and_quarter.sql"
FRAME = Frame(run_id=7, scope="선블록", content_type="long_form", panel_version=1)
TOPICS = ("백탁", "발림성")
QUARTERS = tuple(f"{year}Q{index}" for year in (2023, 2024, 2025, 2026) for index in (1, 2, 3, 4))


def _video(item_id: str, quarter: str, **kwargs) -> Video:
    return Video(
        item_id=item_id,
        channel_id=kwargs.get("channel_id", "c1"),
        panel_role=kwargs.get("panel_role", sensitivity.PRODUCT),
        quarter=quarter,
        topics=kwargs.get("topics", ("백탁",)),
        declared=kwargs.get("declared", False),
        matched=kwargs.get("matched", False),
    )


def _reaction(parent: str, digest: str, **kwargs) -> Reaction:
    return Reaction(
        parent_item_id=parent,
        digest=digest,
        counted=kwargs.get("counted", 1),
        documents=kwargs.get("documents", 1),
        topics=kwargs.get("topics", ("백탁",)),
        creator=kwargs.get("creator", False),
        promo=kwargs.get("promo", False),
    )


def _by_cell(made) -> dict[tuple[str, str], PanelSensitivityRow]:
    return {(row.source, row.topic_key): row for row in made}


def _panel_row(**kwargs) -> PanelSensitivityRow:
    return PanelSensitivityRow(
        source=kwargs.get("source", VIDEO),
        topic_key=kwargs.get("topic_key", "백탁"),
        quarters_ok_product=kwargs.get("quarters_ok_product", 9),
        quarters_ok_all=kwargs.get("quarters_ok_all", 9),
        delta_product_pp=kwargs["delta_product_pp"],
        delta_all_pp=kwargs["delta_all_pp"],
        difference_pp=round(kwargs["delta_all_pp"] - kwargs["delta_product_pp"], 2),
        sample_ok=kwargs.get("sample_ok", True),
    )


# ---------- 창 ----------
def test_the_previous_quarter_is_the_calendar_one_not_the_observed_one():
    assert sensitivity.previous_quarter("2026Q3") == "2026Q2"
    assert sensitivity.previous_quarter("2026Q1") == "2025Q4"


def test_the_two_windows_are_the_eight_quarters_before_the_one_still_running():
    """마지막 분기는 진행 중이라 두 창 밖이다. ydc 가 달력값으로 박아 둔 여덟과 같아야 한다."""
    prior, recent = sensitivity.calendar_windows(["2023Q3", "2026Q3"])
    assert recent == ("2025Q3", "2025Q4", "2026Q1", "2026Q2")
    assert prior == ("2024Q3", "2024Q4", "2025Q1", "2025Q2")


def test_a_quarter_missing_from_the_observation_still_takes_its_seat_in_the_window():
    """관측 목록의 인덱스로 세면 빠진 분기가 창을 한 칸씩 뒤로 민다 -- 그러면 같은 코퍼스가 두 개의
    "최근 4분기"를 갖는다. 이 코퍼스에서 빠지는 분기는 2025Q1 이고, 달력으로 세면 그 칸이 남는다."""
    sparse = [q for q in QUARTERS[2:15] if q != "2025Q1"]
    assert "2025Q1" not in sparse
    prior, recent = sensitivity.calendar_windows(sparse)
    # 값을 리터럴로 못 박는다 -- 두 호출을 맞대면 `calendar_windows` 가 마지막 분기만 읽으므로
    # 어떤 입력으로도 안 깨지는 항등식이 된다.
    assert prior == ("2024Q3", "2024Q4", "2025Q1", "2025Q2")
    assert recent == ("2025Q3", "2025Q4", "2026Q1", "2026Q2")
    # 관측 인덱스로 셌다면 창이 한 칸 밀려 2024Q2 가 직전 구간에 들어왔을 자리다.
    assert "2024Q2" not in prior


def test_a_population_without_a_quarter_is_refused_instead_of_answered():
    try:
        sensitivity.calendar_windows([])
    except sensitivity.ShortHistory as blocked:
        assert "no quarter" in str(blocked)
    else:  # pragma: no cover - the guard is the point of the test
        raise AssertionError("an empty history answered instead of stopping")


# ---------- 패널 ----------
def _thirteen_quarters(mentioned: int, *, role_split: bool = False) -> Population:
    """13분기 산출 하나. `mentioned` 개 분기에서만 표본 게이트를 넘게 만든다."""
    videos: list[Video] = []
    quarters = list(QUARTERS[2:15])
    for index, quarter in enumerate(quarters):
        topics = ("백탁",) if index < mentioned else ()
        for copy in range(MIN_MENTIONS):
            videos.append(_video(f"v{index}-{copy}", quarter, topics=("발림성", *topics)))
        if role_split:
            videos.append(
                _video(
                    f"e{index}", quarter, topics=("발림성",), channel_id="e1", panel_role=sensitivity.EXPERT
                )
            )
    return Population(tuple(videos), ())


def test_a_topic_carried_by_more_than_half_the_quarters_is_a_judged_cell():
    """ydc 는 13분기 산출에서 이 문장을 7 로 박아 뒀다. 유도한 규칙이 같은 수를 내야 한다."""
    seven = _by_cell(sensitivity.panel_sensitivity(_thirteen_quarters(7), TOPICS))
    six = _by_cell(sensitivity.panel_sensitivity(_thirteen_quarters(6), TOPICS))
    assert seven[(VIDEO, "백탁")].quarters_ok_product == 7
    assert seven[(VIDEO, "백탁")].sample_ok is True
    assert six[(VIDEO, "백탁")].quarters_ok_product == 6
    assert six[(VIDEO, "백탁")].sample_ok is False


def test_the_expert_channels_land_in_the_all_column_and_nowhere_else():
    """expert 를 분모에 넣은 대조군이 product 산출과 다른 수를 낸다 -- 같으면 이 측정은 말이 없다.

    여기서는 expert 채널이 최근 4분기에만 `백탁` 을 낸다: product 델타는 0 인데 43채널 델타는 오른다.
    """
    prior, recent = sensitivity.calendar_windows(["2026Q3"])
    videos = [_video(f"p{q}", q, topics=("백탁", "발림성")) for q in (*prior, *recent)]
    videos.append(_video("p-last", "2026Q3", topics=("백탁", "발림성")))
    videos += [
        _video(f"e{q}", q, topics=("백탁",), channel_id="e1", panel_role=sensitivity.EXPERT) for q in recent
    ]
    made = _by_cell(sensitivity.panel_sensitivity(Population(tuple(videos), ()), TOPICS))
    assert made[(VIDEO, "백탁")].delta_product_pp == 0.0
    assert made[(VIDEO, "백탁")].delta_all_pp > 15
    assert made[(VIDEO, "백탁")].difference_pp == made[(VIDEO, "백탁")].delta_all_pp
    # 그 채널들은 product 산출의 어느 행에도 들지 않는다 (코퍼스 규칙 5).
    product = sensitivity.metrics(Population(tuple(videos), ()), TOPICS, FRAME)
    assert {row.panel_role for row in product} == {sensitivity.PRODUCT}
    assert all(row.denom_channels == 1 for row in product)


def test_a_cell_whose_direction_reverses_by_a_material_amount_is_a_flip():
    flipped = sensitivity.flipped([_panel_row(delta_product_pp=1.2, delta_all_pp=-0.8)])
    assert len(flipped) == 1


def test_a_sign_change_that_only_wobbles_around_zero_is_not_a_flip():
    """부호만 보면 0 근처를 오가는 셀이 전부 뒤집힘으로 잡힌다 -- 전량에서 실제로 한 셀이 그렇다."""
    assert sensitivity.flipped([_panel_row(delta_product_pp=-0.03, delta_all_pp=0.03)]) == []
    assert sensitivity.MATERIAL_PP == 0.5


def test_a_cell_that_was_never_a_judged_cell_cannot_flip_a_conclusion():
    assert sensitivity.flipped([_panel_row(delta_product_pp=1.2, delta_all_pp=-0.8, sample_ok=False)]) == []


def test_two_deltas_that_move_the_same_way_are_not_a_flip_however_far_apart():
    assert sensitivity.flipped([_panel_row(delta_product_pp=1.0, delta_all_pp=9.0)]) == []


# ---------- 후향 ----------
def test_the_quarter_we_cut_at_belongs_to_the_before_window_and_not_the_after_one():
    quarters = ["2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2"]
    assert sensitivity.next_quarters(quarters, "2024Q2", 2) == ["2024Q3", "2024Q4"]
    assert sensitivity.next_quarters(quarters, "2025Q2", 2) == []
    assert sensitivity.prior_quarters(quarters, "2024Q2", 4) == ["2024Q1", "2024Q2"]
    assert "2024Q2" in sensitivity.prior_quarters(quarters, "2024Q2", 4)
    assert "2024Q2" not in sensitivity.next_quarters(quarters, "2024Q2", 4)


def test_a_rise_that_keeps_climbing_hits_both_criteria():
    row = sensitivity.outcome(COMMENT, "백탁", "급상승", "2025Q1", 4.0, 3.0, 5.0, 6.0)
    assert (row.expected, row.hit, row.hit_level, row.actual) == (sensitivity.RISE_HELD, True, True, "상승")


def test_a_rise_that_only_holds_its_level_hits_b_and_misses_a():
    """기준 A 의 직전 구간에는 급상승한 분기 T 자체가 들어 있다 -- 평균 회귀만으로 실패가 나온다.
    두 질문이 다르고, 둘 중 하나만 내면 결과를 고른 것이 된다."""
    row = sensitivity.outcome(COMMENT, "백탁", "급상승", "2025Q1", 4.0, 3.0, 3.5, 6.0)
    assert (row.hit, row.hit_level) == (False, True)


def test_a_fall_is_scored_the_other_way_round():
    row = sensitivity.outcome(VIDEO, "백탁", "사라짐", "2025Q1", 4.0, 5.0, 3.0, 2.0)
    assert (row.expected, row.hit, row.hit_level) == (sensitivity.FALL_HELD, True, True)


def test_a_peak_is_asked_only_whether_the_peak_itself_went_away():
    """피크는 "그 분기보다 낮아졌는가"라 두 기준이 같은 질문이 된다 -- 직전 구간은 상대가 아니다."""
    row = sensitivity.outcome(VIDEO, "백탁", "단기 피크", "2025Q1", 1.0, 1.0, 3.0, 6.0)
    assert (row.expected, row.hit, row.hit_level, row.actual) == (sensitivity.PEAK_GONE, True, True, "상승")


def test_the_state_describing_types_are_not_backtested():
    """`지속 인기`·`채널 확산` 은 방향 예측이 아니라 상태 서술이다. 넣으면 적중률이 부풀려진다."""
    assert set(sensitivity.STATEFUL).isdisjoint(sensitivity.DIRECTIONAL)
    assert set(sensitivity.DIRECTIONAL) == {"급상승", "신규 등장", "사라짐", "단기 피크"}


# ---------- 표시 ----------
def test_the_ad_flag_is_the_union_of_the_self_report_and_the_description():
    """신고는 유튜버 자체 신고라 누락이 있다 -- 전량 실측으로 문구만 걸리는 영상이 200편을 넘는다."""
    assert _video("v", "2025Q1", declared=True, matched=False).ad is True
    assert _video("v", "2025Q1", declared=False, matched=True).ad is True
    assert _video("v", "2025Q1").ad is False


def test_the_description_rule_reads_a_sponsorship_and_not_an_apple():
    assert sensitivity.AD_RE.search("본 영상은 유료광고를 포함합니다")
    assert sensitivity.AD_RE.search("제품을 제공 받아 촬영했습니다")
    assert not sensitivity.AD_RE.search("애플 apple 신제품 리뷰")
    assert not sensitivity.AD_RE.search("supplement 후기")


def test_the_promo_rule_keeps_the_false_positives_that_got_the_other_rules_dropped():
    """전화번호·도박 사전은 재보니 걸린 것이 거의 전부 오검출이라 버렸다. 다시 넣지 않으려고 남긴다."""
    assert not sensitivity.PROMO_RE.search("나노쿠션 토토톡 할 수 있어서 좋아요")
    assert not sensitivity.PROMO_RE.search("40대출산맘 입니다")
    assert sensitivity.PROMO_RE.search("쿠팡 파트너스 링크입니다 https://coupa.ng/x")


def test_the_creator_hash_is_the_one_the_collector_wrote_into_the_corpus():
    """`sha256("youtube:" + channel_id)[:24]`. 식이 다르면 운영자 댓글이 조용히 0건이 된다."""
    assert sensitivity.creator_hash("UCabc123") == "224427a5eb83274bdf825b8a"
    assert len(sensitivity.creator_hash("UCabc123")) == sensitivity.CREATOR_HASH_LENGTH
    assert re.fullmatch(r"[0-9a-f]{24}", sensitivity.creator_hash("UCabc123"))


# ---------- 제외 ----------
def _one_group(documents: int, **kwargs) -> Population:
    videos = (_video("v1", "2025Q1"),)
    return Population(videos, (_reaction("v1", "d1", counted=1, documents=documents, **kwargs),))


def test_excluding_a_comment_takes_the_whole_copy_pasted_group_with_it():
    """복붙 한 쪽만 빼면 `unique_ratio` 의 분자와 분모가 다른 모집단을 세게 된다."""
    made, _panel, _quarters = sensitivity.counts(_one_group(3))
    assert made[COMMENT].mentions[("백탁", "2025Q1")] == 1
    assert made[COMMENT].raw[("백탁", "2025Q1")] == 3
    dropped, _panel, _quarters = sensitivity.counts(_one_group(3), drop_reactions=frozenset({("v1", "d1")}))
    assert dropped[COMMENT].mentions == {}
    assert dropped[COMMENT].raw == {}


def test_dropping_a_video_drops_the_comments_that_hang_under_it():
    """분기 귀속이 부모 영상이라, 부모가 빠진 댓글은 어느 분기에도 속하지 못한다 (코퍼스 규칙 3)."""
    made, _panel, _quarters = sensitivity.counts(_one_group(1), drop_videos=frozenset({"v1"}))
    assert made[VIDEO].documents == {}
    assert made[COMMENT].documents == {}


def test_the_cutoff_makes_the_later_quarters_look_like_they_never_happened():
    population = Population(
        (_video("v1", "2024Q1"), _video("v2", "2025Q1")),
        (_reaction("v2", "d1"),),
    )
    made, _panel, quarters = sensitivity.counts(population, cutoff="2024Q4")
    assert quarters == ["2024Q1"]
    assert made[COMMENT].documents == {}


def test_the_expert_rows_arrive_only_when_the_role_set_asks_for_them():
    population = Population(
        (_video("v1", "2025Q1"), _video("e1", "2025Q1", channel_id="e", panel_role=sensitivity.EXPERT)),
        (),
    )
    _made, _panel, product = sensitivity.counts(population)
    assert sum(sensitivity.counts(population)[0][VIDEO].documents.values()) == 1
    assert sum(sensitivity.counts(population, roles=sensitivity.ALL_ROLES)[0][VIDEO].documents.values()) == 2
    assert product == ["2025Q1"]


def test_the_counterfactual_panel_label_has_no_seat_in_the_stored_vocabulary():
    """이 산출이 표가 되지 않는 이유가 문장이 아니라 DDL 에서 읽힌다: 022 의 닫힌 어휘에 자리가 없다."""
    ddl = DDL.read_text(encoding="utf-8")
    assert "panel_role IN ('product','expert')" in ddl
    assert sensitivity.ALL_ROLES_LABEL not in ddl
    made = sensitivity.metrics(
        Population((_video("v1", "2025Q1"),), ()), TOPICS, FRAME, roles=sensitivity.ALL_ROLES
    )
    assert {row.panel_role for row in made} == {sensitivity.ALL_ROLES_LABEL}
