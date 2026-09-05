"""The sensitivity and backtest trio — `contracts/interfaces.md` §Sensitivity is canonical (fork #41).

The rules come from ydc `panel_sensitivity.py` · `backtest.py` · `spam_ad_flags.py`
(shk95-1/cosmai-ydc-old `v0.1.0` `02440ab`, the TEAM_DECISIONS v0.2 definition; unchanged through the import
pin `v0.4.0`, `contracts/versioning.md`) and were written over rather than imported from the pinned copy
`analysis/slices/ydc/` (deleted, #9) (the way `analysis/trend/` and `analysis/judge/` did it).

All three **make no new metrics**: they rerun the same `analysis.trend` formulas and the same
`analysis.judge` rules with only the population changed, and measure whether the conclusion wobbles on that
choice. So what this module handles is always a **counterfactual population**, and those rows are not stored
-- neither `panel_role='product+expert'` nor "the output with the ad videos removed" has a place in the
closed vocabulary of 022 or in `analysis_run`.

This module knows no DB either: it takes one set of documents (`Population`) and emits rows, so the same code
runs on the corpus tables and on the raw collection CSV, and that is where the 1:1 comparison with the ydc
output stands.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from analysis.judge import (
    COMMENT,
    DIFFUSING,
    EMERGING,
    FADING,
    SPIKE,
    STICKY,
    SURGE,
    VIDEO,
    judge,
)
from analysis.trend import MIN_MENTIONS, WINDOW_QUARTERS, Counts, VideoPanel, rows
from analysis.types import (
    AdSensitivityRow,
    BacktestRow,
    MetricsTopicQuarterRow,
    PanelSensitivityRow,
)

# The minimum cell width (%p) for a flip. Go by sign alone and every cell hovering around 0 is caught as a
# flip (the contract's §Sensitivity).
MATERIAL_PP = 0.5
# The panel vocabulary. 022 knows only the two, and the name of the counterfactual population that joins them
# is not stored, so it lives here.
PRODUCT = "product"
EXPERT = "expert"
ALL_ROLES: tuple[str, ...] = (PRODUCT, EXPERT)
ALL_ROLES_LABEL = "product+expert"

# The lengths of the two backtest windows (quarters). The window length being 4, the same as `persistence`,
# is seasonality -- both the previous and the following window have to hold four full quarters for the summer
# effect to cancel.
HORIZON = WINDOW_QUARTERS
LOOKBACK = WINDOW_QUARTERS
# 방향이 있는 유형만 검증한다. `지속 인기`·`채널 확산` 은 방향 예측이 아니라 상태 서술이라 뺀다 --
# 넣으면 적중률이 부풀려진다.
UP_TYPES = (SURGE, EMERGING)
DOWN_TYPES = (FADING,)
PEAK_TYPES = (SPIKE,)
DIRECTIONAL = (*UP_TYPES, *DOWN_TYPES, *PEAK_TYPES)
STATEFUL = (STICKY, DIFFUSING)
RISE_HELD = "상승 유지"
FALL_HELD = "하락 유지"
PEAK_GONE = "피크 소멸"
ROSE = "상승"
FELL = "하락"

# The three things marked. ydc writes them as human-readable Korean labels; here the three are held by name
# (the contract's §Sensitivity).
AD_VIDEO = "ad_video"
CREATOR_COMMENT = "creator_comment"
PROMO_COMMENT = "promo_comment"
ALL_FLAGGED = "all_flagged"
VARIANTS = (AD_VIDEO, CREATOR_COMMENT, PROMO_COMMENT, ALL_FLAGGED)

# Sponsorship the disclosure field misses is caught by the wording of the description. `ppl` is given
# boundaries -- without them `apple` matches.
AD_RE = re.compile(
    r"유료\s*광고|협찬|광고\s*포함|#\s*광고|\bppl\b|제공\s*받|지원\s*받|무상\s*제공"
    r"|sponsor|paid\s+partnership|제작\s*지원",
    re.I,
)
PROMO_RE = re.compile(
    r"https?://|www\.|bit\.ly|coupa\.ng|smartstore|파트너스|판매\s*링크|구매\s*링크"
    r"|공동\s*구매|공구\s*링크|오픈\s*채팅|라이브\s*마켓|할인\s*코드|쿠폰\s*코드",
    re.I,
)
# It has to be the same formula as the hash the collector attaches to a comment author -- different, and
# operator comments quietly become 0.
CREATOR_HASH_PREFIX = "youtube:"
CREATOR_HASH_LENGTH = 24


class ShortHistory(LookupError):
    """There is not one quarter. The two windows (previous 4 · recent 4) have nowhere to stand, so it stops
    instead of emitting 0."""


def creator_hash(channel_id: str) -> str:
    """Rebuilds `author_channel_hash` from the channel id -- an exact match rather than a guess."""
    digest = hashlib.sha256(f"{CREATOR_HASH_PREFIX}{channel_id}".encode()).hexdigest()
    return digest[:CREATOR_HASH_LENGTH]


@dataclass(frozen=True)
class Video:
    """One long video in the population. `panel_role` being on the row is where the panel sensitivity
    stands."""

    item_id: str
    channel_id: str
    panel_role: str
    quarter: str
    topics: tuple[str, ...]
    declared: bool  # the uploader's own report (has_paid_product_placement). It has gaps (TEAM_DECISIONS §9)
    matched: bool  # the description wording matched

    @property
    def ad(self) -> bool:
        """The **union** of the two. Measured over the whole set, 254 disclosed and 407 matched, and over 200
        are matched by wording alone."""
        return self.declared or self.matched


@dataclass(frozen=True)
class Reaction:
    """A group of comments with the same text after normalization inside one video. Exclusion is per group --
    dropping only one side of a copy-paste makes the numerator and the denominator of `unique_ratio` count
    different populations (the same as ydc's `(video_id, text)` key)."""

    parent_item_id: str
    digest: str
    counted: int  # documents whose quality_flags is empty. The share for mentions and quarter documents
    documents: int  # documents counted including copy-paste. The share for the `unique_ratio` denominator
    topics: tuple[str, ...]
    creator: bool
    promo: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.parent_item_id, self.digest)


@dataclass(frozen=True)
class Population:
    """The two series of one snapshot. Every counterfactual is filtered out of this one set -- reading again
    per variant would mix the difference of reading times into the difference between variants."""

    videos: tuple[Video, ...]
    reactions: tuple[Reaction, ...]


@dataclass(frozen=True)
class Frame:
    """Where a counterfactual row records what it is a counterfactual of. It is not stored, so it does not
    carry the CHECK of 022."""

    run_id: int
    scope: str
    content_type: str
    panel_version: int


@dataclass(frozen=True)
class Backtest:
    rows: tuple[BacktestRow, ...]
    cutoffs: tuple[tuple[str, str], ...]  # (the cut quarter C, the judged quarter T = the quarter before C)
    base_rate: float  # share (%) of cells whose next 4 quarters beat the previous 4, judged or not
    base_level_rate: float  # the same against baseline B (previous window minus the judged quarter)
    base_cells: int


@dataclass(frozen=True)
class AdSensitivity:
    rows: tuple[AdSensitivityRow, ...]
    videos: int
    ad_videos: int
    declared: int
    matched: int
    comments: int  # comment documents counted including copy-paste
    creator_comments: int  # the number of operator comment **groups**
    promo_comments: int
    lost_cells: Mapping[str, int]  # variant -> cells whose judgement disappeared for want of sample


def previous_quarter(quarter: str) -> str:
    """One step back on the calendar. Not one step back in the observed list -- a missing quarter would
    quietly widen the window."""
    year, index = int(quarter[:4]), int(quarter[5])
    return f"{year}Q{index - 1}" if index > 1 else f"{year - 1}Q4"


def calendar_windows(quarters: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(the previous 4 quarters, the recent 4 quarters). The last quarter is in progress and outside both
    windows (the in-progress label of `analysis.judge`).

    ydc nailed these eight down as calendar values (`PRIOR` · `RECENT`). They are derived here because that
    list is a constant tied to this corpus, and deriving it gives the same eight quarters. The point is
    **counting by the calendar rather than by the index of the observed list**: a quarter whose row is missing
    for want of mentions still takes a slot in the window and enters as 0.
    """
    if not quarters:
        raise ShortHistory("the population has no quarter; the two windows have nowhere to stand")
    walk = previous_quarter(quarters[-1])
    window: list[str] = []
    for _ in range(2 * WINDOW_QUARTERS):
        window.append(walk)
        walk = previous_quarter(walk)
    window.reverse()
    return tuple(window[:WINDOW_QUARTERS]), tuple(window[WINDOW_QUARTERS:])


def _kept(
    population: Population,
    *,
    roles: Sequence[str],
    cutoff: str | None = None,
    drop_videos: frozenset[str] = frozenset(),
) -> list[Video]:
    """The video population of one counterfactual. When a video drops, its comments drop with it (the quarter
    is attributed to the parent)."""
    return [
        video
        for video in population.videos
        if video.panel_role in roles
        and video.item_id not in drop_videos
        and (cutoff is None or video.quarter <= cutoff)
    ]


def _video_counts(videos: Sequence[Video]) -> tuple[Counts, VideoPanel]:
    documents: dict[str, int] = defaultdict(int)
    in_quarter: dict[str, set[str]] = defaultdict(set)
    mentions: dict[tuple[str, str], int] = defaultdict(int)
    per_channel: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for video in videos:
        documents[video.quarter] += 1
        in_quarter[video.quarter].add(video.channel_id)
        for topic in video.topics:
            key = (topic, video.quarter)
            mentions[key] += 1
            per_channel[key][video.channel_id] = per_channel[key].get(video.channel_id, 0) + 1
    # A video counts one document once, so the duplicate-inclusive mention count equals the mention count --
    # unique_ratio is 1.
    return (
        Counts(
            dict(documents),
            dict(mentions),
            dict(mentions),
            {key: len(distribution) for key, distribution in per_channel.items()},
        ),
        VideoPanel(
            {quarter: len(found) for quarter, found in in_quarter.items()},
            {key: dict(distribution) for key, distribution in per_channel.items()},
        ),
    )


def _comment_counts(
    videos: Sequence[Video],
    reactions: Iterable[Reaction],
    drop_reactions: frozenset[tuple[str, str]] = frozenset(),
) -> Counts:
    parents = {video.item_id: video for video in videos}
    documents: dict[str, int] = defaultdict(int)
    mentions: dict[tuple[str, str], int] = defaultdict(int)
    raw: dict[tuple[str, str], int] = defaultdict(int)
    channels: dict[tuple[str, str], set[str]] = defaultdict(set)
    for reaction in reactions:
        parent = parents.get(reaction.parent_item_id)
        if parent is None or reaction.key in drop_reactions:
            continue
        documents[parent.quarter] += reaction.counted
        for topic in reaction.topics:
            key = (topic, parent.quarter)
            # The duplicate-inclusive denominator counts copy-paste and the numerator does not (corpus rule
            # 9).
            raw[key] += reaction.documents
            if reaction.counted:
                mentions[key] += reaction.counted
                channels[key].add(parent.channel_id)
    return Counts(dict(documents), dict(mentions), dict(raw), {key: len(f) for key, f in channels.items()})


def counts(
    population: Population,
    *,
    roles: Sequence[str] = (PRODUCT,),
    cutoff: str | None = None,
    drop_videos: frozenset[str] = frozenset(),
    drop_reactions: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[dict[str, Counts], VideoPanel, list[str]]:
    """The two count sets of one counterfactual, the channel distribution of the videos, and the quarter axis
    of that output (measured on the videos)."""
    videos = _kept(population, roles=roles, cutoff=cutoff, drop_videos=drop_videos)
    video_counts, panel = _video_counts(videos)
    comment_counts = _comment_counts(videos, population.reactions, drop_reactions)
    return (
        {VIDEO: video_counts, COMMENT: comment_counts},
        panel,
        sorted({video.quarter for video in videos}),
    )


def metrics(
    population: Population,
    topics: Sequence[str],
    frame: Frame,
    *,
    roles: Sequence[str] = (PRODUCT,),
    cutoff: str | None = None,
    drop_videos: frozenset[str] = frozenset(),
    drop_reactions: frozenset[tuple[str, str]] = frozenset(),
) -> list[MetricsTopicQuarterRow]:
    """One set of metric rows of one counterfactual. The formulas are `analysis.trend`'s as they are -- they
    are not written again here."""
    made_counts, panel, _quarters = counts(
        population,
        roles=roles,
        cutoff=cutoff,
        drop_videos=drop_videos,
        drop_reactions=drop_reactions,
    )
    label = roles[0] if len(roles) == 1 else "+".join(roles)
    made: list[MetricsTopicQuarterRow] = []
    for source in (VIDEO, COMMENT):
        # A series with not one document has no quarter axis -- 0 rows is right, and a grid filled with 0
        # turns an absent observation into an observed 0 (the baseline of `analysis.trend.rows` is settled on
        # those zeros).
        if not made_counts[source].documents:
            continue
        made.extend(
            rows(
                topics,
                made_counts[source],
                panel,
                run_id=frame.run_id,
                scope=frame.scope,
                source=source,
                content_type=frame.content_type,
                panel_version=frame.panel_version,
                panel_role=label,
            )
        )
    return made


def verdicts(made: Sequence[MetricsTopicQuarterRow]) -> dict[tuple[str, str, str], str]:
    """(source, 주제, 분기) -> 유형. 판정된 셀만 든다 -- 판정하지 않은 셀은 "바뀌었다"의 상대가 아니다."""
    return {(row.source, row.topic_key, row.quarter): row.trend_type for row in judge(made) if row.judged}


def _share(counted: Counts, topics: Sequence[str], window: Sequence[str]) -> dict[str, float]:
    """The share (%) among topics inside the window. The denominator is the sum of all topic mentions in that
    window."""
    total = sum(counted.mentions.get((topic, quarter), 0) for topic in topics for quarter in window)
    if not total:
        return dict.fromkeys(topics, 0.0)
    return {
        topic: 100 * sum(counted.mentions.get((topic, quarter), 0) for quarter in window) / total
        for topic in topics
    }


def _quarters_ok(counted: Counts, topic: str, quarters: Sequence[str]) -> int:
    return sum(1 for quarter in quarters if counted.mentions.get((topic, quarter), 0) >= MIN_MENTIONS)


def panel_sensitivity(population: Population, topics: Sequence[str]) -> list[PanelSensitivityRow]:
    """Does the panel composition change the conclusion -- the product-only output and the all-43-channel
    output are run side by side.

    Measuring the effect of the choice itself rather than moving individual channels is because ydc tried the
    reclassification and measured that **the two groups are not told apart by text metrics** (the contract's
    §Sensitivity).
    """
    base_counts, _panel, base_quarters = counts(population, roles=(PRODUCT,))
    all_counts, _all_panel, all_quarters = counts(population, roles=ALL_ROLES)
    prior, recent = calendar_windows(base_quarters)
    made: list[PanelSensitivityRow] = []
    for source in (VIDEO, COMMENT):
        deltas = {}
        for name, counted in (("product", base_counts[source]), ("all", all_counts[source])):
            ahead, behind = _share(counted, topics, recent), _share(counted, topics, prior)
            deltas[name] = {topic: ahead[topic] - behind[topic] for topic in topics}
        for topic in topics:
            ok_product = _quarters_ok(base_counts[source], topic, base_quarters)
            product, every = deltas["product"][topic], deltas["all"][topic]
            made.append(
                PanelSensitivityRow(
                    source=source,
                    topic_key=topic,
                    quarters_ok_product=ok_product,
                    quarters_ok_all=_quarters_ok(all_counts[source], topic, all_quarters),
                    delta_product_pp=round(product, 2),
                    delta_all_pp=round(every, 2),
                    # The difference is taken between the unrounded values -- the difference of two roundings
                    # is coarser than the decimal places the contract fixed.
                    difference_pp=round(every - product, 2),
                    # Below half the quarters qualifying, it is not judged at all. ydc nailed this sentence
                    # to 7 on a 13-quarter output; here the same sentence is derived from the observed
                    # quarter count.
                    sample_ok=2 * ok_product > len(base_quarters),
                )
            )
    return made


def flipped(made: Sequence[PanelSensitivityRow]) -> list[PanelSensitivityRow]:
    """The judged cells whose **direction actually flipped**. Only a move of at least `MATERIAL_PP` on one
    side is counted."""
    return [
        row
        for row in made
        if row.sample_ok
        and row.delta_product_pp * row.delta_all_pp < 0
        and max(abs(row.delta_product_pp), abs(row.delta_all_pp)) >= MATERIAL_PP
    ]


def next_quarters(quarters: Sequence[str], cutoff: str, count: int) -> list[str]:
    return [quarter for quarter in quarters if quarter > cutoff][:count]


def prior_quarters(quarters: Sequence[str], cutoff: str, count: int) -> list[str]:
    """The value of the cut quarter **is in** the previous window. It is not in the following one."""
    return [quarter for quarter in quarters if quarter <= cutoff][-count:]


def _mean(
    full: Mapping[tuple[str, str, str], float], source: str, topic: str, window: Sequence[str]
) -> float:
    values = [full[(source, topic, q)] for q in window if (source, topic, q) in full]
    return 100 * statistics.fmean(values) if values else 0.0


def backtest(
    population: Population,
    topics: Sequence[str],
    frame: Frame,
    base: Sequence[MetricsTopicQuarterRow],
) -> Backtest:
    """Recount as if only up to the past quarter C was known, judge the previous quarter T, and look at the 4
    quarters after C.

    The cut is the point: the baseline of `persistence` is the **median over the whole period**, so judging
    the past without cutting means the baseline was set looking at quarters that had not arrived.
    `velocity_yoy` uses only the same quarter a year earlier, so it leaks nothing. Cutting at C rather than T
    is because the judgement leaves the last quarter in progress -- judging T needs data up to C = the quarter
    after T, and production runs that way too.
    """
    full = {(row.source, row.topic_key, row.quarter): float(row.composition or 0.0) for row in base}
    quarters = sorted({row.quarter for row in base})
    cutoffs = tuple(
        (quarters[index], quarters[index - 1])
        for index in range(LOOKBACK, len(quarters))
        if len(next_quarters(quarters, quarters[index], HORIZON)) == HORIZON
    )
    made: list[BacktestRow] = []
    hits = level = cells = 0
    for cutoff, target in cutoffs:
        before_window = prior_quarters(quarters, target, LOOKBACK)
        # The previous window with the surging quarter T itself removed. Why both bases are emitted is the
        # contract's §Sensitivity.
        excl_window = [q for q in prior_quarters(quarters, target, LOOKBACK + 1) if q != target]
        after_window = next_quarters(quarters, cutoff, HORIZON)
        as_of = metrics(population, topics, frame, cutoff=cutoff)
        at_cutoff = {
            (row.source, row.topic_key): 100 * float(row.composition or 0.0)
            for row in as_of
            if row.quarter == target
        }
        for row in judge(as_of):
            if row.quarter != target or not row.judged or row.trend_type not in DIRECTIONAL:
                continue
            made.append(
                outcome(
                    row.source,
                    row.topic_key,
                    row.trend_type,
                    target,
                    _mean(full, row.source, row.topic_key, before_window),
                    _mean(full, row.source, row.topic_key, excl_window),
                    _mean(full, row.source, row.topic_key, after_window),
                    at_cutoff.get((row.source, row.topic_key), 0.0),
                )
            )
        # The base rate. A backtest that emits only a hit rate is promotion rather than validation -- the
        # share of cells unrelated to the judgement is emitted with it.
        for source in (VIDEO, COMMENT):
            for topic in topics:
                before = _mean(full, source, topic, before_window)
                after = _mean(full, source, topic, after_window)
                if before == 0 and after == 0:
                    continue
                cells += 1
                hits += after > before
                level += after > _mean(full, source, topic, excl_window)
    return Backtest(
        rows=tuple(sorted(made, key=lambda row: (row.cutoff, row.source, row.topic_key))),
        cutoffs=cutoffs,
        base_rate=round(100 * hits / cells, 2) if cells else 0.0,
        base_level_rate=round(100 * level / cells, 2) if cells else 0.0,
        base_cells=cells,
    )


def outcome(
    source: str,
    topic: str,
    trend_type: str,
    target: str,
    before: float,
    excluded: float,
    after: float,
    at_cutoff: float,
) -> BacktestRow:
    if trend_type in UP_TYPES:
        expected, hit, level = RISE_HELD, after > before, after > excluded
    elif trend_type in DOWN_TYPES:
        expected, hit, level = FALL_HELD, after < before, after < excluded
    else:
        # A peak asks "did it fall below that quarter", so the two baselines become the same question.
        expected, hit, level = PEAK_GONE, after < at_cutoff, after < at_cutoff
    return BacktestRow(
        cutoff=target,
        source=source,
        topic_key=topic,
        trend_type=trend_type,
        before_pp=round(before, 2),
        before_excl_pp=round(excluded, 2),
        after_pp=round(after, 2),
        at_cutoff_pp=round(at_cutoff, 2),
        expected=expected,
        actual=ROSE if after > before else FELL,
        hit=hit,
        hit_level=level,
    )


def flags(
    population: Population,
) -> tuple[frozenset[str], frozenset[tuple[str, str]], frozenset[tuple[str, str]]]:
    """(ad and sponsored video ids, operator comment group keys, promotional comment group keys)."""
    return (
        frozenset(video.item_id for video in population.videos if video.ad),
        frozenset(r.key for r in population.reactions if r.creator),
        frozenset(r.key for r in population.reactions if r.promo),
    )


def ad_sensitivity(
    population: Population,
    topics: Sequence[str],
    frame: Frame,
    base: Sequence[MetricsTopicQuarterRow],
) -> AdSensitivity:
    """Is the conclusion the same with the ad and sponsorship marks removed -- a column that is only marked
    and never used is a column nobody reads."""
    ad, creator, promo = flags(population)
    _prior, recent = calendar_windows(sorted({row.quarter for row in base}))
    kept_videos = _kept(population, roles=(PRODUCT,))
    seen = {video.item_id for video in kept_videos}
    mine = [r for r in population.reactions if r.parent_item_id in seen]
    base_counts, _panel, _quarters = counts(population, roles=(PRODUCT,))
    was = verdicts(base)
    judged_cells: dict[tuple[str, str], int] = defaultdict(int)
    for source, topic, _quarter in was:
        judged_cells[(source, topic)] += 1

    made: list[AdSensitivityRow] = []
    lost: dict[str, int] = {}
    for variant, drop_videos, drop_reactions in (
        (AD_VIDEO, ad, frozenset()),
        (CREATOR_COMMENT, frozenset(), creator),
        (PROMO_COMMENT, frozenset(), promo),
        (ALL_FLAGGED, ad, creator | promo),
    ):
        kept_counts, _kept_panel, _kept_quarters = counts(
            population, roles=(PRODUCT,), drop_videos=drop_videos, drop_reactions=drop_reactions
        )
        now = verdicts(
            metrics(population, topics, frame, drop_videos=drop_videos, drop_reactions=drop_reactions)
        )
        # A judgement lost to a shrunken sample is kept apart from a type that flipped. Mixed, it looks like
        # "excluding them changes every conclusion" when most of it is simply too little sample.
        flips: dict[tuple[str, str], int] = defaultdict(int)
        gone = 0
        for cell, kind in was.items():
            if cell not in now:
                gone += 1
            elif now[cell] != kind:
                flips[(cell[0], cell[1])] += 1
        lost[variant] = gone
        for source in (VIDEO, COMMENT):
            before = _share(base_counts[source], topics, recent)
            after = _share(kept_counts[source], topics, recent)
            for topic in topics:
                made.append(
                    AdSensitivityRow(
                        variant=variant,
                        source=source,
                        topic_key=topic,
                        composition_base_pp=round(before[topic], 2),
                        composition_kept_pp=round(after[topic], 2),
                        diff_pp=round(after[topic] - before[topic], 2),
                        judged_cells=judged_cells[(source, topic)],
                        flipped_cells=flips[(source, topic)],
                    )
                )
    return AdSensitivity(
        rows=tuple(made),
        videos=len(kept_videos),
        ad_videos=sum(1 for video in kept_videos if video.ad),
        declared=sum(1 for video in kept_videos if video.declared),
        matched=sum(1 for video in kept_videos if video.matched),
        comments=sum(r.documents for r in mine),
        creator_comments=sum(1 for r in mine if r.creator),
        promo_comments=sum(1 for r in mine if r.promo),
        lost_cells=lost,
    )
