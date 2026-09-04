"""Puts the sources side by side and finds where they disagree (fork #7). **Nothing is summed.**

규칙의 출처는 ydc `source_composition.py` · `commerce_crosscheck.py` · `cross_source.py` 이고, 슬라이스를
import 하지 않고 옮겨 적었다. **핀(`v0.1.0` `02440ab`)이 아니라 `v0.3.0`(`e5a1b00`)이다** — 성분 키와
선크림 문맥이 그 판에서 정정됐고 `cross_source.py` 는 핀 사본에 아예 없었다. 대조는
`tool/compare-ydc-crosscheck` 가 ydc 레포를 태그째 읽어 돌린다.

왜 합산하지 않나. 소스마다 분모가 다르다 -- 구성비는 그 소스의 13주제 언급 합 대비, 플랫폼 속성 평가는
`topic_group` 안의 응답 비중, 성분 담론은 문서 수다. 더하거나 평균 내면 그 순간 뜻이 없어진다. 그래서
**크기가 아니라 순위와 방향**을 본다 (`contracts/interfaces.md` §대조).

Only the rules live here. The DB is `analysis/crosscheck/pipeline.py`, and that side feeds these functions
their values -- the same split as `analysis/sensitivity`.
"""

from __future__ import annotations

import csv
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------- composition (ydc source_composition.py)

COMMENT = "youtube_comment"
TRANSCRIPT = "youtube_transcript"
VIDEO_TITLE = "youtube_video"
COMMERCE_REVIEW = "commerce_review"
SOURCES = (COMMENT, TRANSCRIPT, VIDEO_TITLE, COMMERCE_REVIEW)

# 제작자 쪽은 자막이다. ydc 의 `youtube_video` 는 영상 설명이었지만 우리 `youtube_video` 청크는 제목
# 한 줄이라(analysis/retrieval/corpus.py 의 VIDEOS) 제작자 언어의 그릇이 아니다 -- 5,908문서에서 주제
# 언급이 1,123건뿐이다. 제목 열은 표에 남되 해석 규칙을 물지 않는다 (계약 §구성).
CREATOR = TRANSCRIPT
CONSUMER = COMMERCE_REVIEW

LEAD_PP = 5.0
THIN_PP = 2.0
SPARSE_PP = 0.5
TALK_RATIO = 3

READ_CONSUMER_ONLY = "영상 설명으로는 관측 불가 · 실사용 발화에만 있음"
READ_CONSUMER_LEAD = "실사용 쪽이 훨씬 많이 말함"
READ_CREATOR_LEAD = "제작자 쪽이 훨씬 많이 말함 · 스펙·성분 언어"
READ_COMMENT_ONLY = "영상은 안 다루는데 댓글·리뷰에는 있음"


@dataclass(frozen=True)
class SourceShare:
    """The place one topic takes on each source. Document counts are not summed across sources."""

    topic_key: str
    documents: Mapping[str, int]
    shares: Mapping[str, float]
    ranks: Mapping[str, int]
    reading: str = ""


# ---------------------------------------------------------- rating (ydc commerce_crosscheck.py)

# 커머스 topic_group -> 우리 topic_id. 대응이 없는 것은 넣지 않는다 (전량에서 `피부타입` 이 그 자리다).
GROUP_MAP = {
    "자극도": "자극_눈시림",
    "보습력": "촉촉함_건조함",
    "수분감": "촉촉함_건조함",
    "지속력": "지속력_워터프루프",
    "발림성": "발림성",
    "커버력": "톤업_메이크업베이스",
    "발색력": "톤업_메이크업베이스",
}
# 커머스는 선택지 문구로 극성을 나타낸다. 중립이 먼저다 -- `보통이에요` 는 부정 힌트를 안 갖는다.
#
# **힌트 목록도 벤더 문자열 위의 부분문자열이라 성분 키와 같은 병이 있다.** 운영 `review_topic` 의
# `GROUP_MAP` 그룹 어휘 23개에 먹여 보니 다섯이 뒤집혀 나왔다(2026-08-27 실측): `자극도/자극이 있어요` ·
# `보습력/약간 건조해요` · `지속력/예상보다 짧아요` · `커버력/예상보다 짧아요` 가 긍정으로,
# `가루날림/날림이 없어요` 는 `없어요` 힌트에 걸려 부정으로. 오늘 선케어 집합에는 바르게 분류되는 세
# 선택지만 오지만, `GROUP_MAP` 이 존재하는 이유가 나머지 그룹이 오는 날이다. 그래서 힌트는 **마지막
# 수단**이고 정본은 사람이 확인한 표다 (계약 §평가).
NEGATIVE_HINTS = ("느껴져요", "아쉬", "부족", "무거", "끈적", "밀려", "answer_no", "없어요")
NEUTRAL_HINTS = ("보통",)
POLARITY_CSV = Path(__file__).resolve().parent / "audit" / "polarity_v1.csv"

# 우리 판정에 document_count >= 5 를 요구하면서 이 대조에만 예외를 두면 이중 기준이다 (계약 §평가).
MIN_PRODUCTS = 5
POSITIVE_RATE_HIGH = 80.0
GAP_PP_MATERIAL = 1.0

READ_GAP_UNHAPPY = "소비자 불만 실재 · 제품 공백 근거 강화"
READ_GAP_HAPPY = "많이 말하지만 만족도 높음 · 공백이 아니라 관심"
READ_QUIET_UNHAPPY = "만족도 낮음 · 언급은 적어 관찰 필요"
READ_SATURATED = "만족도 높고 갭 작음 · 포화"


@dataclass(frozen=True)
class RatingRow:
    """The platform attribute ratings for one topic and the judgement of that run. The direction is looked at,
    not the value."""

    topic_key: str
    commerce_groups: tuple[str, ...]
    products_rated: int
    positive_rate_mean: float
    positive_rate_median: float
    youtube_rank_comment: int | None = None
    youtube_composition_pct: float | None = None
    youtube_gap_pp: float | None = None
    youtube_trend_type: str = ""
    reading: str = ""

    @property
    def thin(self) -> bool:
        return self.products_rated < MIN_PRODUCTS


# ------------------------------------------------ ingredients (the ingredient axis of ydc cross_source.py)

# NAVER 성분 그룹 -> 성분표에서 찾을 이름 조각. 소문자·공백 제거 후 부분문자열로 본다.
#
# **두 글자 별칭을 성분명 부분문자열로 쓰면 안 된다** (ydc v0.3.0 e5a1b00 의 정정). 아래 REJECTED_TERMS
# 가 되돌리면 무엇이 잡히는지를 든다 -- 우리 표에서 다시 감사한 값이다 (계약 §성분).
INGREDIENT_KEYS: dict[str, tuple[str, ...]] = {
    "PDRN": ("피디알엔", "pdrn", "폴리데옥시리보뉴클레오타이드"),
    "엑소좀": ("엑소좀", "exosome"),
    "트라넥삼산": ("트라넥삼산",),
    "레티날": ("레티날",),
    "시카센텔라": ("병풀", "센텔라", "마데카", "아시아티코", "아시아틱"),
    "나이아신아마이드": ("나이아신아마이드",),
    "히알루론산": ("하이알루로", "히알루론"),
    "펩타이드": ("펩타이드",),
    "콜라겐": ("콜라겐",),
    "판테놀": ("판테놀",),
}
# 쓰지 않기로 한 별칭과 그 근거. 상수로 남기는 것은, 되돌리면 무엇이 잡히는지를 적어 두는 것 말고는
# "시카가 왜 없지" 하고 되살리는 것을 막을 길이 없기 때문이다. 값은 우리 표 실측(2026-08-27)이다.
# (`센텔라` 는 버린 것이 아니라 키에 남아 있다 -- 0행일 뿐이고, 그래서 `병풀` 이 필요했다.)
REJECTED_TERMS: dict[str, str] = {
    "시카": "216행 전부 트라이에톡시카프릴릴실레인(209)·트리에톡시카프릴릴실란(7) -- 실리콘 분산제다",
    "레티놀": "7행. `레티날` 은 0행이고 레티놀과 레티날은 다른 물질이다",
}
# 어느 키도 잡아서는 안 되는 성분명과 그 이유. **감사의 기계 게이트는 이것 하나다.**
#
# ydc 의 `[의심]` 규칙(잡힌 이름에 키가 하나도 안 들어 있는가)은 여기 옮기지 않았다 -- 매처는 대소문자·
# 공백을 접고 그 규칙은 원문 그대로 보므로, 실제로 잡을 수 있는 것이 폴딩 아티팩트(`pdrn` 대 `PDRN`)뿐
# 이다. 정작 `시카` 는 그 규칙을 **만족한다**(`시카` 가 트라이에톡시카프릴릴실레인 안에 진짜로 들어 있다).
# 그것을 잡은 것은 규칙이 아니라 찍힌 이름을 읽은 사람이다. 그래서 우리는 사람이 한 번 읽어 확인한 것을
# 목록으로 남긴다 -- 되살아나면 종료 코드 1 이다 (계약 §성분).
DENIED_NAMES: dict[str, str] = {
    "트라이에톡시카프릴릴실레인": "실리콘 분산제. `시카` 별칭이 우리 표에서 209행을 이것으로 잡았다",
    "트리에톡시카프릴릴실란": "같은 물질의 다른 표기. 7행",
}
# **그 키만** 잡아서는 안 되는 성분명. 전역으로 두면 안 되는 것이 실측으로 드러났다 -- 쉼표 없이 공백
# 으로만 나열한 성분표 한 덩어리에 `벼에스에이치-올리고펩타이드-1   * 레티놀 함량 509 IU/g` 가 들어
# 있어서, `레티놀` 을 전역 금지로 두면 **펩타이드 키가 빨개진다.** 그 줄은 진짜 펩타이드 행이고
# 오매칭이 아니다. 금지의 단위는 물질이 아니라 (키, 물질)이다.
DENIED_FOR: dict[str, dict[str, str]] = {
    "레티날": {"레티놀": "레티날과 다른 물질이다. 후한 것이 아니라 틀린 것이다"},
}


def denial_reason(key: str, name: str) -> str:
    return DENIED_NAMES.get(name) or DENIED_FOR.get(key, {}).get(name, "")


# 담론 수를 "선크림 담론" 으로 읽으면 안 된다. 색인 전체에서 센 값이라 같은 채널이 소개한 앰플·
# 스킨부스터가 다 들어 있다. 그래서 이 말이 **같은 청크 안에** 있는 것을 따로 센다 (계약 §성분).
SUN_WORDS = ("선크림", "썬크림", "선스크린", "자차", "선세럼", "선쿠션", "자외선차단")
SUN_SHARE_LOW = 25.0

# 처방 축은 잠겨 있다: `product.ingredients` 가 있는 180제품 중 선케어는 2개다. 180 으로 나누면
# "선케어 처방 채택률" 이라는 이름 아래 다른 모집단의 비율이 선다 -- PAPER_HOLD 가 정정한 그 오류다.
FORMULA_HOLD = True
# 논문 축은 원천도 없고 보정도 성립하지 않는다: 분자는 전분야 검색어(잔존율 20~48%)이고 분모
# `cosmetic` 은 화장품 검색어라 모집단이 다른 값으로 나눴다. (초판이 근거로 적은 "cosmetic 잔존율
# 100.1%" 는 쓰지 않는다 -- 필터가 검색어를 하나도 못 걸러내는 항등식이다.)
PAPER_HOLD = True

READ_NOT_SUNCARE = "선크림 담론이 아니다"

# 사람이 한 번 읽어 확인한 키별 성분명(2026-08-27 운영 표, 180제품 · 190이름). **키가 무엇을 잡는지의
# 정본이다.** 상수 목록(DENIED_NAMES)은 이미 아는 오매칭만 막고, 아직 모르는 오매칭 -- 코퍼스가 자라
# 새 물질이 어떤 키에 들어오는 것 -- 은 이 목록과 실제 표를 맞대야 보인다. 맞대는 길은
# `tool/measure-crosscheck-keys` 이고, CI 는 그 일을 할 수 없다(운영 표에 닿지 못한다).
KNOWN_NAMES_CSV = Path(__file__).resolve().parent / "audit" / "known_names_v1.csv"

# 성분표를 성분명으로 쪼개는 규칙 둘. 우리 원천에만 있는 함정이라 ydc 에 대응이 없다 (계약 §성분).
BRACKET_RE = re.compile(r"\[[^\]]*\]")
STAR_NOTE_RE = re.compile(r"^[^\S\n]*\*.*$", re.MULTILINE)
# An ingredient list written with spaces and no commas stays one lump. Splitting it quietly would stand the
# blending order on a wrong value, so it is only counted.
RUN_ON_SPACES = 5


@dataclass(frozen=True)
class KeyAudit:
    """What one key actually catches. **A check for substring mismatches alone** -- the numbers cannot catch
    it."""

    key: str
    terms: tuple[str, ...]
    rows: int
    products: int
    names: tuple[tuple[str, int], ...] = ()
    denied: tuple[str, ...] = ()

    @property
    def suspect(self) -> bool:
        """Did it catch an ingredient name a person checked once and forbade. 0 rows is absence rather than a
        mismatch, so it passes."""
        return bool(self.denied)


@dataclass(frozen=True)
class IngredientRow:
    """The three discourse counts of one ingredient. The formulation and paper columns are locked, so they
    are None (FORMULA_HOLD · PAPER_HOLD)."""

    ingredient: str
    talk_youtube: int
    talk_youtube_sun: int
    talk_commerce: int
    formula_products: int | None = None
    formula_pct: float | None = None
    median_order: int | None = None
    high_dose_pct: float | None = None
    reading: str = ""

    @property
    def sun_share(self) -> float:
        return 100 * self.talk_youtube_sun / self.talk_youtube if self.talk_youtube else 0.0


@dataclass(frozen=True)
class Ingredients:
    rows: tuple[IngredientRow, ...] = ()
    audits: tuple[KeyAudit, ...] = ()
    formula_products: int = 0
    run_on_lists: int = 0
    names: int = 0

    @property
    def suspects(self) -> tuple[KeyAudit, ...]:
        return tuple(audit for audit in self.audits if audit.suspect)


def ranks(values: Mapping[str, float]) -> dict[str, int]:
    """The largest value is rank 1. The comparison is by rank rather than by size (ydc
    `cross_source.ranks`)."""
    order = sorted(values, key=lambda key: -values[key])
    return {key: place + 1 for place, key in enumerate(order)}


def composition(
    mentions: Mapping[str, Mapping[str, int]], topic_keys: Sequence[str]
) -> tuple[SourceShare, ...]:
    """소스별 (주제 -> 언급 문서 수) -> 주제마다 한 줄. 분모는 그 소스의 주제 언급 문서 수 합이다.

    분모를 `topic_keys` 로 좁히는 것이 뜻이다 -- 축 밖 주제(`trend_use` 가 거짓인 `선크림`·`추천_재구매`)
    가 분모에 들면 모든 소스의 구성비가 조용히 작아진다.
    """
    total = {source: sum(counts.get(topic, 0) for topic in topic_keys) for source, counts in mentions.items()}
    shares = {
        source: {
            topic: (100 * mentions[source].get(topic, 0) / total[source] if total[source] else 0.0)
            for topic in topic_keys
        }
        for source in mentions
    }
    place = {source: ranks(shares[source]) for source in mentions}
    return tuple(
        SourceShare(
            topic_key=topic,
            documents={source: mentions[source].get(topic, 0) for source in mentions},
            shares={source: shares[source][topic] for source in mentions},
            ranks={source: place[source][topic] for source in mentions},
            reading=share_reading({source: shares[source][topic] for source in mentions}),
        )
        for topic in topic_keys
    )


def share_reading(shares: Mapping[str, float]) -> str:
    """Which side is the vessel for that topic (ydc `source_composition.reading` + one line of
    `cross_source`)."""
    creator, consumer = shares.get(CREATOR, 0.0), shares.get(CONSUMER, 0.0)
    comment = shares.get(COMMENT, 0.0)
    if consumer >= LEAD_PP and creator < THIN_PP:
        return READ_CONSUMER_ONLY
    if consumer - creator >= LEAD_PP:
        return READ_CONSUMER_LEAD
    if creator - consumer >= LEAD_PP:
        return READ_CREATOR_LEAD
    # One line of ydc `cross_source.topic_table`. It is not attached when the usage side is 0 -- a topic
    # nobody speaks of is not a creator's blind spot.
    if consumer > 0 and creator < SPARSE_PP and comment > creator * TALK_RATIO:
        return READ_COMMENT_ONLY
    return ""


@lru_cache(maxsize=1)
def confirmed_polarity() -> dict[tuple[str, str], str]:
    """(topic_group, topic_name) -> the polarity a person confirmed. It answers **before** the hints."""
    with POLARITY_CSV.open(encoding="utf-8-sig", newline="") as handle:
        return {(row["topic_group"], row["topic_name"]): row["polarity"] for row in csv.DictReader(handle)}


def polarity(topic_name: str, *, topic_group: str | None = None) -> str:
    """확인된 표가 먼저, 없으면 힌트. 힌트 안에서는 중립이 먼저다 -- `보통이에요` 는 부정 힌트를 하나도
    갖지 않지만 긍정도 아니다. 그룹을 안 주면 힌트만 도는데, 그것이 ydc `commerce_crosscheck.polarity`
    와 같은 답이라 대조가 선다 (`tool/compare-ydc-crosscheck`)."""
    name = (topic_name or "").strip()
    if topic_group is not None:
        known = confirmed_polarity().get((topic_group, name))
        if known is not None:
            return known
    if any(hint in name for hint in NEUTRAL_HINTS):
        return "neutral"
    if any(hint in name for hint in NEGATIVE_HINTS):
        return "negative"
    return "positive"


def positive_rate(choices: Sequence[tuple[str, float]], *, topic_group: str | None = None) -> float | None:
    """The share the positive choices take inside one product and one topic_group."""
    total = sum(share for _name, share in choices)
    if not total:
        return None
    positive = sum(share for name, share in choices if polarity(name, topic_group=topic_group) == "positive")
    return 100 * positive / total


def rating_reading(positive_rate_mean: float, gap_pp: float | None) -> str:
    """Many mentions with low satisfaction is room to improve; both high is a strength already solved.

    A cell whose gap is unknown (there is no judgement row) is not read as the large-gap side -- unknown and
    small are different, and reading the unknown as large claims a gap on grounds that do not exist.
    """
    wide = gap_pp is not None and gap_pp > GAP_PP_MATERIAL
    if wide:
        return READ_GAP_UNHAPPY if positive_rate_mean < POSITIVE_RATE_HIGH else READ_GAP_HAPPY
    return READ_QUIET_UNHAPPY if positive_rate_mean < POSITIVE_RATE_HIGH else READ_SATURATED


def ratings(
    rated: Mapping[tuple[str, str, str], Sequence[tuple[str, float]]],
    judged: Mapping[str, tuple[int | None, float | None, float | None, str]],
) -> tuple[RatingRow, ...]:
    """(source, product, topic_group) -> the choices, and the judgement of that run -> one line per topic."""
    per_topic: dict[str, list[float]] = {}
    groups: dict[str, set[str]] = {}
    for (_source, _product, group), choices in rated.items():
        topic = GROUP_MAP.get(group)
        # 대응이 없는 그룹은 넣지 않는다. 전량에서 `피부타입` 이 그 자리다.
        if topic is None:
            continue
        rate = positive_rate(choices, topic_group=group)
        if rate is None:
            continue
        per_topic.setdefault(topic, []).append(rate)
        groups.setdefault(topic, set()).add(group)
    made: list[RatingRow] = []
    for topic, rates in per_topic.items():
        mean = statistics.mean(rates)
        rank, composition_pct, gap, trend_type = judged.get(topic, (None, None, None, ""))
        row = RatingRow(
            topic_key=topic,
            commerce_groups=tuple(sorted(groups[topic])),
            products_rated=len(rates),
            positive_rate_mean=round(mean, 1),
            positive_rate_median=round(statistics.median(rates), 1),
            youtube_rank_comment=rank,
            youtube_composition_pct=composition_pct,
            youtube_gap_pp=gap,
            youtube_trend_type=trend_type,
        )
        # 표본이 얇은 주제는 수치를 그대로 싣되 해석을 쓰지 않는다 (계약 §평가).
        made.append(row if row.thin else replace(row, reading=rating_reading(mean, gap)))
    made.sort(key=lambda row: (-row.products_rated, row.topic_key))
    return tuple(made)


def parse_ingredients(text: str) -> list[str]:
    """One ingredient list into ingredient names. A bracketed section marker is dropped and a comma inside
    parentheses is not cut."""
    body = BRACKET_RE.sub(" ", STAR_NOTE_RE.sub(" ", text or ""))
    out: list[str] = []
    depth, current = 0, []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char in ",\n" and depth == 0:
            out.append("".join(current))
            current = []
        else:
            current.append(char)
    out.append("".join(current))
    return [name.strip() for name in out if name.strip()]


def run_on(name: str) -> bool:
    """Is it one lump of an ingredient list written with spaces and no commas. It is only counted, not split
    quietly."""
    return name.count(" ") >= RUN_ON_SPACES


def known_names(path: Path | None = None) -> dict[str, frozenset[str]]:
    """Key -> the ingredient names it is confirmed to catch. A key that catches nothing answers with an empty
    set."""
    found: dict[str, set[str]] = {key: set() for key in INGREDIENT_KEYS}
    with (path or KNOWN_NAMES_CSV).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            found.setdefault(row["key"], set()).add(row["ingredient"])
    return {key: frozenset(names) for key, names in found.items()}


def matches(name: str, terms: Iterable[str]) -> bool:
    """**성분명** 전용. 공백과 대소문자를 접는다 -- 성분표는 `나이아신아마이드 (20,000 ppm)` 처럼
    같은 성분을 띄어쓰기만 달리 적는다."""
    folded = name.replace(" ", "").lower()
    return any(term.replace(" ", "").lower() in folded for term in terms)


def mentions_term(text: str, terms: Iterable[str]) -> bool:
    """**담론** 전용. 원문 그대로 본다 (ydc `count_terms`). 자유 문장에서 공백을 접으면 낱말 경계를
    넘어 붙어(`... 콜라` + `겐 ...`) 없는 언급이 생긴다."""
    return any(term in text for term in terms)


def denied_in(key: str, names: Iterable[str]) -> tuple[str, ...]:
    """Which of the names this key caught fall under the ban. The same width as the matcher (a substring with
    whitespace and case folded)."""
    forbidden = {**DENIED_NAMES, **DENIED_FOR.get(key, {})}
    found = list(names)
    return tuple(sorted({bad for bad in forbidden if any(matches(hit, (bad,)) for hit in found)}))


def audit(
    rows: Sequence[tuple[str, str]],
    *,
    keys: Mapping[str, tuple[str, ...]] | None = None,
    top: int = 5,
) -> tuple[KeyAudit, ...]:
    """(제품 키, 성분명) 행들 -> 키마다 실제로 잡히는 고유 성분명.

    채택률만 보면 `시카` 의 41.1% 는 그럴듯했다. 이름을 찍으면 즉시 보인다.
    """
    table = keys if keys is not None else INGREDIENT_KEYS
    made: list[KeyAudit] = []
    for key, terms in table.items():
        hit = [(product, name) for product, name in rows if matches(name, terms)]
        names = Counter(name for _product, name in hit)
        made.append(
            KeyAudit(
                key=key,
                terms=terms,
                rows=len(hit),
                products=len({product for product, _name in hit}),
                names=tuple(names.most_common(top)),
                # **게이트는 매처와 같은 폭이어야 한다.** 완전 일치로 물으면 매처가 부분문자열로
                # 잡은 `트라이에톡시카프릴릴실레인 (1%)` 나 `레티놀(0.04 ppm)` 을 게이트가 못 본다 --
                # 운영 표의 `레티놀` 7행 중 4행이 이미 그런 접미사형이다.
                denied=denied_in(key, names),
            )
        )
    return tuple(made)


def ingredient_reading(row: IngredientRow) -> str:
    """담론 수를 "선크림 담론" 으로 읽지 말라고 말하는 한 줄 (계약 §성분)."""
    if row.talk_youtube and row.sun_share < SUN_SHARE_LOW:
        return f"{READ_NOT_SUNCARE} (선크림 문맥 {row.sun_share:.1f}%)"
    return ""


__all__ = [
    "COMMENT",
    "DENIED_FOR",
    "DENIED_NAMES",
    "COMMERCE_REVIEW",
    "CONSUMER",
    "CREATOR",
    "FORMULA_HOLD",
    "GAP_PP_MATERIAL",
    "GROUP_MAP",
    "INGREDIENT_KEYS",
    "LEAD_PP",
    "MIN_PRODUCTS",
    "PAPER_HOLD",
    "POLARITY_CSV",
    "POSITIVE_RATE_HIGH",
    "REJECTED_TERMS",
    "SOURCES",
    "SPARSE_PP",
    "SUN_SHARE_LOW",
    "SUN_WORDS",
    "TALK_RATIO",
    "THIN_PP",
    "TRANSCRIPT",
    "VIDEO_TITLE",
    "Ingredients",
    "IngredientRow",
    "KeyAudit",
    "KNOWN_NAMES_CSV",
    "RatingRow",
    "SourceShare",
    "audit",
    "composition",
    "confirmed_polarity",
    "denial_reason",
    "denied_in",
    "ingredient_reading",
    "known_names",
    "matches",
    "mentions_term",
    "parse_ingredients",
    "polarity",
    "positive_rate",
    "ranks",
    "run_on",
    "rating_reading",
    "ratings",
    "share_reading",
]
