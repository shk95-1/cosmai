"""소스를 나란히 놓고 어긋나는 자리를 찾는다 (포크 #7). **합산하지 않는다.**

왜 합산하지 않나. 소스마다 분모가 다르다 -- 구성비는 그 소스의 13주제 언급 합 대비, 플랫폼 속성 평가는
`topic_group` 안의 응답 비중, 성분 담론은 문서 수다. 더하거나 평균 내면 그 순간 뜻이 없어진다. 그래서
**크기가 아니라 순위와 방향**을 본다 (`contracts/interfaces.md` §대조).

여기는 규칙만 산다. DB 는 `analysis/crosscheck/pipeline.py` 이고, 그쪽이 이 함수들에 값을 먹인다 --
`analysis/sensitivity` 와 같은 가름이다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# ---------------------------------------------------------------- 구성 (ydc source_composition.py)

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
    """한 주제가 소스마다 차지하는 자리. 소스 간 문서 수를 합산하지 않는다."""

    topic_key: str
    documents: Mapping[str, int]
    shares: Mapping[str, float]
    ranks: Mapping[str, int]
    reading: str = ""


# ---------------------------------------------------------------- 평가 (ydc commerce_crosscheck.py)

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
NEGATIVE_HINTS = ("느껴져요", "아쉬", "부족", "무거", "끈적", "밀려", "answer_no", "없어요")
NEUTRAL_HINTS = ("보통",)

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
    """한 주제에 대한 플랫폼 속성 평가와 그 run 의 판정. 값이 아니라 방향을 본다."""

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


# ---------------------------------------------------------------- 성분 (ydc cross_source.py 성분 축)

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
REJECTED_TERMS: dict[str, str] = {
    "시카": "216행 전부 트라이에톡시카프릴릴실레인(209)·트리에톡시카프릴릴실란(7) -- 실리콘 분산제다",
    "센텔라": "0행. 우리 성분표는 병풀·마데카소사이드·아시아티코사이드로 적는다",
    "레티놀": "7행. `레티날` 은 0행이고 레티놀과 레티날은 다른 물질이다",
}
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

# 성분표를 성분명으로 쪼개는 규칙 둘. 우리 원천에만 있는 함정이라 ydc 에 대응이 없다 (계약 §성분).
BRACKET_RE = re.compile(r"\[[^\]]*\]")
STAR_NOTE_RE = re.compile(r"^[^\S\n]*\*.*$", re.MULTILINE)
# 쉼표 없이 공백으로만 나열한 성분표는 한 덩어리로 남는다. 조용히 쪼개면 배합 순위가 틀린 값으로 서므로
# 세기만 한다.
RUN_ON_SPACES = 5


@dataclass(frozen=True)
class KeyAudit:
    """키 하나가 실제로 무엇을 잡는가. **부분문자열 오매칭 전용 검사다** -- 눈으로는 못 잡는다."""

    key: str
    terms: tuple[str, ...]
    rows: int
    products: int
    names: tuple[tuple[str, int], ...] = ()

    @property
    def suspect(self) -> bool:
        """잡힌 성분명에 키가 하나도 안 들어 있는가. 0행은 오매칭이 아니라 부재라 통과다."""
        return bool(self.rows) and not any(
            any(term in name for term in self.terms) for name, _count in self.names
        )


@dataclass(frozen=True)
class IngredientRow:
    """성분 하나의 담론 셋. 처방·논문 칸은 잠겨 있어 None 이다 (FORMULA_HOLD · PAPER_HOLD)."""

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
    """큰 값이 1위. 비교는 크기가 아니라 순위로 한다 (ydc `cross_source.ranks`)."""
    raise NotImplementedError


def composition(
    mentions: Mapping[str, Mapping[str, int]], topic_keys: Sequence[str]
) -> tuple[SourceShare, ...]:
    """소스별 (주제 -> 언급 문서 수) -> 주제마다 한 줄. 분모는 그 소스의 주제 언급 문서 수 합이다."""
    raise NotImplementedError


def share_reading(shares: Mapping[str, float]) -> str:
    """어느 쪽이 그 주제를 담는 그릇인가 (ydc `source_composition.reading` + `cross_source` 한 줄)."""
    raise NotImplementedError


def polarity(topic_name: str) -> str:
    raise NotImplementedError


def positive_rate(choices: Sequence[tuple[str, float]]) -> float | None:
    """한 제품·한 topic_group 안에서 긍정 선택지가 차지하는 비중."""
    raise NotImplementedError


def rating_reading(positive_rate_mean: float, gap_pp: float | None) -> str:
    """언급이 많은데 만족도가 낮으면 개선 여지, 둘 다 높으면 이미 해결된 강점."""
    raise NotImplementedError


def ratings(
    rated: Mapping[tuple[str, str, str], Sequence[tuple[str, float]]],
    judged: Mapping[str, tuple[int | None, float | None, float | None, str]],
) -> tuple[RatingRow, ...]:
    """(소스, 제품, topic_group) -> 선택지들, 그리고 그 run 의 판정 -> 주제마다 한 줄."""
    raise NotImplementedError


def parse_ingredients(text: str) -> list[str]:
    """성분표 한 장을 성분명으로. 대괄호 구간 표시는 버리고 괄호 안의 쉼표는 자르지 않는다."""
    raise NotImplementedError


def matches(name: str, terms: Iterable[str]) -> bool:
    raise NotImplementedError


def audit(
    rows: Sequence[tuple[str, str]],
    *,
    keys: Mapping[str, tuple[str, ...]] | None = None,
    top: int = 5,
) -> tuple[KeyAudit, ...]:
    """(제품 키, 성분명) 행들 -> 키마다 실제로 잡히는 고유 성분명."""
    raise NotImplementedError


def ingredient_reading(row: IngredientRow) -> str:
    raise NotImplementedError


__all__ = [
    "COMMENT",
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
    "RatingRow",
    "SourceShare",
    "audit",
    "composition",
    "ingredient_reading",
    "matches",
    "parse_ingredients",
    "polarity",
    "positive_rate",
    "ranks",
    "rating_reading",
    "ratings",
    "share_reading",
]
