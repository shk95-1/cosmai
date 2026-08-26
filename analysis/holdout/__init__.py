"""새 표본으로 되묻는다 — `contracts/interfaces.md` §홀드아웃 이 정본이다 (포크 #51).

규칙의 출처는 ydc `holdout_commerce.py`(v0.3.0)이고, 슬라이스를 import 하지 않고 옮겨 적었다
(`analysis/crosscheck` · `analysis/sensitivity` 가 쓴 방식).

**지표를 새로 만들지 않는다**: 같은 사전으로 같은 단위(리뷰 1건)를 세되, **한 번도 안 본 리뷰만** 따로
세서 기존 비율이 재현되는지 본다. 재현되면 결론이 표본에 얹혀 있지 않다는 뜻이고, 안 되면 그것이 더
중요한 발견이다. 그래서 이 모듈이 다루는 것은 언제나 **두 팔**이고, 그 행들은 저장되지 않는다.

여기는 규칙만 산다. DB 는 `analysis/holdout/pipeline.py` 이고, 그쪽이 두 팔을 갈라 이 함수들에 먹인다 --
`analysis/crosscheck` 와 같은 가름이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from analysis.crosscheck import ranks
from analysis.trend import MIN_MENTIONS

SEEN = "seen"
HOLDOUT = "holdout"

# ydc `holdout_commerce.report` 의 `abs(d) <= 1.5`: "홀드아웃이 3분의 1 크기라 표본 흔들림이 있다.
# 1.5%p 를 넘으면 사람이 본다." 적합값이 아니라 **사람이 볼지 정하는 문턱**이다 (계약 §홀드아웃).
MATERIAL_PP = 1.5
# 같은 함수의 `ra[:2] == rb[:2]`. 뒤에 붙은 최하위 일치는 옮기지 않았다 -- 13주제 축의 꼬리는 0으로
# 묶여 있어 그 자리를 검사하면 순위가 아니라 정렬 순서를 검사하게 된다 (계약 §홀드아웃).
RANK_TOP = 2

WINDOW_NEW = "새 기간이다"
WINDOW_EXTENDED = "같은 창이 길어졌다"
VERDICT_REPRODUCED = "재현"
VERDICT_RANK_ONLY = "순위 재현"
VERDICT_BROKEN = "순위 변동"
# 순위를 갖는 주제가 하나도 없으면 상위 비교가 공회전한다. 그것을 `재현` 이라고 부르면 없는 근거로
# 재현을 주장하는 것이다 (계약 §홀드아웃).
VERDICT_THIN = "순위 없음"


@dataclass(frozen=True)
class Review:
    """홀드아웃이 세는 단위. 원천 표가 아니라 이 모양이 규칙의 입력이다 -- 같은 코드가 두 팔에 돈다."""

    platform: str
    product_key: str
    captured_at: datetime
    topics: tuple[str, ...]


@dataclass(frozen=True)
class Arm:
    """한 팔의 크기와 창. **분모가 둘이라 둘 다 든다** (계약 §홀드아웃)."""

    name: str
    reviews: int
    mentions: int
    documents: Mapping[str, int]
    products: frozenset[str]
    platforms: Mapping[str, int]
    first_captured: datetime | None = None
    last_captured: datetime | None = None


@dataclass(frozen=True)
class TopicRow:
    """한 주제를 두 팔에서. `rate` 와 `share` 는 분모가 다르므로 차를 분모를 넘어 내지 않는다."""

    topic_key: str
    seen_documents: int
    holdout_documents: int
    seen_rate: float
    holdout_rate: float
    seen_share: float
    holdout_share: float
    seen_rank: int | None = None
    holdout_rank: int | None = None

    @property
    def rate_diff_pp(self) -> float:
        """ydc 의 축에서의 차. **판정이 보는 수다.**"""
        return self.holdout_rate - self.seen_rate

    @property
    def share_diff_pp(self) -> float:
        """§구성 의 축에서의 차. `rate_diff_pp` 와 빼거나 더하지 않는다 -- 분모가 다르다."""
        return self.holdout_share - self.seen_share

    @property
    def reproduced(self) -> bool:
        return abs(self.rate_diff_pp) <= MATERIAL_PP

    @property
    def share_reproduced(self) -> bool:
        return abs(self.share_diff_pp) <= MATERIAL_PP


@dataclass(frozen=True)
class PlatformRow:
    platform: str
    seen_reviews: int
    holdout_reviews: int
    seen_mix: float
    holdout_mix: float


@dataclass(frozen=True)
class StandardRow:
    """기존 팔의 플랫폼 구성비로 재가중한 홀드아웃. 구성 효과를 뺀 값이다."""

    topic_key: str
    seen_rate: float
    holdout_rate: float
    standardized_rate: float

    @property
    def residual_pp(self) -> float:
        """구성 효과를 뺀 뒤 남는 차. **이것이 실제 변화다.**"""
        return self.standardized_rate - self.seen_rate


@dataclass(frozen=True)
class Basket:
    seen_products: int
    holdout_products: int
    shared: int
    seen_only: int
    holdout_only: int
    seen_reviews: int
    holdout_reviews: int


@dataclass(frozen=True)
class BasketRow:
    topic_key: str
    seen_rate_all: float
    seen_rate_shared: float
    holdout_rate_shared: float

    @property
    def diff_pp(self) -> float:
        """바스켓 효과를 뺀 차. 두 값 모두 **공통 제품**의 리뷰만 센 것이다."""
        return self.holdout_rate_shared - self.seen_rate_shared


@dataclass(frozen=True)
class Comparison:
    seen: Arm
    holdout: Arm
    topics: tuple[TopicRow, ...] = ()
    platforms: tuple[PlatformRow, ...] = ()
    standardized: tuple[StandardRow, ...] = ()
    basket: Basket | None = None
    basket_rows: tuple[BasketRow, ...] = ()
    window: str = ""
    verdict: str = ""

    @property
    def ranked(self) -> tuple[TopicRow, ...]:
        return tuple(row for row in self.topics if row.seen_rank is not None)

    @property
    def reproduced(self) -> int:
        """언급률 축에서 재현된 주제 수. 분모는 `ranked` 다."""
        return sum(1 for row in self.ranked if row.reproduced)

    @property
    def share_reproduced(self) -> int:
        """구성비 축에서 재현된 주제 수. **같은 분모(`ranked`) 위의 다른 축**이라 위 수와 섞지 않는다."""
        return sum(1 for row in self.ranked if row.share_reproduced)


def rate(documents: int, reviews: int) -> float:
    """분모는 **그 팔의 리뷰 수**다 (ydc `rates` 의 축). 주제가 안 걸린 리뷰도 이 분모에 있다 --
    빼면 언급률이 조용히 커진다."""
    return 100 * documents / reviews if reviews else 0.0


def share(documents: int, mentions: int) -> float:
    """분모는 **그 팔의 `trend_use` 주제 언급 합**이다 (§구성 의 축). `rate` 와 섞지 않는다."""
    return 100 * documents / mentions if mentions else 0.0


def arm(name: str, reviews: Sequence[Review], topic_keys: Sequence[str]) -> Arm:
    """한 팔을 센다. 한 리뷰에 같은 주제가 여러 표기로 나와도 한 번이다 (ydc `rates`).

    축 밖 주제는 두 분모 어디에도 들지 않는다 -- 들면 구성비가 조용히 작아진다 (§구성 과 같은 규칙).
    """
    axis = set(topic_keys)
    documents: dict[str, int] = {topic: 0 for topic in topic_keys}
    platforms: dict[str, int] = {}
    products: set[str] = set()
    mentions = 0
    for review in reviews:
        platforms[review.platform] = platforms.get(review.platform, 0) + 1
        products.add(review.product_key)
        for topic in {topic for topic in review.topics if topic in axis}:
            documents[topic] += 1
            mentions += 1
    stamps = [review.captured_at for review in reviews]
    return Arm(
        name=name,
        reviews=len(reviews),
        mentions=mentions,
        documents=documents,
        products=frozenset(products),
        platforms=platforms,
        first_captured=min(stamps) if stamps else None,
        last_captured=max(stamps) if stamps else None,
    )


def mix(counted: Arm) -> dict[str, float]:
    """플랫폼 구성비(%). 표준화의 가중치이자 표의 한 열이라 백분율 하나로 든다."""
    return {name: rate(count, counted.reviews) for name, count in counted.platforms.items()}


def _gated(seen: Arm, topic_keys: Sequence[str]) -> tuple[str, ...]:
    """순위를 갖는 주제. 게이트는 **기존 팔**이 진다 -- 홀드아웃에 걸면 새 표본이 얇다는 이유로 기존
    순위가 사라져 물음이 거꾸로 선다 (계약 §홀드아웃)."""
    return tuple(topic for topic in topic_keys if seen.documents.get(topic, 0) >= MIN_MENTIONS)


def topics(seen: Arm, holdout: Arm, topic_keys: Sequence[str]) -> tuple[TopicRow, ...]:
    """주제마다 한 줄. 두 분모를 나란히 싣되 차를 분모를 넘어 내지 않는다 (계약 §홀드아웃)."""
    ranked = _gated(seen, topic_keys)
    seen_place = ranks({topic: rate(seen.documents.get(topic, 0), seen.reviews) for topic in ranked})
    hold_place = ranks({topic: rate(holdout.documents.get(topic, 0), holdout.reviews) for topic in ranked})
    return tuple(
        TopicRow(
            topic_key=topic,
            seen_documents=seen.documents.get(topic, 0),
            holdout_documents=holdout.documents.get(topic, 0),
            seen_rate=rate(seen.documents.get(topic, 0), seen.reviews),
            holdout_rate=rate(holdout.documents.get(topic, 0), holdout.reviews),
            seen_share=share(seen.documents.get(topic, 0), seen.mentions),
            holdout_share=share(holdout.documents.get(topic, 0), holdout.mentions),
            seen_rank=seen_place.get(topic),
            holdout_rank=hold_place.get(topic),
        )
        for topic in topic_keys
    )


def platforms(seen: Arm, holdout: Arm) -> tuple[PlatformRow, ...]:
    """두 팔의 플랫폼 구성. 목록은 **읽어서 만든다** -- ydc 는 셋을 상수로 박았지만 우리 소스는 는다."""
    seen_mix, hold_mix = mix(seen), mix(holdout)
    names = sorted(set(seen.platforms) | set(holdout.platforms))
    return tuple(
        PlatformRow(
            platform=name,
            seen_reviews=seen.platforms.get(name, 0),
            holdout_reviews=holdout.platforms.get(name, 0),
            seen_mix=seen_mix.get(name, 0.0),
            holdout_mix=hold_mix.get(name, 0.0),
        )
        for name in names
    )


def _rates(reviews: Sequence[Review], topic_keys: Sequence[str]) -> dict[str, float]:
    counted = arm("", reviews, topic_keys)
    return {topic: rate(counted.documents.get(topic, 0), counted.reviews) for topic in topic_keys}


def _by_platform(reviews: Sequence[Review]) -> dict[str, list[Review]]:
    made: dict[str, list[Review]] = {}
    for review in reviews:
        made.setdefault(review.platform, []).append(review)
    return made


def standardize(
    seen: Sequence[Review], holdout: Sequence[Review], topic_keys: Sequence[str]
) -> tuple[StandardRow, ...]:
    """기존 팔의 플랫폼 구성비로 홀드아웃을 재가중한다. **구성 효과를 뺀 값이 남는 실제 변화다.**

    가중치는 기존 팔에 있는 플랫폼의 것이므로, 홀드아웃에 그 플랫폼의 리뷰가 하나도 없으면 그 칸은
    0 으로 들어간다 (ydc `by_platform` 과 같다) -- 조용한 0 이 아니라 `PlatformRow` 가 말하는 0 이다.
    """
    weights = mix(arm("", seen, topic_keys))
    hold_by = _by_platform(holdout)
    per_platform = {name: _rates(hold_by.get(name, []), topic_keys) for name in weights}
    seen_rates, hold_rates = _rates(seen, topic_keys), _rates(holdout, topic_keys)
    return tuple(
        StandardRow(
            topic_key=topic,
            seen_rate=seen_rates[topic],
            holdout_rate=hold_rates[topic],
            standardized_rate=sum(
                weight * per_platform[name][topic] / 100 for name, weight in weights.items()
            ),
        )
        for topic in topic_keys
    )


def basket(
    seen: Sequence[Review], holdout: Sequence[Review], topic_keys: Sequence[str]
) -> tuple[Basket, tuple[BasketRow, ...]]:
    """**수집 과정이 관측값을 만든다.** 두 팔이 공유하는 제품만으로 다시 세어 바스켓 효과를 뺀다.

    교집합이 비면 표를 세우지 않는다 -- 0% 는 답이 아니라 없음이고, 그것을 행으로 실으면 "같은 제품에서
    아무도 그 말을 안 한다"로 읽힌다.
    """
    seen_products = {review.product_key for review in seen}
    hold_products = {review.product_key for review in holdout}
    shared = seen_products & hold_products
    seen_in = [review for review in seen if review.product_key in shared]
    hold_in = [review for review in holdout if review.product_key in shared]
    made = Basket(
        seen_products=len(seen_products),
        holdout_products=len(hold_products),
        shared=len(shared),
        seen_only=len(seen_products - hold_products),
        holdout_only=len(hold_products - seen_products),
        seen_reviews=len(seen_in),
        holdout_reviews=len(hold_in),
    )
    if not shared:
        return made, ()
    seen_all, seen_shared, hold_shared = (
        _rates(seen, topic_keys),
        _rates(seen_in, topic_keys),
        _rates(hold_in, topic_keys),
    )
    return made, tuple(
        BasketRow(
            topic_key=topic,
            seen_rate_all=seen_all[topic],
            seen_rate_shared=seen_shared[topic],
            holdout_rate_shared=hold_shared[topic],
        )
        for topic in topic_keys
    )


def window_reading(seen: Arm, holdout: Arm) -> str:
    """새 기간인가, 같은 창이 길어진 것인가. **선언하지 않고 읽어서 답한다** (계약 §홀드아웃)."""
    if seen.last_captured is None or holdout.first_captured is None:
        return ""
    return WINDOW_NEW if holdout.first_captured >= seen.last_captured else WINDOW_EXTENDED


def verdict(rows: Sequence[TopicRow]) -> str:
    """ydc `holdout_commerce.report` 의 갈래 순서 그대로 -- **수준이 먼저다** (계약 §홀드아웃)."""
    ranked = [row for row in rows if row.seen_rank is not None and row.holdout_rank is not None]
    if not ranked:
        return VERDICT_THIN
    if max(abs(row.rate_diff_pp) for row in ranked) <= MATERIAL_PP:
        return VERDICT_REPRODUCED
    top_seen = [row.topic_key for row in sorted(ranked, key=lambda row: row.seen_rank or 0)][:RANK_TOP]
    top_hold = [row.topic_key for row in sorted(ranked, key=lambda row: row.holdout_rank or 0)][:RANK_TOP]
    return VERDICT_RANK_ONLY if top_seen == top_hold else VERDICT_BROKEN


def compare(seen: Sequence[Review], holdout: Sequence[Review], topic_keys: Sequence[str]) -> Comparison:
    """두 팔 한 벌. 이 함수 안의 비교는 언제나 **같은 함수를 탄 두 팔 사이**에서만 한다."""
    seen_arm, hold_arm = arm(SEEN, seen, topic_keys), arm(HOLDOUT, holdout, topic_keys)
    rows = topics(seen_arm, hold_arm, topic_keys)
    made, basket_rows = basket(seen, holdout, topic_keys)
    return Comparison(
        seen=seen_arm,
        holdout=hold_arm,
        topics=rows,
        platforms=platforms(seen_arm, hold_arm),
        standardized=standardize(seen, holdout, topic_keys),
        basket=made,
        basket_rows=basket_rows,
        window=window_reading(seen_arm, hold_arm),
        verdict=verdict(rows),
    )


__all__ = [
    "HOLDOUT",
    "MATERIAL_PP",
    "RANK_TOP",
    "SEEN",
    "VERDICT_BROKEN",
    "VERDICT_RANK_ONLY",
    "VERDICT_REPRODUCED",
    "VERDICT_THIN",
    "WINDOW_EXTENDED",
    "WINDOW_NEW",
    "Arm",
    "Basket",
    "BasketRow",
    "Comparison",
    "PlatformRow",
    "Review",
    "StandardRow",
    "TopicRow",
    "arm",
    "basket",
    "compare",
    "mix",
    "platforms",
    "rate",
    "share",
    "standardize",
    "topics",
    "verdict",
    "window_reading",
]
