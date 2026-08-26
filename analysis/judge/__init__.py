"""트렌드 유형 7종 판정과 두 점수 — `contracts/interfaces.md` §판정 이 정본이다 (포크 #40).

규칙의 출처는 ydc `analysis/slices/ydc/judge.py`(v0.2)이고, 슬라이스를 import 하지 않고 옮겨 적었다
(`analysis/trend/` 가 쓴 방식). 이 모듈은 DB 를 모른다: 지표 행(`MetricsTopicQuarterRow`)을 받아 판정
행을 만들 뿐이라, 같은 규칙을 저장된 표에서도 원 수집 CSV 에서도 같은 코드로 돌릴 수 있다 — 골든
대조가 성립하는 자리가 그것이다.

판정 상수 다섯의 근거와 채택/재적합 판단은 계약 §판정 의 표가 지고, `tests/test_judge_constants.py`
가 그 표와 이 파일의 수를 대조한다. 값만 여기 있고 근거가 계약에 없으면 "왜 0.35 인가"에 답할 자리가
없다.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Mapping, Sequence

from analysis.trend import MIN_MENTIONS
from analysis.types import MetricsTopicQuarterRow, TopicQuarterJudgementRow

# 판정의 정의 판본. `metric` 과 따로 있는 것이 뜻이다 — 지표를 다시 세지 않고 기준만 바꾸는 것이
# 두 단계를 갈라 둔 이유이고, 그때 움직이는 것은 이 키 하나다 (`contracts/versioning.md`).
JUDGEMENT_VERSION = "v0.2"

# --- 상수 (근거와 채택 판단은 계약 §판정 의 표) ---
TAU = 0.35
DIFFUSION_TAU = 0.089
EVIDENCE_FLOOR = 50.0
NEW_TOPIC_MAX_SHARE = 0.01
# 지표 표의 표본 게이트와 같은 수다. 여기서 다시 정의하면 022 의 CHECK 과 조용히 갈릴 수 있다.
MIN_DOCUMENTS = MIN_MENTIONS
W_EVIDENCE: Mapping[str, float] = {"documents": 43.75, "channels": 31.25, "unique": 25.0}
W_SCORE: Mapping[str, float] = {
    "velocity": 0.35,
    "persistence": 0.25,
    "channel_diffusion": 0.20,
    "evidence_strength": 0.20,
}
# 저장 자리수 (계약 §판정 "판정 자리수", 024 의 numeric(p,s)).
DIGITS: Mapping[str, int] = {"evidence_strength": 1, "opportunity_score": 1, "gap_pp": 2}

# --- 어휘 ---
SURGE = "급상승"
FADING = "사라짐"
STICKY = "지속 인기"
SPIKE = "단기 피크"
EMERGING = "신규 등장"
DIFFUSING = "채널 확산"
THIN = "근거 부족"
HELD = "판정 보류"
RUNNING = "미확정(진행 중)"
# 일곱이 유형이고 둘은 판정하지 않았다는 말이다 (계약 §판정).
TREND_TYPES = (SURGE, FADING, STICKY, SPIKE, EMERGING, DIFFUSING, THIN)
NOT_A_VERDICT = (HELD, RUNNING)
UNJUDGED = (THIN, *NOT_A_VERDICT)

NO_PRIOR_YEAR = "no_prior_year"
ABOVE_HALF_PEAK = "above_half_peak"
WITHIN_TAU_SHORT_PERSISTENCE = "within_tau_short_persistence"
NO_RULE = "no_rule"
HOLD_REASONS = (NO_PRIOR_YEAR, ABOVE_HALF_PEAK, WITHIN_TAU_SHORT_PERSISTENCE, NO_RULE)

COMMENT = "youtube_comment"
VIDEO = "youtube_video"


class SparseGrid(LookupError):
    """판정은 조밀한 격자를 전제한다 — 직전 3분기·전년 동분기·전 기간 최고를 그 주제의 이력에서
    꺼내므로, 빠진 칸을 만나면 0 이 아니라 "모른다"를 만난다 (계약 §판정)."""


class MissingValue(LookupError):
    """비율 칸이 비어 있다. 024 는 그 칸들을 nullable 로 두지만 판정은 다 찬 행 위에서만 뜻이 있다."""


# 지표 행의 완전한 키. 판정이 이 키로 그 행에 1:1 로 붙는다 (024 의 FK).
Key = tuple[int, str, str, str, str, str, int, str]


def _key(row: MetricsTopicQuarterRow) -> Key:
    return (
        row.run_id, row.scope, row.topic_key, row.quarter,
        row.source, row.content_type, row.panel_version, row.panel_role,
    )  # fmt: skip


def _population(row: MetricsTopicQuarterRow) -> tuple[int, str, str, int, str, str]:
    """백분위와 0~100 정규화가 도는 범위. 한 source 의 산출 한 벌이다."""
    return (row.run_id, row.scope, row.content_type, row.panel_version, row.panel_role, row.source)


def _cell(row: MetricsTopicQuarterRow) -> tuple[int, str, str, int, str, str, str]:
    """source 를 뺀 자리. `gap_pp` 가 두 source 행을 여기서 만난다."""
    return (
        row.run_id, row.scope, row.content_type, row.panel_version,
        row.panel_role, row.topic_key, row.quarter,
    )  # fmt: skip


def _number(row: MetricsTopicQuarterRow, name: str) -> float:
    value = getattr(row, name)
    if value is None:
        raise MissingValue(f"{_key(row)} has no {name}")
    return float(value)


def previous_year_quarter(quarter: str) -> str:
    """비교 상대가 인접 분기가 아니라 전년 동분기인 것은 선케어가 계절 상품이기 때문이다."""
    return f"{int(quarter[:4]) - 1}Q{quarter[5]}"


def percentile_rank(sorted_values: Sequence[int], value: int) -> float:
    """그 source 안에서 value 가 놓인 위치(0~1). 같은 값이 여럿이면 그 구간의 중간을 준다.

    절대 기준을 하나 쓰지 않는 것은 소스별 스케일이 다르기 때문이다 — 이 코퍼스 실측으로 영상 중앙
    16 대 댓글 중앙 62 라, 한 기준을 쓰면 영상 셀이 통째로 낮게 나온다.
    """
    if len(sorted_values) <= 1:
        return 1.0
    below = bisect.bisect_left(sorted_values, value)
    upto = bisect.bisect_right(sorted_values, value)
    return ((below + upto) / 2) / len(sorted_values)


def evidence_strength(doc_rank: float, channel_ratio: float, unique_ratio: float) -> float:
    """0~100. 세 항을 각각 0~1 로 두고 가중합한다.

    `channel_ratio` 는 `channel_count / denom_channels` 이고, 이것은 `channel_diffusion` 이 쓰는 두
    채널 비율과 **또 다른 세 번째** 비율이다 (계약 §판정 의 표). 영상 행에서는 첫 두 비율이 우연히
    같은 수라, 갈리는 것은 댓글 행뿐이다.
    """
    return (
        W_EVIDENCE["documents"] * min(1.0, doc_rank)
        + W_EVIDENCE["channels"] * min(1.0, channel_ratio)
        + W_EVIDENCE["unique"] * min(1.0, unique_ratio)
    )


def _refuse_sparse(cells: Sequence[MetricsTopicQuarterRow], quarters: Sequence[str]) -> None:
    wanted = set(quarters)
    seen: dict[str, set[str]] = defaultdict(set)
    for cell in cells:
        seen[cell.topic_key].add(cell.quarter)
    thin = {topic: sorted(wanted - held) for topic, held in seen.items() if held != wanted}
    if thin:
        raise SparseGrid(f"the grid is not dense; these topics have no row in {thin}")


def _classify(
    cell: MetricsTopicQuarterRow,
    evidence: float,
    history: Mapping[str, MetricsTopicQuarterRow],
    quarters: Sequence[str],
    is_last: bool,
) -> tuple[str, str]:
    """판정 순서 — 위에서 먼저 걸리면 종료한다. 순서 자체가 정의다 (계약 §판정)."""
    if is_last:
        return RUNNING, ""
    if evidence < EVIDENCE_FLOOR or cell.mentions < MIN_DOCUMENTS:
        return THIN, ""

    index = quarters.index(cell.quarter)
    prior3 = quarters[max(0, index - 3) : index]
    if (
        prior3
        and all(_number(history[q], "composition") < NEW_TOPIC_MAX_SHARE for q in prior3)
        and cell.mentions >= MIN_DOCUMENTS
        and _number(cell, "channel_count") >= 2
    ):
        return EMERGING, ""

    velocity = cell.velocity_yoy
    # 이 게이트가 `신규 등장` 보다 뒤인 것이 뜻이다 — 새로 나타난 주제는 전년 동분기 표본이 없다.
    if velocity is None:
        return HELD, NO_PRIOR_YEAR
    velocity = float(velocity)
    persist = int(cell.persist_quarters or 0)

    if velocity > TAU:
        return (SPIKE if persist == 1 else SURGE), ""

    previous = history.get(previous_year_quarter(cell.quarter))
    if (
        previous is not None
        and _number(cell, "channel_diffusion") - _number(previous, "channel_diffusion") > DIFFUSION_TAU
    ):
        return DIFFUSING, ""

    if abs(velocity) <= TAU and persist >= 3:
        return STICKY, ""

    peak = max(_number(history[q], "composition") for q in quarters)
    if velocity < -TAU and _number(cell, "composition") < peak / 2:
        return FADING, ""

    if velocity < -TAU:
        # 두 조건을 함께 요구해서 가장 큰 하락이 여기로 떨어진다. 규칙은 그대로 두고 사유만 남긴다.
        return HELD, ABOVE_HALF_PEAK
    if abs(velocity) <= TAU and persist < 3:
        return HELD, WITHIN_TAU_SHORT_PERSISTENCE
    return HELD, NO_RULE


def _score(
    cells: Sequence[MetricsTopicQuarterRow],
    evidence: Mapping[Key, float],
    verdicts: Mapping[Key, tuple[str, str]],
    last: str,
) -> dict[Key, float]:
    """네 항을 0~1 로 맞춰 가중합한 뒤 그 source 안에서 0~100 으로 정규화한다 — run 상대인 눈금이다."""
    scored = [
        cell
        for cell in cells
        if cell.velocity_yoy is not None
        and cell.quarter != last
        and verdicts[_key(cell)][0] not in (THIN, HELD)
    ]
    if not scored:
        return {}
    velocities = [float(cell.velocity_yoy) for cell in scored]  # type: ignore[arg-type]
    low, high = min(velocities), max(velocities)
    span = (high - low) or 1.0
    raw = {
        _key(cell): (
            W_SCORE["velocity"] * (float(cell.velocity_yoy) - low) / span  # type: ignore[arg-type]
            + W_SCORE["persistence"] * _number(cell, "persistence")
            + W_SCORE["channel_diffusion"] * _number(cell, "channel_diffusion")
            + W_SCORE["evidence_strength"] * evidence[_key(cell)] / 100
        )
        for cell in scored
    }
    floor, ceiling = min(raw.values()), max(raw.values())
    reach = (ceiling - floor) or 1.0
    return {
        key: round(100 * (value - floor) / reach, DIGITS["opportunity_score"]) for key, value in raw.items()
    }


def _gaps(rows: Sequence[MetricsTopicQuarterRow]) -> dict[tuple[int, str, str, int, str, str, str], float]:
    """댓글 구성비 - 영상 구성비. 갭 자체가 신호라 두 계열을 가중합으로 섞지 않는다."""
    compositions: dict[tuple[int, str, str, int, str, str, str], dict[str, float]] = defaultdict(dict)
    for row in rows:
        compositions[_cell(row)][row.source] = _number(row, "composition")
    return {
        cell: round(100 * (sides[COMMENT] - sides[VIDEO]), DIGITS["gap_pp"])
        for cell, sides in compositions.items()
        if COMMENT in sides and VIDEO in sides
    }


def judge(rows: Sequence[MetricsTopicQuarterRow]) -> list[TopicQuarterJudgementRow]:
    """한 run 의 지표 행 전부를 받아 같은 키로 판정 행을 낸다 (1:1, 024 의 FK).

    행 하나만으로는 판정할 수 없다 — 근거 수 백분위도 기회 점수의 0~100 도 그 source 의 행 집합
    전체가 있어야 정해진다. 판정이 run 단위 파생인 이유가 그것이다.
    """
    populations: dict[tuple[int, str, str, int, str, str], list[MetricsTopicQuarterRow]] = defaultdict(list)
    for row in rows:
        populations[_population(row)].append(row)

    evidence: dict[Key, float] = {}
    verdicts: dict[Key, tuple[str, str]] = {}
    scores: dict[Key, float] = {}
    for cells in populations.values():
        quarters = sorted({cell.quarter for cell in cells})
        _refuse_sparse(cells, quarters)
        counts = sorted(cell.mentions for cell in cells)
        for cell in cells:
            evidence[_key(cell)] = round(
                evidence_strength(
                    percentile_rank(counts, cell.mentions),
                    _number(cell, "channel_count") / cell.denom_channels if cell.denom_channels else 0.0,
                    _number(cell, "unique_ratio"),
                ),
                DIGITS["evidence_strength"],
            )
        history: dict[str, dict[str, MetricsTopicQuarterRow]] = defaultdict(dict)
        for cell in cells:
            history[cell.topic_key][cell.quarter] = cell
        for cell in cells:
            verdicts[_key(cell)] = _classify(
                cell, evidence[_key(cell)], history[cell.topic_key], quarters, cell.quarter == quarters[-1]
            )
        scores.update(_score(cells, evidence, verdicts, quarters[-1]))

    gaps = _gaps(rows)
    made = [
        TopicQuarterJudgementRow(
            run_id=row.run_id,
            scope=row.scope,
            topic_key=row.topic_key,
            quarter=row.quarter,
            source=row.source,
            content_type=row.content_type,
            panel_version=row.panel_version,
            panel_role=row.panel_role,
            trend_type=verdicts[_key(row)][0],
            judged=verdicts[_key(row)][0] not in UNJUDGED,
            evidence_strength=evidence[_key(row)],
            # v1 은 언제나 true 다. 게이트가 꺼져 있다는 사실이 행에서 읽혀야 한다 (계약 §판정).
            single_source=True,
            opportunity_score=scores.get(_key(row)),
            gap_pp=gaps.get(_cell(row)),
            hold_reason=verdicts[_key(row)][1],
        )
        for row in rows
    ]
    made.sort(key=lambda row: (row.source, row.topic_key, row.quarter))
    return made
