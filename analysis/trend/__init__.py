"""분기 시계열의 다섯 수식 — `contracts/interfaces.md` §수식 이 정본이다 (포크 #5).

규칙의 출처는 ydc `analysis/slices/ydc/trend.py`(v0.2)이고, 슬라이스를 import 하지 않고 옮겨 적었다
(`analysis/retrieval/` 가 쓴 방식). 이 모듈은 DB 를 모른다: 셈(`Counts`·`VideoPanel`)을 받아 행을
만들 뿐이라, 같은 수식을 코퍼스 표에서도 원 수집 CSV 에서도 같은 코드로 돌릴 수 있다 — 골든 대조가
성립하는 자리가 그것이다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from analysis.types import MetricsTopicQuarterRow

# ydc 가 모든 행에 다는 정의 판본. 값이 아니라 정의(TEAM_DECISIONS_v0.2)의 이름이라 그대로 옮긴다.
METRIC_VERSION = "v0.2"
# 표본 게이트이자 velocity 의 조건. 022 의 CHECK 이 이 수와 sample_ok 의 등식을 강제한다.
MIN_MENTIONS = 5
# persistence 의 창 길이 상한. 전역 최신 4분기가 아니라 그 행의 분기에서 끝나는 4개다.
WINDOW_QUARTERS = 4
# 저장 자리수 (interfaces.md §수식 "저장 자리수", 022 의 numeric(p,s)).
DIGITS: Mapping[str, int] = {
    "composition": 5,
    "velocity_yoy": 4,
    "persistence": 3,
    "unique_ratio": 4,
    "channel_diffusion": 3,
}


@dataclass(frozen=True)
class Counts:
    """한 `source` 의 셈. 다섯 수식은 이 넷 위에서만 돌아, 셈이 어디서 왔는지와 무관하다."""

    documents: Mapping[str, int]  # 분기 -> 그 분기 그 모집단의 문서 수
    mentions: Mapping[tuple[str, str], int]  # (주제, 분기) -> 언급 수
    raw: Mapping[tuple[str, str], int]  # (주제, 분기) -> 중복 포함 언급 수 (unique_ratio 의 분모)
    channels: Mapping[tuple[str, str], int]  # (주제, 분기) -> 그 source 에서 그 주제를 낸 채널 수


@dataclass(frozen=True)
class VideoPanel:
    """영상에서 나온 채널 분포. `channel_diffusion` 은 두 항 다 이것을 써서 source 에 의존하지 않는다."""

    denom_channels: Mapping[str, int]  # 분기 -> 그 분기에 산출에 든 패널 채널 수
    per_channel: Mapping[tuple[str, str], Mapping[str, int]]  # (주제, 분기) -> {채널: 영상 수}


def previous_year_quarter(quarter: str) -> str:
    """계절 상품이라 비교 상대가 인접 분기가 아니라 전년 동분기다 (formats.md §시간)."""
    return f"{int(quarter[:4]) - 1}Q{quarter[5]}"


def entropy(counts: Sequence[int]) -> float:
    """정규화 섀넌 엔트로피 — 한 채널이 독점하면 0, 그 채널들에 고르게 퍼지면 1이다."""
    total = sum(counts)
    if total == 0 or len(counts) <= 1:
        return 0.0
    probs = [c / total for c in counts if c]
    return -sum(p * math.log(p) for p in probs) / math.log(len(counts))


def diffusion(distribution: Mapping[str, int], denom_channels: int) -> float:
    """넓이(몇 채널이 냈나)와 고름(한 채널이 독점하나)을 반씩 섞는다 — 둘 중 하나만으로는 갈린다."""
    breadth = len(distribution) / denom_channels if denom_channels else 0.0
    return 0.5 * breadth + 0.5 * entropy(list(distribution.values()))


def rows(
    topics: Sequence[str],
    counts: Counts,
    panel: VideoPanel,
    *,
    run_id: int,
    scope: str,
    source: str,
    content_type: str,
    panel_version: int,
    panel_role: str,
) -> list[MetricsTopicQuarterRow]:
    """조밀한 격자 한 벌: trend_use 주제 × 그 산출에 존재하는 분기 전부, 언급 0 셀도 행이다."""
    quarters = sorted(counts.documents)
    # 분모는 그 분기 trend_use 주제들의 언급 합이다 — 이 합이 닫혀야 저장된 표의 GROUP BY 가 맞는다.
    totals = {q: sum(counts.mentions.get((t, q), 0) for t in topics) for q in quarters}
    composition = {
        (t, q): (counts.mentions.get((t, q), 0) / totals[q] if totals[q] else 0.0)
        for t in topics
        for q in quarters
    }
    # 기준선의 "전 기간"은 언급 0 분기도 포함한다 — 0 셀을 빼면 모든 주제의 persistence 가 올라간다.
    baseline = {t: statistics.median([composition[(t, q)] for q in quarters]) for t in topics}

    built: list[MetricsTopicQuarterRow] = []
    for topic in topics:
        for index, quarter in enumerate(quarters):
            mentions = counts.mentions.get((topic, quarter), 0)
            window = quarters[max(0, index - WINDOW_QUARTERS + 1) : index + 1]
            persist = sum(1 for w in window if composition[(topic, w)] > baseline[topic])
            distribution = panel.per_channel.get((topic, quarter), {})
            duplicated = counts.raw.get((topic, quarter), 0)
            built.append(
                MetricsTopicQuarterRow(
                    run_id=run_id,
                    scope=scope,
                    topic_key=topic,
                    quarter=quarter,
                    source=source,
                    content_type=content_type,
                    panel_version=panel_version,
                    panel_role=panel_role,
                    mentions=mentions,
                    documents=counts.documents[quarter],
                    quarter_mentions=totals[quarter],
                    denom_channels=panel.denom_channels.get(quarter, 0),
                    composition=round(composition[(topic, quarter)], DIGITS["composition"]),
                    velocity_yoy=_velocity(topic, quarter, counts, composition, totals),
                    persistence=round(persist / len(window), DIGITS["persistence"]),
                    persist_quarters=persist,
                    window_quarters=len(window),
                    # 중복 포함 언급 수가 0인 칸은 NULL 이 아니라 1 이다 (§수식).
                    unique_ratio=round(mentions / duplicated if duplicated else 1.0, DIGITS["unique_ratio"]),
                    channel_count=counts.channels.get((topic, quarter), 0),
                    channel_diffusion=round(
                        diffusion(distribution, panel.denom_channels.get(quarter, 0)),
                        DIGITS["channel_diffusion"],
                    ),
                    sample_ok=mentions >= MIN_MENTIONS,
                )
            )
    return built


def _velocity(
    topic: str,
    quarter: str,
    counts: Counts,
    composition: Mapping[tuple[str, str], float],
    totals: Mapping[str, int],
) -> float | None:
    """표본 부족을 급등으로 읽지 않으려고 양쪽 분기가 다 게이트를 넘을 때만 낸다."""
    previous = previous_year_quarter(quarter)
    if previous not in totals:
        return None
    if counts.mentions.get((topic, quarter), 0) < MIN_MENTIONS:
        return None
    if counts.mentions.get((topic, previous), 0) < MIN_MENTIONS:
        return None
    delta = math.log(composition[(topic, quarter)]) - math.log(composition[(topic, previous)])
    return round(delta, DIGITS["velocity_yoy"])
