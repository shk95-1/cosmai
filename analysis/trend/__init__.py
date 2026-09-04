"""The five formulas of the quarterly time series — `contracts/interfaces.md` §Formulas is canonical (#5).

The rules come from ydc `analysis/slices/ydc/trend.py` (v0.2) and were written over rather than imported
from the slice (the way `analysis/retrieval/` did it). This module knows no DB: it takes counts (`Counts` ·
`VideoPanel`) and produces rows, so the same formulas run on the corpus tables and on the raw collection CSV
with the same code -- that is where the golden comparison stands.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from analysis.types import MetricsTopicQuarterRow

# The definition revision ydc puts on every row. It is the name of a definition (TEAM_DECISIONS_v0.2) rather
# than a value, so it is carried over as it is.
METRIC_VERSION = "v0.2"
# The sample gate and the condition of velocity. The CHECK of 022 enforces the equality of this number with
# sample_ok.
MIN_MENTIONS = 5
# The cap on the window length of persistence. Not the four newest quarters globally but the four ending at
# that row's quarter.
WINDOW_QUARTERS = 4
# Stored decimal places (interfaces.md §Formulas, "stored decimal places"; 022's numeric(p,s)).
DIGITS: Mapping[str, int] = {
    "composition": 5,
    "velocity_yoy": 4,
    "persistence": 3,
    "unique_ratio": 4,
    "channel_diffusion": 3,
}


@dataclass(frozen=True)
class Counts:
    """The counts of one `source`. The five formulas run on these four alone, independent of where the counts
    came from."""

    documents: Mapping[str, int]  # quarter -> documents of that population in that quarter
    mentions: Mapping[tuple[str, str], int]  # (topic, quarter) -> mentions
    raw: Mapping[tuple[str, str], int]  # (topic, quarter) -> mentions with duplicates (unique_ratio denom)
    channels: Mapping[tuple[str, str], int]  # (topic, quarter) -> channels producing that topic on the source


@dataclass(frozen=True)
class VideoPanel:
    """The channel distribution coming from the videos. `channel_diffusion` uses it for both terms and so
    does not depend on the source."""

    denom_channels: Mapping[str, int]  # quarter -> panel channels that entered the output in that quarter
    per_channel: Mapping[tuple[str, str], Mapping[str, int]]  # (topic, quarter) -> {channel: videos}


def previous_year_quarter(quarter: str) -> str:
    """A seasonal product, so the comparison partner is not the adjacent quarter but the same quarter of the
    previous year (formats.md §Time)."""
    return f"{int(quarter[:4]) - 1}Q{quarter[5]}"


def entropy(counts: Sequence[int]) -> float:
    """Normalized Shannon entropy -- 0 when one channel monopolizes it, 1 when it is spread evenly over
    those channels."""
    total = sum(counts)
    if total == 0 or len(counts) <= 1:
        return 0.0
    probs = [c / total for c in counts if c]
    return -sum(p * math.log(p) for p in probs) / math.log(len(counts))


def diffusion(distribution: Mapping[str, int], denom_channels: int) -> float:
    """Half breadth (how many channels produced it) and half evenness (does one channel monopolize it) --
    either one on its own splits them apart."""
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
    """One dense grid: trend_use topics x every quarter present in that output; a cell with 0 mentions is a
    row too."""
    quarters = sorted(counts.documents)
    # The denominator is the sum of mentions of that quarter's trend_use topics -- that sum has to close for
    # the GROUP BY of the stored table to hold.
    totals = {q: sum(counts.mentions.get((t, q), 0) for t in topics) for q in quarters}
    composition = {
        (t, q): (counts.mentions.get((t, q), 0) / totals[q] if totals[q] else 0.0)
        for t in topics
        for q in quarters
    }
    # The "whole period" of the baseline includes quarters with 0 mentions -- drop the 0 cells and the
    # persistence of every topic rises.
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
                    # A cell whose duplicate-inclusive mention count is 0 is 1, not NULL (§Formulas).
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
    """Emitted only when both quarters clear the gate, so a thin sample is not read as a surge."""
    previous = previous_year_quarter(quarter)
    if previous not in totals:
        return None
    if counts.mentions.get((topic, quarter), 0) < MIN_MENTIONS:
        return None
    if counts.mentions.get((topic, previous), 0) < MIN_MENTIONS:
        return None
    delta = math.log(composition[(topic, quarter)]) - math.log(composition[(topic, previous)])
    return round(delta, DIGITS["velocity_yoy"])
