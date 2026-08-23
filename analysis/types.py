# analysis/types.py — contracts/interfaces.md 의 코드 블록과 같아야 한다 (tests/test_contract_types.py).
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol


# ---------- 입력 ----------
@dataclass(frozen=True)
class TextUnit:  # 분석 입력의 최소 단위
    src: str  # review | yt_comment | yt_transcript | yt_title | naver_blog
    site: str
    ref: str  # 안정 키. 문법은 formats.md §ref
    text: str
    observed_at: date
    observed_at_resolution: str  # day | month | year
    rating: float | None = None
    like_count: int | None = None
    view_count: int | None = None  # A11: 자막 단위의 가중치
    product_key: str | None = None
    category: str | None = None  # 사이트 원문. 사전 선택은 lexicon_category (formats.md)
    channel_id: str | None = None


# ---------- 사전 ----------
@dataclass(frozen=True)
class EntitySurface:  # 사전 한 행 = entity_lexicon
    # product_line 은 여기 없다: 라인은 표제어가 아니라 brand + line_tokens 로 합성된다 (A14).
    kind: str  # brand | format | attribute | ingredient | stopword | alias (001 의 CHECK 와 같은 어휘)
    canonical: str
    surface: str
    tier: str | None  # brand: normal | cooc_required | stop
    source: str | None


@dataclass(frozen=True)
class Lexicon:
    """entity_lexicon 한 버전. 링커와 바람 추출기가 필요로 하는 전부."""

    version: int
    surfaces: tuple[EntitySurface, ...]
    surface_to_canonical: Mapping[str, str]  # lower-case 키 포함
    surface_re: re.Pattern[str]  # 길이 내림차순 + 조사 허용
    stop: frozenset[str]
    cooc_required: frozenset[str]
    product_word_re: re.Pattern[str]  # 제품어 공기 판정용
    cooc_window: int = 25  # 좌우 25자
    format_patterns: tuple[tuple[str, re.Pattern[str]], ...] = ()
    attribute_patterns: tuple[tuple[str, re.Pattern[str]], ...] = ()


@dataclass(frozen=True)
class AspectPattern:
    aspect: str  # need_key
    scope: str  # generic | category
    category: str  # scope=category 일 때만, generic 은 ''
    pattern: re.Pattern[str]
    is_neutral_noun: bool  # 중립 명사 쌍둥이
    priority: int  # B5: 오름차순 매칭, 동률은 id
    ruleset: str  # B4: suncare-v2.2 | p1-v2.2 | shared


@dataclass(frozen=True)
class AspectLexicon:
    """polarity.classify 와 extractor.candidates 가 함께 쓴다. 로더는 ruleset IN (요청, 'shared')."""

    version: int
    ruleset: str
    patterns: tuple[AspectPattern, ...]
    discourse_marker_re: re.Pattern[str]
    wish_marker_re: re.Pattern[str]

    def for_category(self, category: str | None) -> tuple[AspectPattern, ...]:
        """priority 오름차순, 동률은 id — category 전용이 같은 이름의 generic 을 가린다."""
        ...

    def complaint_marker_re(self, category: str | None) -> re.Pattern[str]:
        """담화 표지 | 그 카테고리의 전 패턴."""
        ...


# ---------- 제품 식별 ----------
@dataclass(frozen=True)
class ProductRow:
    source: str
    product_key: str
    name: str
    brand: str | None
    volume: str | None = None
    first_ranked: date | None = None
    review_from: date | None = None
    reviews_collected: int = 0


@dataclass(frozen=True)
class ProductRefRow:  # → needs.product_ref
    product_ref: str
    brand: str | None
    name_norm: str  # T19: 링커가 반드시 낸다
    name: str
    n_sites: int
    first_seen: date | None
    linker_version: str


@dataclass(frozen=True)
class ProductMemberRow:  # → needs.product_member
    source: str
    product_key: str
    product_ref: str
    role: str  # primary | member
    match_score: float | None  # A13: 같은 쌍의 candidates.dice


@dataclass(frozen=True)
class ProductVariantRow:  # → needs.product_variant (B3: 산출 알고리즘이 없어 #2 범위 밖)
    source: str
    product_key: str
    variant_of: str
    variant_kind: str  # refill | size | scent | shade | option | set
    variant_label: str | None


@dataclass(frozen=True)
class ProductCandidateRow:  # → needs.product_ref_candidate (A13: 사람 검수 큐)
    src_a: str
    key_a: str
    src_b: str
    key_b: str
    brand: str | None
    shared_tok: int
    shared_sig: int
    dice: float
    mutual: bool


@dataclass(frozen=True)
class ProductMatch:  # B2: 한 번의 유니온-파인드가 네 가지를 동시에 낸다
    refs: tuple[ProductRefRow, ...]
    members: tuple[ProductMemberRow, ...]
    variants: tuple[ProductVariantRow, ...] = ()
    candidates: tuple[ProductCandidateRow, ...] = ()


# ---------- 추출 ----------
@dataclass(frozen=True)
class EntityHit:  # linker 출력
    kind: str  # brand | format | attribute | ingredient | product_line
    canonical: str
    surface: str
    start: int
    end: int
    cooc: bool  # 제품어 공기 여부


@dataclass(frozen=True)
class Candidate:  # extractor 출력 (문장 단위)
    unit_ref: str
    sentence: str
    kind: str  # complaint | wish | low_rating
    marker: str
    subject: str | None = None  # A10: 제품명·영상 제목 — 사람이 후보를 읽을 때의 맥락


@dataclass(frozen=True)
class PolarityResult:
    aspect: str | None  # B8: 없음은 need_key='' 로 저장한다
    polarity: str  # 불만 | 만족 | 중립
    reason: str
    version: str  # rule-v2.2 | llm-<model>-<date>


@dataclass(frozen=True)
class WishResult:
    wish_class: str  # a | b | c | n
    brand: str | None
    format: str | None  # A12: ';' 구분 최대 3, 첫 번째가 주 값 (formats.md)
    attribute: str | None
    marker: str | None
    sentence: str = ""  # B1: 어느 문장이 걸렸는지 — wish_mention.sentence 는 NOT NULL


# ---------- 언급 행 ----------
@dataclass(frozen=True)
class NeedMentionRow:  # → needs.need_mention
    src: str
    site: str
    ref: str
    product_ref: str | None
    source_product_key: str | None
    category: str | None  # 사이트 원문 카테고리
    lexicon_category: str | None  # B10: 사전 선택에 쓴 카테고리
    need_key: str  # B8: aspect 없음 = ''
    aspect_scope: str | None  # generic | category
    polarity: str  # 불만 | 만족 | 중립
    strength: float | None  # review: 1 - rating/5 · comment: like_count
    rating: float | None
    observed_at: date
    observed_at_resolution: str
    month: str
    sentence: str
    kind: str | None  # A9: complaint | wish | low_rating
    marker: str | None  # A9
    polarity_reason: str | None  # B9
    extractor_version: str
    polarity_version: str


@dataclass(frozen=True)
class WishMentionRow:  # → needs.wish_mention
    src: str
    ref: str  # A20: 'video_id/comment_id'
    video_id: str | None
    channel_id: str | None
    channel_is_brand_owner: bool | None
    product_ref: str | None
    observed_at: date
    observed_at_resolution: str
    month: str
    wish_class: str  # a | b | c ('n' 은 저장하지 않는다)
    brand: str | None
    format: str | None
    attribute: str | None
    marker: str | None
    sentence: str
    like_count: int | None
    extractor_version: str


@dataclass(frozen=True)
class DenominatorRow:  # → needs.product_denominator
    source: str
    product_key: str
    captured_at: date
    category: str | None  # B6: 카테고리 분모 합산에 필수
    site_review_count: int | None
    low_collected: int | None
    low_complete: bool | None
    site_low_est: float | None


# ---------- 집계 ----------
@dataclass(frozen=True)
class MetricsNeedRow:  # → needs.metrics_need
    run_id: int
    scope: str  # 카테고리명 | 'all'
    need_key: str
    month: str = ""  # '' = 전체 기간
    product_ref: str = ""  # '' = 카테고리 합
    neg: int = 0
    pos: int = 0
    yt_neg: int | None = None
    yt_pos: int | None = None
    unresolved: float | None = None
    unresolved_new: float | None = None
    low_share: float | None = None
    population_share_pct: float | None = None
    low_mentioning: int | None = None
    denom_low: int | None = None
    denom_site: int | None = None
    strength_mean: float | None = None
    strength_low_rating_ratio: float | None = None
    persist_months: int | None = None
    persist_months_total: int | None = None
    persist_products: int | None = None
    persist_products_total: int | None = None
    aspect_scope: str | None = None


@dataclass(frozen=True)
class MetricsWishRow:  # → needs.metrics_wish
    run_id: int
    scope: str  # 'wish:a' | 'wish:b' | 'wish:a:format×attr'
    format: str = ""
    attribute: str = ""
    brand: str = ""
    mentions: int = 0
    channels: int | None = None
    videos: int | None = None
    months_present: int | None = None
    first_month: str | None = None
    last_month: str | None = None
    like_sum: int | None = None
    like_cap_sum: float | None = None  # A8
    max_like: int | None = None
    example: str | None = None


# ---------- 프로토콜 ----------
class Linker(Protocol):
    version: str

    def link(self, unit: TextUnit, lexicon: Lexicon) -> list[EntityHit]: ...
    def match_products(self, products: Iterable[ProductRow]) -> ProductMatch: ...  # B2


class Extractor(Protocol):
    version: str

    def candidates(self, unit: TextUnit, aspects: AspectLexicon) -> list[Candidate]: ...
    def wishes(self, unit: TextUnit, lexicon: Lexicon) -> WishResult | None: ...


class Polarity(Protocol):  # ← LLM 삽입점. 규칙 구현과 LLM 구현이 같은 시그니처
    version: str

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult: ...  # category 는 lexicon_category 다 (사이트 원문 아님)


class Aggregator(Protocol):
    version: str

    def need_metrics(
        self, mentions: Iterable[NeedMentionRow], denominators: Iterable[DenominatorRow], scope: str
    ) -> list[MetricsNeedRow]: ...
    def wish_metrics(self, wishes: Iterable[WishMentionRow], scope: str) -> list[MetricsWishRow]: ...
