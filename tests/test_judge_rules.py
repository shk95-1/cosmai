"""판정 순서 일곱 갈래와 두 점수 — `contracts/interfaces.md` §판정 (포크 #40).

DB 를 타지 않는다. 판정은 지표 행 위의 순수 함수라, 규칙이 갈리는 자리는 픽스처가 아니라 그 행
하나에서 보여야 한다. 골든(`tests/test_judge_golden.py`)이 값 전체를 지고, 이 파일은 **왜 그 값인지**를
진다 -- 표본에 우연히 나타나지 않는 갈래(전량 산출에 `지속 인기` 는 있지만 표본 골든에는 없다)도 여기
서는 서 있다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from analysis.judge import (
    ABOVE_HALF_PEAK,
    COMMENT,
    DIFFUSING,
    DIFFUSION_TAU,
    DIGITS,
    EMERGING,
    EVIDENCE_FLOOR,
    FADING,
    HELD,
    MIN_DOCUMENTS,
    NEW_TOPIC_MAX_SHARE,
    NO_PRIOR_YEAR,
    NO_RULE,
    RUNNING,
    SPIKE,
    STICKY,
    SURGE,
    TAU,
    THIN,
    TREND_TYPES,
    VIDEO,
    W_EVIDENCE,
    W_SCORE,
    WITHIN_TAU_SHORT_PERSISTENCE,
    SparseGrid,
    evidence_strength,
    judge,
    percentile_rank,
)
from analysis.types import MetricsTopicQuarterRow

INTERFACES = Path(__file__).resolve().parents[1] / "contracts" / "interfaces.md"
QUARTERS = ("2023Q4", "2024Q1", "2024Q2", "2024Q3", "2024Q4", "2025Q1")


def metric(topic: str, quarter: str, **kw: Any) -> MetricsTopicQuarterRow:
    """근거가 넉넉하고 변화가 없는 셀 하나. 갈래마다 이 기본에서 한 칸씩만 움직인다."""
    base: dict[str, Any] = dict(
        run_id=1, scope="선블록", topic_key=topic, quarter=quarter, source=VIDEO,
        content_type="long_form", panel_version=1, panel_role="product",
        mentions=20, documents=100, quarter_mentions=200, denom_channels=10,
        composition=0.10, velocity_yoy=0.0, persistence=1.0, persist_quarters=4,
        window_quarters=4, unique_ratio=1.0, channel_count=8, channel_diffusion=0.5,
        sample_ok=True,
    )  # fmt: skip
    base.update(kw)
    return MetricsTopicQuarterRow(**base)


def series(**per_quarter: dict[str, Any]) -> list[MetricsTopicQuarterRow]:
    """한 주제의 한 소스 시계열. 격자를 조밀하게 두는 것이 판정의 전제다."""
    return [metric("백탁", q, **per_quarter.get(q, {})) for q in QUARTERS]


def decided(rows: list[MetricsTopicQuarterRow]) -> dict[tuple[str, str, str], Any]:
    return {(r.topic_key, r.quarter, r.source): r for r in judge(rows)}


def verdict(rows: list[MetricsTopicQuarterRow], quarter: str = "2024Q4") -> str:
    return decided(rows)[("백탁", quarter, VIDEO)].trend_type


# ---------- 어휘 ----------
def test_the_seven_types_are_the_seven_the_contract_names():
    """유형이 하나 늘거나 이름이 바뀌면 그것은 계약 변경이지 구현 변경이 아니다."""
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    line = next(text for text in lines if text.strip().startswith("유형 일곱:"))
    assert set(TREND_TYPES) == set(re.findall(r"`([^`]+)`", line))
    assert len(TREND_TYPES) == 7


def test_the_two_non_verdicts_are_not_types():
    assert HELD not in TREND_TYPES and RUNNING not in TREND_TYPES


# ---------- 판정 순서 ----------
def test_the_last_quarter_of_a_source_is_never_decided():
    """진행 중이라 문서 수가 덜 찼다 -- 마지막 분기를 확정하면 그 절단이 하락으로 읽힌다."""
    assert verdict(series(), quarter=QUARTERS[-1]) == RUNNING


def test_a_cell_under_the_evidence_floor_is_thin():
    thin = series(**{"2024Q4": {"channel_count": 0, "unique_ratio": 0.0, "mentions": 5}})
    assert decided(thin)[("백탁", "2024Q4", VIDEO)].evidence_strength < EVIDENCE_FLOOR
    assert verdict(thin) == THIN


def test_a_cell_under_the_document_gate_is_thin():
    assert verdict(series(**{"2024Q4": {"mentions": MIN_DOCUMENTS - 1, "sample_ok": False}})) == THIN


def test_a_topic_whose_previous_three_quarters_were_tiny_is_emerging():
    quiet = {"composition": NEW_TOPIC_MAX_SHARE / 2}
    rows = series(**{q: quiet for q in ("2024Q1", "2024Q2", "2024Q3")})
    assert verdict(rows) == EMERGING


def test_emerging_needs_two_channels():
    quiet = {"composition": NEW_TOPIC_MAX_SHARE / 2}
    rows = series(**{**{q: quiet for q in ("2024Q1", "2024Q2", "2024Q3")}, "2024Q4": {"channel_count": 1}})
    assert verdict(rows) != EMERGING


def test_emerging_is_decided_before_the_missing_velocity_gate():
    """새로 나타난 주제는 전년 동분기 표본이 없는 것이 정상이다 -- 보류로 흘리면 이 유형이 안 선다."""
    quiet = {"composition": NEW_TOPIC_MAX_SHARE / 2}
    rows = series(**{**{q: quiet for q in ("2024Q1", "2024Q2", "2024Q3")}, "2024Q4": {"velocity_yoy": None}})
    row = decided(rows)[("백탁", "2024Q4", VIDEO)]
    assert row.trend_type == EMERGING
    # velocity 가 없으면 점수 집합 밖이라 판정은 섰는데 점수는 NULL 이다 (전량 실측 2셀).
    assert row.judged and row.opportunity_score is None


def test_a_cell_without_a_prior_year_is_held_with_a_reason():
    row = decided(series(**{"2024Q4": {"velocity_yoy": None}}))[("백탁", "2024Q4", VIDEO)]
    assert (row.trend_type, row.hold_reason) == (HELD, NO_PRIOR_YEAR)


def test_surge_and_spike_split_on_persist_quarters():
    fast = {"velocity_yoy": TAU + 0.1}
    assert verdict(series(**{"2024Q4": {**fast, "persist_quarters": 1}})) == SPIKE
    assert verdict(series(**{"2024Q4": {**fast, "persist_quarters": 2}})) == SURGE


def test_exactly_tau_is_not_a_surge():
    """경계가 `>` 인지 `>=` 인지가 곧 유형이다."""
    assert verdict(series(**{"2024Q4": {"velocity_yoy": TAU}})) != SURGE


def test_channel_diffusion_must_beat_the_diffusion_tau():
    """0 으로 두면 아무리 작은 증가도 참이라 판정이 이 유형 하나로 쏠린다 (전량 실측 52/89셀)."""
    prior = {"channel_diffusion": 0.5}
    crept = series(**{"2023Q4": prior, "2024Q4": {"channel_diffusion": 0.5 + DIFFUSION_TAU / 2}})
    leapt = series(**{"2023Q4": prior, "2024Q4": {"channel_diffusion": 0.5 + DIFFUSION_TAU * 2}})
    assert verdict(crept) != DIFFUSING
    assert verdict(leapt) == DIFFUSING


def test_sticky_needs_three_quarters_over_the_baseline():
    assert verdict(series(**{"2024Q4": {"persist_quarters": 3}})) == STICKY
    held = decided(series(**{"2024Q4": {"persist_quarters": 2}}))[("백탁", "2024Q4", VIDEO)]
    assert (held.trend_type, held.hold_reason) == (HELD, WITHIN_TAU_SHORT_PERSISTENCE)


def test_fading_needs_both_the_drop_and_half_the_peak():
    """`사라짐` 이 두 조건을 함께 요구해서 가장 큰 하락이 보류로 떨어지는 구멍이 실제로 있다."""
    dropped = {"velocity_yoy": -TAU - 0.1}
    gone = series(**{"2024Q4": {**dropped, "composition": 0.04}})
    assert verdict(gone) == FADING
    still_big = decided(series(**{"2024Q4": {**dropped, "composition": 0.06}}))[("백탁", "2024Q4", VIDEO)]
    assert (still_big.trend_type, still_big.hold_reason) == (HELD, ABOVE_HALF_PEAK)


def test_a_cell_that_matches_no_rule_says_so():
    rows = series(**{"2024Q4": {"velocity_yoy": TAU + 0.1, "persist_quarters": 0}})
    # velocity > tau 는 언제나 유형을 주므로, 규칙 미해당은 그 위의 갈래를 다 비껴간 자리다.
    quiet = decided(series(**{"2024Q4": {"velocity_yoy": -TAU - 0.1, "composition": 0.10}}))
    assert verdict(rows) == SURGE
    assert quiet[("백탁", "2024Q4", VIDEO)].hold_reason in (ABOVE_HALF_PEAK, NO_RULE)


def test_judged_is_the_six_types_that_are_not_thin():
    rows = decided(series(**{"2024Q4": {"persist_quarters": 3}}))
    assert rows[("백탁", "2024Q4", VIDEO)].judged
    assert not rows[("백탁", QUARTERS[-1], VIDEO)].judged
    assert not decided(series(**{"2024Q4": {"mentions": 1, "sample_ok": False}}))[
        ("백탁", "2024Q4", VIDEO)
    ].judged


# ---------- evidence_strength 와 세 번째 채널 비율 ----------
def test_the_three_weights_are_a_hundred_points():
    assert evidence_strength(1.0, 1.0, 1.0) == pytest.approx(100.0)
    assert evidence_strength(0.0, 1.0, 1.0) == pytest.approx(W_EVIDENCE["channels"] + W_EVIDENCE["unique"])


def test_no_term_can_be_worth_more_than_its_weight():
    assert evidence_strength(2.0, 3.0, 4.0) == evidence_strength(1.0, 1.0, 1.0)


def test_percentile_rank_gives_the_middle_of_a_tie():
    assert percentile_rank([1, 2, 3, 4], 1) == 0.125
    assert percentile_rank([1, 2, 3, 4], 4) == 0.875
    assert percentile_rank([5, 5, 5, 5], 5) == 0.5
    assert percentile_rank([7], 7) == 1.0


def test_the_evidence_channel_term_is_channel_count_over_denom_channels_not_the_diffusion():
    """§판정 이 갈라 적은 **세 번째** 채널 비율이다. 댓글 행에서만 갈리므로 영상만 보면 안 보인다 --
    `channel_diffusion` 은 두 소스가 같은 값이고 이 항은 그 행의 `channel_count` 를 쓴다."""
    video = metric("백탁", "2024Q1", source=VIDEO, channel_count=8, channel_diffusion=0.5)
    comment = metric("백탁", "2024Q1", source=COMMENT, channel_count=2, channel_diffusion=0.5)
    made = decided([video, comment])
    seen = (made[("백탁", "2024Q1", VIDEO)], made[("백탁", "2024Q1", COMMENT)])
    assert seen[0].evidence_strength != seen[1].evidence_strength
    for row, count in zip(seen, (8, 2), strict=True):
        expected = evidence_strength(percentile_rank([20], 20), count / 10, 1.0)
        assert row.evidence_strength == round(expected, DIGITS["evidence_strength"])


def test_the_evidence_channel_term_saturates_when_the_row_has_more_channels_than_the_denominator():
    lopsided = metric("백탁", "2024Q1", channel_count=40, denom_channels=10)
    assert decided([lopsided])[("백탁", "2024Q1", VIDEO)].evidence_strength == pytest.approx(100.0)


# ---------- opportunity_score ----------
def test_the_score_is_normalised_inside_the_source():
    """제품군 내 0~100 이라 그 산출의 최저가 0, 최고가 100 이다 -- run 상대인 것이 이 눈금의 뜻이다."""
    rows = [
        metric("백탁", q, velocity_yoy=v, persist_quarters=3)
        for q, v in zip(QUARTERS, (0.0, 0.1, 0.2, 0.3, -0.2, 0.0), strict=True)
    ]
    scored = [r.opportunity_score for r in judge(rows) if r.opportunity_score is not None]
    assert min(scored) == 0.0 and max(scored) == 100.0


def test_the_score_is_null_outside_the_scored_set():
    """0 은 "가장 낮은 기회"이고 NULL 은 "점수를 매기지 않았다"다."""
    rows = series(**{"2024Q4": {"mentions": 1, "sample_ok": False}, "2024Q2": {"persist_quarters": 3}})
    made = decided(rows)
    assert made[("백탁", "2024Q4", VIDEO)].opportunity_score is None
    assert made[("백탁", QUARTERS[-1], VIDEO)].opportunity_score is None


def test_the_four_weights_of_the_score_sum_to_one():
    assert sum(W_SCORE.values()) == pytest.approx(1.0)


# ---------- gap_pp ----------
def test_gap_pp_is_the_same_number_on_both_source_rows():
    """(주제, 분기) 단위 사실이라 두 행이 같은 값을 든다 -- 갭 자체가 신호라 가중합으로 섞지 않는다."""
    rows = [
        metric("백탁", "2024Q1", source=VIDEO, composition=0.10),
        metric("백탁", "2024Q1", source=COMMENT, composition=0.16),
    ]
    made = decided(rows)
    assert made[("백탁", "2024Q1", VIDEO)].gap_pp == 6.0
    assert made[("백탁", "2024Q1", COMMENT)].gap_pp == 6.0


def test_gap_pp_is_null_when_one_source_has_no_such_cell():
    made = decided([metric("백탁", "2024Q1", source=VIDEO)])
    assert made[("백탁", "2024Q1", VIDEO)].gap_pp is None


def test_single_source_is_true_because_the_source_count_gate_is_off():
    """영상과 댓글은 상호 검증 소스가 아니라 성격이 다른 두 계열이다 -- 게이트가 꺼져 있다는 사실이
    행에 남는다."""
    made = decided([metric("백탁", "2024Q1", source=VIDEO), metric("백탁", "2024Q1", source=COMMENT)])
    assert all(row.single_source for row in made.values())


# ---------- 전제 ----------
def test_a_sparse_grid_is_refused():
    """직전 3분기·전년 동분기·전 기간 최고를 이력에서 꺼내므로, 빠진 칸은 0 이 아니라 "모른다"다."""
    rows = [metric("백탁", q) for q in QUARTERS] + [metric("자외선차단지수", "2024Q1")]
    with pytest.raises(SparseGrid):
        judge(rows)
