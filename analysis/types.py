# analysis/types.py — contracts/interfaces.md 의 코드 블록과 같아야 한다 (tests/test_contract_types.py).
import re
from collections.abc import Iterable, Mapping, Sequence
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
        # 반환 순서는 patterns 가 이미 (priority, id) 로 실려 온다는 로더의 약속에 기댄다.
        specific = tuple(p for p in self.patterns if p.scope == "category" and p.category == category)
        # 쌍둥이(중립 명사)는 같은 이름을 갖고 따로 살아남아야 하므로 is_neutral_noun 까지 키에 넣는다.
        hidden = {(p.aspect, p.is_neutral_noun) for p in specific}
        generic = tuple(
            p for p in self.patterns if p.scope == "generic" and (p.aspect, p.is_neutral_noun) not in hidden
        )
        return specific + generic

    def complaint_marker_re(self, category: str | None) -> re.Pattern[str]:
        """담화 표지 | 그 카테고리의 전 패턴."""
        # 카테고리마다 다른 합집합이라 로드 시점에 만들 수 없다 — 부르는 쪽이 카테고리별로 캐시한다.
        parts = [self.discourse_marker_re.pattern]
        parts += [p.pattern.pattern for p in self.for_category(category)]
        return re.compile("|".join(parts))


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
class PolarityRequest:  # classify_many 한 건. classify 의 인자를 그대로 묶은 것이다
    sentence: str
    rating: float | None = None
    category: str | None = None


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


@dataclass(frozen=True)
class PanelRosterRow:  # → needs.panel_roster (포크 #3). 명부 판본 한 줄 — panel_version 이 가리킬 부모
    version: int
    note: str | None = None  # 이 판본이 무엇인지 (seed:channels_v1 …)


@dataclass(frozen=True)
class PanelChannelRow:  # → needs.panel_channel (포크 #3). 43채널 패널 명부; 값은 시드가 채운다 (#31)
    channel_id: str
    version: int  # 명부 판본. 사전과 같은 모양이다 (formats.md §패널 명부 CSV)
    panel_role: str  # product | expert — 명부에 없는 채널은 패널 밖이라 분모에 안 들어간다
    handle: str | None = None
    channel_title: str | None = None
    role_basis: str | None = None  # 역할을 그렇게 정한 근거 (team_message | name_rule_verified …)
    source_list: str | None = None
    active: bool = True


@dataclass(frozen=True)
class MetricsTopicQuarterRow:  # → needs.metrics_topic_quarter (분기 입자의 정본, formats.md §시간)
    run_id: int
    scope: str  # 카테고리명 | 'all' (metrics_need.scope 와 같은 어휘)
    # 주제 축의 레지스트리는 aspect_lexicon(ruleset='retrieval-topic').aspect 이고 needs.need_key 가 아니다
    topic_key: str  # 두 축은 `백탁` 하나만 겹친다 (tests/test_panel_quarter_contract.py)
    quarter: str  # 'YYYYQn'
    source: str  # youtube_video | youtube_comment — 영상 설명과 댓글은 합치지 않고 나란히 낸다
    content_type: str  # long_form | short_form — 분모는 장문만이다 (§수식)
    panel_version: int  # 이 비율의 모집단: panel_channel.version
    panel_role: str  # 그 명부의 어느 모집단인지. product | expert
    mentions: int  # 분자: 이 주제가 걸린 문서 수
    documents: int  # 그 분기 그 모집단의 문서 수
    quarter_mentions: int  # 구성비의 분모: 그 분기 trend_use 주제들의 언급 합
    denom_channels: int  # 그 분기에 산출에 든 패널 채널 수. 두 source 가 같은 값을 쓴다 (§수식)
    composition: float | None = None
    velocity_yoy: float | None = None
    persistence: float | None = None
    persist_quarters: int | None = None
    window_quarters: int | None = None
    unique_ratio: float | None = None
    channel_count: int | None = None
    channel_diffusion: float | None = None
    sample_ok: bool = False


@dataclass(frozen=True)
class TopicQuarterJudgementRow:  # → needs.topic_quarter_judgement (판정. 집계가 아니라 파생 — §판정)
    # 앞 여덟 칸은 metrics_topic_quarter 의 기본키 그대로다. 판정은 그 표의 한 행을 받아 한 행을 내므로
    # 이 여덟이 곧 FK 이고, 그래서 판정 행은 자기 근거가 되는 지표 행 없이 존재할 수 없다.
    run_id: int
    scope: str
    topic_key: str
    quarter: str
    source: str
    content_type: str
    panel_version: int
    panel_role: str
    trend_type: str  # 유형 7종 + 판정 보류 + 미확정(진행 중) — 어휘는 §판정 이 닫는다
    judged: bool  # 유형 7종에서 `근거 부족` 을 뺀 여섯에 들었는가. 셋(근거 부족·보류·미확정)이면 false
    evidence_strength: float  # 0~100 (§판정)
    single_source: bool  # 이 판정이 소스 하나만 보고 내려졌는가. v1(YouTube 단독)은 언제나 true
    opportunity_score: float | None = None  # 제품군 내 0~100 정규화. 점수 대상이 아닌 셀은 NULL
    gap_pp: float | None = None  # 댓글 구성비 - 영상 구성비 (%p). (주제, 분기) 사실이라 두 행이 같은 값
    hold_reason: str = ""  # `판정 보류` 의 사유 코드. 보류가 아니면 '' (§판정 의 닫힌 어휘)


@dataclass(frozen=True)
class TopicQuarterEvidenceRow:  # → needs.topic_quarter_evidence (판정 셀을 받치는 소비자 발화 — §근거)
    # 앞 여덟 칸은 topic_quarter_judgement 의 기본키 그대로다. 근거가 가리키는 것이 지표 행이 아니라
    # 판정 셀인 것이 뜻이다 — 근거를 묻는 사람은 유형을 읽은 사람이다.
    run_id: int
    scope: str
    topic_key: str
    quarter: str
    source: str
    content_type: str
    panel_version: int
    panel_role: str
    rank: int  # 그 셀 안 좋아요 내림차순 자리. 1 부터 빈칸 없이 (§근거)
    snapshot_id: int  # 근거 문서가 사는 관측 판본. doc_id 하나로는 재수집분과 갈리지 않는다
    doc_id: str  # 본문은 여기 없다 — 코퍼스가 정본이고 뷰 topic_quarter_evidence_quote 가 잇는다
    like_count: int  # 고른 이유. collected_at 시점의 스냅샷이라 나중에 세면 다른 수다
    matched_term: str | None = None  # corpus_mention 이 이미 단 표현. 여기서 다시 매칭하지 않는다


# ---------- 민감도 (반사실 산출. 어느 표에도 저장되지 않는다 — §민감도) ----------
@dataclass(frozen=True)
class PanelSensitivityRow:  # 패널 구성이 결론을 바꾸는가 (ydc panel_sensitivity.py)
    source: str
    topic_key: str
    quarters_ok_product: int  # 언급이 표본 게이트를 넘는 분기 수 — product 만인 산출
    quarters_ok_all: int  # 같은 것을 43채널 전부(product+expert)로 잰 값
    delta_product_pp: float  # 최근 4분기 구성비 − 직전 4분기 구성비 (%p)
    delta_all_pp: float
    difference_pp: float  # 두 델타의 차. 반올림 전 값끼리 뺀다
    sample_ok: bool  # 충족 분기가 관측 분기의 절반을 넘는가. 아니면 애초에 판정 대상이 아니다


@dataclass(frozen=True)
class BacktestRow:  # 그때 알 수 있었는가 (ydc backtest.py)
    cutoff: str  # 판정 대상 분기 T. 지표는 T 다음 분기까지만 알던 것처럼 다시 셌다
    source: str
    topic_key: str
    trend_type: str  # 방향이 있는 넷뿐이다 — 급상승·신규 등장·사라짐·단기 피크
    before_pp: float  # 직전 4분기 평균 구성비 (기준 A)
    before_excl_pp: float  # T 를 뺀 직전 4분기 평균 (기준 B — "올라간 수준이 유지됐는가")
    after_pp: float  # C 이후 4분기 평균
    at_cutoff_pp: float  # T 분기의 구성비. `단기 피크` 의 비교 상대다
    expected: str  # 상승 유지 | 하락 유지 | 피크 소멸
    actual: str  # 상승 | 하락 (기준 A 의 비교 결과)
    hit: bool  # 기준 A
    hit_level: bool  # 기준 B


@dataclass(frozen=True)
class AdSensitivityRow:  # 광고·협찬을 빼도 결론이 같은가 (ydc spam_ad_flags.py)
    variant: str  # ad_video | creator_comment | promo_comment | all_flagged
    source: str
    topic_key: str
    composition_base_pp: float  # 최근 4분기 구성비 (기저)
    composition_kept_pp: float  # 같은 것을 그 변형에서 잰 값
    diff_pp: float
    judged_cells: int  # 그 (source, 주제) 에서 기저가 판정한 셀 수
    flipped_cells: int  # 그중 유형이 바뀐 셀 수. 표본 미달로 사라진 셀은 여기 들지 않는다


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
    def classify_many(
        self, items: Sequence[PolarityRequest], aspects: AspectLexicon
    ) -> list[PolarityResult]: ...  # 배치 API 를 가진 구현(#6)만 이득이다. 입력과 같은 길이·순서


class Aggregator(Protocol):
    version: str

    def need_metrics(
        self, mentions: Iterable[NeedMentionRow], denominators: Iterable[DenominatorRow], scope: str
    ) -> list[MetricsNeedRow]: ...
    def wish_metrics(self, wishes: Iterable[WishMentionRow], scope: str) -> list[MetricsWishRow]: ...


# ---------- 평가 ----------
@dataclass(frozen=True)
class LabeledRow:  # needs.labeled_set 한 행. eval 하네스가 구현체에 넘기는 유일한 입력
    task: str
    ref: str
    split: str
    gold: str
    text: str
    extra: Mapping[str, object]  # 셋 이름(`set`)·rating·in_final 등 원본 CSV 의 나머지 열


class Predictor(Protocol):  # eval 구현체. 배치로 받고 입력과 같은 길이·순서로 라벨을 돌려준다
    def __call__(self, rows: Sequence[LabeledRow]) -> Sequence[str]: ...
