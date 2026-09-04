"""민감도·후향 검증 셋 — `contracts/interfaces.md` §민감도 가 정본이다 (포크 #41).

규칙의 출처는 ydc `analysis/slices/ydc/panel_sensitivity.py` · `backtest.py` · `spam_ad_flags.py`(v0.2)
이고, 슬라이스를 import 하지 않고 옮겨 적었다 (`analysis/trend/` · `analysis/judge/` 가 쓴 방식).

셋 다 **지표를 새로 만들지 않는다**: 같은 `analysis.trend` 수식과 같은 `analysis.judge` 규칙을 모집단만
바꿔 다시 돌려, 결론이 그 선택에 흔들리는지를 잰다. 그래서 이 모듈이 다루는 것은 언제나 **반사실
모집단**이고, 그 행들은 저장되지 않는다 -- `panel_role='product+expert'` 도 "광고 영상을 뺀 산출"도
022 의 닫힌 어휘와 `analysis_run` 에 자리가 없다.

이 모듈도 DB 를 모른다: 문서 한 벌(`Population`)을 받아 행을 낼 뿐이라, 같은 코드가 코퍼스 표에서도 원
수집 CSV 에서도 돌고 그 자리가 ydc 산출본과의 1:1 대조가 성립하는 자리다.
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

# 뒤집힘으로 셀 최소 폭(%p). 부호만 보면 0 근처를 오가는 셀이 전부 뒤집힘으로 잡힌다 (계약 §민감도).
MATERIAL_PP = 0.5
# 패널 어휘. 022 는 둘만 알고, 둘을 합친 반사실 모집단의 이름은 저장되지 않으므로 여기 산다.
PRODUCT = "product"
EXPERT = "expert"
ALL_ROLES: tuple[str, ...] = (PRODUCT, EXPERT)
ALL_ROLES_LABEL = "product+expert"

# 후향 검증의 두 구간 길이(분기). 창 길이가 `persistence` 의 것과 같은 4인 것은 계절성이다 -- 직전·이후
# 둘 다 네 분기를 다 담아야 여름 효과가 상쇄된다.
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

# 표시하는 것 셋. ydc 는 사람이 읽는 한국어 라벨로 적고, 여기서는 그 셋을 이름으로 든다 (계약 §민감도).
AD_VIDEO = "ad_video"
CREATOR_COMMENT = "creator_comment"
PROMO_COMMENT = "promo_comment"
ALL_FLAGGED = "all_flagged"
VARIANTS = (AD_VIDEO, CREATOR_COMMENT, PROMO_COMMENT, ALL_FLAGGED)

# 신고 필드가 놓치는 협찬을 설명란 문구로 잡는다. `ppl` 은 경계를 둔다 -- 없으면 `apple` 이 걸린다.
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
# 수집기가 댓글 작성자에 다는 해시와 같은 식이어야 한다 -- 다르면 운영자 댓글이 조용히 0건이 된다.
CREATOR_HASH_PREFIX = "youtube:"
CREATOR_HASH_LENGTH = 24


class ShortHistory(LookupError):
    """분기가 하나도 없다. 두 창(직전 4 · 최근 4)이 설 자리가 없으므로 0 을 내는 대신 멈춘다."""


def creator_hash(channel_id: str) -> str:
    """`author_channel_hash` 를 채널 id 에서 되만든다 -- 추정이 아니라 정확한 매칭이다."""
    digest = hashlib.sha256(f"{CREATOR_HASH_PREFIX}{channel_id}".encode()).hexdigest()
    return digest[:CREATOR_HASH_LENGTH]


@dataclass(frozen=True)
class Video:
    """모집단에 든 장문 영상 한 편. `panel_role` 이 행에 있는 것이 패널 민감도가 서는 자리다."""

    item_id: str
    channel_id: str
    panel_role: str
    quarter: str
    topics: tuple[str, ...]
    declared: bool  # 유튜버 자체 신고(has_paid_product_placement). 누락이 있다 (TEAM_DECISIONS §9)
    matched: bool  # 설명란 문구가 걸렸다

    @property
    def ad(self) -> bool:
        """둘의 **합집합**이다. 전량 실측 신고 254편 · 문구 407편 중 문구만 걸리는 것이 200편이 넘는다."""
        return self.declared or self.matched


@dataclass(frozen=True)
class Reaction:
    """한 영상 안에서 정규화 뒤 같은 텍스트인 댓글 묶음. 제외는 묶음 단위다 -- 복붙 한 쪽만 빼면
    `unique_ratio` 의 분자와 분모가 다른 모집단을 세게 된다 (ydc 의 `(video_id, text)` 키와 같다)."""

    parent_item_id: str
    digest: str
    counted: int  # quality_flags 가 빈 문서 수. 언급 수와 분기 문서 수의 몫
    documents: int  # 복붙까지 센 문서 수. `unique_ratio` 분모의 몫
    topics: tuple[str, ...]
    creator: bool
    promo: bool

    @property
    def key(self) -> tuple[str, str]:
        return (self.parent_item_id, self.digest)


@dataclass(frozen=True)
class Population:
    """한 스냅샷의 두 계열. 반사실은 전부 이 한 벌 위에서 걸러 만든다 -- 변형마다 다시 읽으면 변형
    사이의 차이에 읽은 시점의 차이가 섞인다."""

    videos: tuple[Video, ...]
    reactions: tuple[Reaction, ...]


@dataclass(frozen=True)
class Frame:
    """반사실 행이 무엇의 반사실인지 적는 자리. 저장되지 않으므로 022 의 CHECK 을 지지 않는다."""

    run_id: int
    scope: str
    content_type: str
    panel_version: int


@dataclass(frozen=True)
class Backtest:
    rows: tuple[BacktestRow, ...]
    cutoffs: tuple[tuple[str, str], ...]  # (자른 분기 C, 판정 대상 T = C 직전 분기)
    base_rate: float  # 판정과 무관하게 이후 4분기 평균이 직전 4분기보다 높은 셀의 비율(%)
    base_level_rate: float  # 같은 것을 기준 B(판정 분기를 뺀 직전 구간)로 잰 값
    base_cells: int


@dataclass(frozen=True)
class AdSensitivity:
    rows: tuple[AdSensitivityRow, ...]
    videos: int
    ad_videos: int
    declared: int
    matched: int
    comments: int  # 복붙까지 센 댓글 문서 수
    creator_comments: int  # 운영자 댓글 **묶음** 수
    promo_comments: int
    lost_cells: Mapping[str, int]  # 변형 -> 표본 미달로 판정이 사라진 셀 수


def previous_quarter(quarter: str) -> str:
    """달력에서 하나 앞. 관측 목록의 앞이 아니다 -- 빠진 분기가 창을 조용히 늘린다."""
    year, index = int(quarter[:4]), int(quarter[5])
    return f"{year}Q{index - 1}" if index > 1 else f"{year - 1}Q4"


def calendar_windows(quarters: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(직전 4분기, 최근 4분기). 마지막 분기는 진행 중이라 두 창 밖이다 (`analysis.judge` 의 `미확정`).

    ydc 는 이 여덟을 달력값으로 박아 뒀다(`PRIOR`·`RECENT`). 여기서 유도하는 것은 그 목록이 이 코퍼스에
    묶인 상수이기 때문이고, 유도해도 같은 여덟 분기가 나온다. **관측 목록의 인덱스가 아니라 달력으로
    세는 것**이 뜻이다: 언급이 없어 행이 빠진 분기도 창의 한 칸을 차지해 0 으로 들어가야 한다.
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
    """반사실 하나의 영상 모집단. 영상이 빠지면 그 영상의 댓글도 함께 빠진다 (분기 귀속이 부모다)."""
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
    # 영상은 한 문서가 한 번만 세어지므로 중복 포함 언급 수가 언급 수와 같다 -- unique_ratio 는 1 이다.
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
            # 중복 포함 분모는 복붙까지 세고 분자는 세지 않는다 (코퍼스 규칙 9).
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
    """한 반사실의 셈 두 벌과 영상 채널 분포, 그리고 그 산출의 분기 축(영상 기준)."""
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
    """한 반사실의 지표 행 한 벌. 수식은 `analysis.trend` 것 그대로다 -- 여기서 다시 쓰지 않는다."""
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
        # 문서가 한 편도 없는 계열은 분기 축이 없다 -- 0 행이 맞고, 0 을 채운 격자는 없는 관측을
        # 관측된 0 으로 만든다 (`analysis.trend.rows` 의 기준선이 그 0 들 위에서 정해진다).
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
    """구간 안 주제 간 구성비(%). 분모는 그 구간 전체 주제 언급 수의 합이다."""
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
    """패널 구성이 결론을 바꾸는가 -- product 만인 산출과 43채널 전부인 산출을 나란히 돌린다.

    개별 채널을 옮기는 대신 선택 자체의 영향을 재는 것은, ydc 가 재분류를 시도해 **텍스트 지표로는 두
    집단이 구분되지 않는다**는 것을 실측했기 때문이다 (계약 §민감도).
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
                    # 차이는 반올림 전 값끼리 뺀다 -- 두 반올림의 차는 계약이 정한 자리수보다 거칠다.
                    difference_pp=round(every - product, 2),
                    # 충족 분기가 절반 미만이면 애초에 판정 대상이 아니다. ydc 는 13분기 산출에서 이
                    # 문장을 7 로 박아 뒀고, 여기서는 같은 문장을 관측 분기 수에서 유도한다.
                    sample_ok=2 * ok_product > len(base_quarters),
                )
            )
    return made


def flipped(made: Sequence[PanelSensitivityRow]) -> list[PanelSensitivityRow]:
    """판정 대상 셀 중 **방향이 실제로 뒤집힌** 것. 한쪽이라도 `MATERIAL_PP` 만큼 움직인 경우만 센다."""
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
    """자르는 분기의 값은 직전 구간에 **든다**. 이후 구간에는 들지 않는다."""
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
    """과거 분기 C 까지만 알던 것처럼 다시 세어 직전 분기 T 를 판정하고, C 이후 4분기를 본다.

    자르는 것이 핵심이다: `persistence` 의 기준선이 **전 기간 중앙값**이라, 자르지 않고 과거를 판정하면
    아직 오지 않은 분기를 보고 기준선을 정한 셈이 된다. `velocity_yoy` 는 전년 동분기만 쓰므로 누출이 없다.
    T 가 아니라 C 로 자르는 것은 판정이 마지막 분기를 `미확정(진행 중)` 으로 두기 때문이다 -- T 를
    판정하려면 C = T 다음 분기까지 데이터가 있어야 하고, 운영도 그렇게 돈다.
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
        # 급상승한 분기 T 자체를 뺀 직전 구간. 두 기준을 다 내는 이유는 계약 §민감도.
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
        # 기저율. 적중률만 내는 후향 검증은 검증이 아니라 홍보다 -- 판정과 무관한 셀의 비율을 함께 낸다.
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
        # 피크는 "그 분기보다 낮아졌는가"라 두 기준이 같은 질문이 된다.
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
    """(광고·협찬 영상 id, 운영자 댓글 묶음 키, 홍보 댓글 묶음 키)."""
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
    """광고·협찬 표시를 빼도 결론이 같은가 -- 표시만 하고 쓰지 않으면 아무도 안 읽는 컬럼이 된다."""
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
        # 표본이 줄어 판정이 사라진 것과 유형이 뒤집힌 것을 나눈다. 섞으면 "제외하니 결론이 다 바뀐다"로
        # 보이는데 실은 대부분 표본 미달이다.
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
