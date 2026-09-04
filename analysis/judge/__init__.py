"""트렌드 유형 7종 판정과 두 점수 — `contracts/interfaces.md` §판정 이 정본이다 (포크 #40).

The rules come from ydc `judge.py` (shk95-1/cosmai-ydc-old `v0.1.0` `02440ab`, the TEAM_DECISIONS v0.2
definition; unchanged through the import pin `v0.4.0`, `contracts/versioning.md`) and were written over
rather than imported from the pinned copy `analysis/slices/ydc/` (deleted, #9) (the way `analysis/trend/`
did it). This module knows no DB: it takes metric rows
(`MetricsTopicQuarterRow`) and produces judgement rows, so the same rules run on the stored table and on the
raw collection CSV with the same code -- that is where the golden comparison stands.

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

# The definition revision of the judgement. Being separate from `metric` is the point -- changing only the
# criteria without recounting the metrics is why the two stages are apart, and this one key is what moves
# then (`contracts/versioning.md`).
JUDGEMENT_VERSION = "v0.2"

# --- 상수 (근거와 채택 판단은 계약 §판정 의 표) ---
TAU = 0.35
DIFFUSION_TAU = 0.089
EVIDENCE_FLOOR = 50.0
NEW_TOPIC_MAX_SHARE = 0.01
# The same number as the sample gate of the metrics table. Redefining it here could drift quietly from the
# CHECK of 022.
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

# --- vocabulary ---
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
    """A ratio column is empty. 024 leaves those columns nullable, but a judgement only means something on a
    row that is filled."""


# The complete key of a metric row. A judgement attaches 1:1 to that row by this key (the FK of 024).
Key = tuple[int, str, str, str, str, str, int, str]


def _key(row: MetricsTopicQuarterRow) -> Key:
    return (
        row.run_id, row.scope, row.topic_key, row.quarter,
        row.source, row.content_type, row.panel_version, row.panel_role,
    )  # fmt: skip


def _population(row: MetricsTopicQuarterRow) -> tuple[int, str, str, int, str, str]:
    """The range the percentile and the 0-100 normalization run over. One output set of one source."""
    return (row.run_id, row.scope, row.content_type, row.panel_version, row.panel_role, row.source)


def _cell(row: MetricsTopicQuarterRow) -> tuple[int, str, str, int, str, str, str]:
    """The place with source taken out. `gap_pp` meets the two source rows here."""
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
    """The comparison is against the same quarter a year earlier rather than the adjacent quarter because
    suncare is a seasonal product."""
    return f"{int(quarter[:4]) - 1}Q{quarter[5]}"


def percentile_rank(sorted_values: Sequence[int], value: int) -> float:
    """Where value sits inside that source (0-1). With several equal values it gives the middle of that band.

    One absolute criterion is not used because the scale differs per source -- measured on this corpus the
    video median is 16 against a comment median of 62, so one criterion would put the video cells wholesale
    at the bottom.
    """
    if len(sorted_values) <= 1:
        return 1.0
    below = bisect.bisect_left(sorted_values, value)
    upto = bisect.bisect_right(sorted_values, value)
    return ((below + upto) / 2) / len(sorted_values)


def evidence_strength(doc_rank: float, channel_ratio: float, unique_ratio: float) -> float:
    """0-100. The three terms are each put on 0-1 and summed with weights.

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
        # Requiring both conditions together drops the largest fall here. The rule is left as it is and only
        # the reason is recorded.
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
    """The four terms are put on 0-1 and summed with weights, then normalized to 0-100 inside that source --
    a scale relative to the run."""
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
    """Comment share minus video share. The gap itself is the signal, so the two series are not mixed by a
    weighted sum."""
    compositions: dict[tuple[int, str, str, int, str, str, str], dict[str, float]] = defaultdict(dict)
    for row in rows:
        compositions[_cell(row)][row.source] = _number(row, "composition")
    return {
        cell: round(100 * (sides[COMMENT] - sides[VIDEO]), DIGITS["gap_pp"])
        for cell, sides in compositions.items()
        if COMMENT in sides and VIDEO in sides
    }


def judge(rows: Sequence[MetricsTopicQuarterRow]) -> list[TopicQuarterJudgementRow]:
    """Takes every metric row of one run and emits judgement rows under the same key (1:1, the FK of 024).

    One row on its own cannot be judged -- both the evidence-count percentile and the 0-100 of the
    opportunity score are settled only with the whole row set of that source. That is why the judgement is a
    run-level derivation.
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
