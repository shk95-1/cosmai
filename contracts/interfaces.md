# 분석 패키지 인터페이스 (Python, 타입은 dataclass/Protocol)

`analysis/types.py` 와 이 코드 블록은 **같아야 한다** — `tests/test_contract_types.py` 가 dataclass 필드를 대조한다.
감사 id(B·A·T)는 2026-08-23 계약 감사(이슈 #17)의 항목 번호다.

```python
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
```

## 수식 (구현이 이 정의를 따른다)

- **population_share_pct** (`metrics_need`) = `100 * (low_mentioning / denom_low) * site_low_pct`
  - `low_mentioning` = 그 카테고리의 저평점 전수(`low_complete`) 제품에서 이 need_key 를 언급한 ≤2★ 리뷰 수
  - `denom_low` = 같은 제품 집합의 `low_collected` 합 · `denom_site` = 같은 집합의 `site_review_count` 합
  - `site_low_pct` = 제품 단위로 `(review_stats.pct_1 + review_stats.pct_2) / 100` (사이트가 보고한
    저평점 비율). `metrics_need` 의 행은 제품이 아니라 카테고리이므로 그 집계는 제품들의 단순 평균이
    아니라 **리뷰수 가중 평균**이다: `Σ site_low_est / denom_site`, 합의 범위는 위 두 분모와 같은
    `low_complete` 제품 집합이고 `site_low_est` = `round(site_review_count × site_low_pct)` 가 그
    제품 단위 값이다 (`product_denominator.site_low_est`). 분자는 그 집합이 사이트에 가진 ≤2★ 리뷰의
    추정 총수이고, 제품 하나짜리 집합에서는 제품 단위 정의로 그대로 되돌아간다.
  - `low_share` = `low_mentioning / denom_low` (저평점 표본 내 비율)
  - B7: 시드의 `seed:slice-p1` 행은 이 식이 아니라 수집 표본 근사(`100 * low_mentioning / denom_site`)로 계산된 값이다. 2차 패스 목표는 두 값의 차 ±0.05 이고 골든이 아니다.
- 분기 입자의 수식은 전부 **패널**을 분모로 쓴다 (`metrics_topic_quarter`, formats.md §패널 명부 CSV).
  모집단은 행 안에 있다: `panel_version`(어느 명부인지) · `panel_role`(그 명부의 어느 모집단인지) ·
  `denom_channels`(그 분기에 실제로 산출에 든 채널 수) · `documents` · `quarter_mentions`. 분모는 **장문
  영상만**이다(`content_type='long_form'`): 쇼츠는 설명란이 비어 매칭률이 24%(장문 64%)인데 그 비중이
  분기마다 55%~41%로 움직여, 한 분모에 넣으면 포맷 선택 변화가 주제 트렌드로 위장된다. 영상 설명과 댓글도
  합치지 않고 `source` 로 나란히 낸다 — 둘은 다른 것을 잰다(설명은 스펙·포뮬러, 댓글은 사용감·불만).
  `content_type` 이 키 안에 있으므로 `short_form` 행도 합법이다 — 두 포맷이 한 분모를 다투지 않고 각자의
  `quarter_mentions`·`denom_channels` 를 갖는다. v1(ydc)은 `long_form` 행만 낸다.
- **분기 문서 모집단** — 위 다섯 칸이 무엇을 센 것인지다. 그 `panel_version` 명부의 활성 행 중 그
  `panel_role` 인 채널이 올린 영상 가운데 ① 길이가 있고 60초를 넘는 것(길이가 없는 영상 — 라이브 등 — 은
  쇼츠와 같이 빠진다) ② 정규화한 **제목+설명**에 `scope` 카테고리의 사전어가 **부분문자열로** 걸리는 것
  (ydc: `선크림` 주제의 별칭 목록) ③ 관측 월이 있는 것. 셋을 다 통과한 영상만 남고, 같은 영상이 여러 run
  에 있어도 한 번만 센다. **`documents` 는 패널의 전체 영상 수가 아니다 — 카테고리로 잘린 뒤의 수다.**
  전체 패널 영상 위에서 계산하면 이 표의 모든 비율이 오류 없이 달라진다.
  - `source='youtube_video'`: 문서 하나 = 영상 하나(제목+설명)이고 `documents` 는 그 분기의 그 영상 수다.
  - `source='youtube_comment'`: 문서 하나 = 그 영상들에 달린 댓글 하나. `documents` 는 **비어 있지 않고, 한
    영상 안에서 정규화 후 같은 것을 하나로 접은** 댓글 수다(영상 간 중복은 접지 않는다 — 다른 영상에 달린
    같은 말은 각각 실제 반응이다). 분기는 댓글 시각이 아니라 **부모 영상의 분기**다: 3년 전 영상에 어제
    댓글이 달리므로 댓글 시각으로 분기를 만들면 분모가 정의되지 않는다.
  - `denom_channels` 는 두 `source` 에서 **같다** — 그 분기에 위 세 조건을 통과한 영상을 낸 채널의 수다
    (댓글은 채널이 아니라 영상에 달린다).
- **분기 표의 행 집합** — 한 (`run_id`, `scope`, `source`, `content_type`, `panel_version`, `panel_role`)
  안에서 이 표는 **조밀한 격자**다: `trend_use=true` 인 주제(현재 13개) × 그 산출에 존재하는 분기 전부에
  행이 하나씩 있고, 언급이 0인 칸도 행이 된다(`mentions=0` · `composition=0` · `unique_ratio=1` ·
  `sample_ok=false`). `trend_use=false` 인 주제(`추천_재구매`·`선크림` — 각각 영상의 76%·93%를 쳐서
  판별력이 없다)는 필터·장르 표시로만 쓰이고 이 표에 행을 갖지 않는다. 그래서 두 불변식이 참이고, 뷰
  `needs.metrics_topic_quarter_violation`(`db/views/`)이 저장된 행에 대고 그것을 되묻는다 — 비어 있으면 참이다.
  1. 격자가 조밀하다: `count(*) = count(distinct topic_key) * count(distinct quarter)`.
  2. 분모가 닫힌다: 한 분기의 `sum(mentions)` 가 그 분기 행들이 다 같이 들고 있는 `quarter_mentions` 다.
  저장된 표에 `SUM(mentions) GROUP BY quarter` 를 돌리는 사람이 맞으려면 그 둘이 서 있어야 한다. 언급 0 셀을
  지우면 첫째가 깨지고 `persistence` 의 기준선이 함께 올라간다.
- **composition** (`metrics_topic_quarter`) = `mentions / quarter_mentions` — 문서 기준 share 가 아니라
  **주제 간 구성비**다. 유튜버 설명란 길이 중앙값이 3년간 1,253자 → 709자로 줄어, share 는 분자만 줄고
  분모는 그대로여서 13개 주제 중 10개가 동반 하락한다(합계 -28.6%p). 구성비는 분자·분모가 같이 줄어 상쇄된다.
  `quarter_mentions` 가 0인 분기에서는 NULL 이 아니라 `0` 이다.
- **velocity_yoy** (`metrics_topic_quarter`) = `ln(composition[q]) - ln(composition[전년 동분기])`. 조건은
  셋이다: 전년 동분기가 그 산출에 **존재하는 분기**여야 하고(없으면 비교 상대가 없다), **양쪽 분기 모두
  `mentions >= 5`** 여야 한다. 하나라도 아니면 NULL — 표본 부족을 급등으로 읽지 않는다. 전년 동분기인 것은
  계절성 때문이다 (formats.md §시간).
- **persistence** (`metrics_topic_quarter`) = 창 안에서 `composition` 이 그 주제의 전 기간 중앙값을 넘은
  분기의 비율. 창은 **그 행의 분기에서 끝나는, 존재하는 분기 최대 4개**이고(전역 최신 4분기가 아니다)
  `window_quarters` 가 그 길이다. 기준선의 "전 기간"은 그 산출에 존재하는 분기 전부이고 **언급 0 분기도
  든다**. 기준선과 창은 `source` 별로 따로 잡는다. 그래서 이 값은 **run 상대**다 — 분기가 더 붙은 다음
  run 은 같은 분기에 다른 값을 정당하게 내고, `run_id` 가 키에 있어 둘 다 남는다. 판정 규칙이 개수 단위로
  쓰여 있어 `persist_quarters`·`window_quarters` 로 개수도 남긴다 — 창이 짧은 초기 분기에서는 비율만으로
  개수를 복원할 수 없다.
- **unique_ratio** (`metrics_topic_quarter`) = `mentions / 중복 포함 언급 수`. 한 영상 안에서 정규화 후 같은
  댓글은 한 번만 세고(복붙 스팸, 실측 1.1%), 영상 간 중복은 지우지 않는다 — 다른 영상에 달린 같은 말은
  각각 실제 반응이다. 중복 포함 언급 수가 0인 칸에서는 NULL 이 아니라 `1` 이다.
- **sample_ok** — `mentions >= 5`. `velocity_yoy` 를 내는 조건과 같은 수이고, 022 의 CHECK 이 그 등식을
  강제한다. 이 칸은 NOT NULL 이라 정의가 없으면 행이 자기 이름과 다른 것을 말한다.
- **channel_diffusion** (`metrics_topic_quarter`) =
  `0.5 * (그 주제를 낸 패널 채널 수 / denom_channels) + 0.5 * 정규화 섀넌 엔트로피(채널별 언급 분포)`.
  두 항 다 **영상에서 나온 채널 분포**를 쓴다 — 그 분기 그 주제가 걸린 영상을 채널마다 몇 편씩 냈는지의
  분포다. 그래서 이 컬럼은 `source` 에 의존하지 않고, 같은 (주제, 분기)의 `youtube_comment` 행은
  `youtube_video` 행과 **같은 값**을 갖는다. 엔트로피의 정규화 분모는 `ln(그 분포에 든 채널 수)` 이지
  `denom_channels` 가 아니다 — 한 채널이 독점하면 0, 그 채널들에 고르게 퍼지면 1이다. 그 분기에 패널 영상이
  하나도 없으면 첫 항은 0이다. 옆 칸 `channel_count` 는 **다른 수**다: 그 행의 `source` 에서 그 주제를 낸
  채널 수이고(`youtube_comment` 행에서는 그 주제의 댓글이 달린 영상들의 채널 수), 이름이 비슷하다고 첫 항의
  분자로 쓰면 댓글 행의 확산도가 달라진다.
- **저장 자리수** (`metrics_topic_quarter` 의 비율 칸) — 아래 자리수로 반올림해 저장한다. 판정 임계값(ydc
  `judge.py` 의 `TAU`·`DIFFUSION_TAU`)이 반올림된 값 위에서 맞춰진 수라, 자리수가 곧 그 게이트의 해상도다.
  022 가 `numeric(p,s)` 로 그 자리수를 들고 있어, 저장이 자리수를 지키는 것은 DDL 이 강제한다.
  자리수: `composition` 5 · `velocity_yoy` 4 · `persistence` 3 · `unique_ratio` 4 · `channel_diffusion` 3.
- **like_cap_sum** (`metrics_wish`) = `sum(min(like_count, LIKE_CAP))`, **LIKE_CAP = 100** (A8: 슬라이스에 cap 이 없어 상수를 계약이 정한다). 상한을 쓰지 않는 구현은 이 컬럼을 NULL 로 둔다.
- **low_complete** (`product_denominator`) = `(low_collected < 150) or has_3star` — RATING_ASC 표본 안에 3★ 이 섞였거나 ≤2★ 가 150 미만이면 ≤2★ 는 전수다. 150 은 수집 표본 상한(`REVIEW_PAGES 3 x 50`)이고 `collectors/commerce/scope.json`(#7)과 `formats.md` 가 같은 값을 갖는다.

## 판정 (트렌드 유형 7종과 두 점수 — `topic_quarter_judgement`, 포크 #40)

판정은 **집계가 아니라 파생**이다. 입력이 문서가 아니라 한 run 의 `metrics_topic_quarter` 행 전부이고,
산출은 그 행과 **1:1**(같은 여덟 칸이 키이자 FK)이다. 그래서 이 표는 `formats.md` §시간 의 "집계 그레인의
정본" 에 줄을 갖지 않는다 — 그 표가 닫는 질문은 "이 그레인의 **언급 수·채널 수·지속성**을 어디에 묻는가"
이고, 판정 표는 그 셋을 하나도 들지 않는다. 세는 칸이 없으므로 정본을 다툴 상대가 없다. 이름이
`metrics_` 로 시작하지 않는 것도 같은 문장이다.

판정은 §분기 표의 행 집합 의 **조밀한 격자를 전제한다**: `신규 등장` 은 직전 3분기를, `사라짐` 은 전 기간
최고 분기를, `채널 확산` 은 전년 동분기를 그 주제의 이력에서 꺼낸다. 언급 0 칸이 행으로 남아 있지 않으면
그 조회가 빈칸을 만나고, 빈칸은 0 이 아니라 "모른다"라서 판정이 조용히 달라진다.

- **evidence_strength** = `W_EVIDENCE.documents * min(1, 근거 수 백분위)` + `W_EVIDENCE.channels *
  min(1, channel_count / denom_channels)` + `W_EVIDENCE.unique * min(1, unique_ratio)`, 0~100.
  - `근거 수 백분위` 는 **그 source 안에서** `mentions` 가 놓인 위치(0~1)다. 같은 값이 여럿이면 그 구간의
    중간을 준다. 절대 기준을 하나 쓰지 않는 것은 소스별 스케일이 다르기 때문이고(이 코퍼스 실측: 영상
    중앙 16 · 댓글 중앙 62), 그래서 이 항은 **행 하나가 아니라 그 source 의 행 집합 전체**에 의존한다 —
    판정이 run 단위 파생인 두 번째 이유다.
  - **채널 항은 `channel_count / denom_channels` 다.** 이것은 `channel_diffusion` 이 쓰는 두 채널 비율과
    **또 다른 세 번째** 비율이다. 셋이 섞이면 오류 없이 다른 수가 나오므로 여기서 갈라 적는다:
    | 어디 | 분자 | 분모 | source 의존 |
    |---|---|---|---|
    | `evidence_strength` 채널 항 | `channel_count` (그 행의 source 에서 그 주제를 낸 채널 수) | `denom_channels` | **있다** (댓글 행과 영상 행이 다른 값) |
    | `channel_diffusion` 첫 항(넓이) | 그 주제를 낸 **영상** 채널 수 | `denom_channels` | 없다 (두 행이 같은 값) |
    | `channel_diffusion` 둘째 항(고름) | 채널별 **영상** 언급 분포의 섀넌 엔트로피 | `ln(그 분포에 든 채널 수)` | 없다 (두 행이 같은 값) |
    `youtube_video` 행에서는 첫 두 비율이 우연히 같은 수이고, 그래서 **영상만 보고 있으면 이 차이가
    보이지 않는다.** 갈리는 것은 댓글 행이다.
  - `unique_ratio` 항은 이 코퍼스에서 사실상 상수다(중앙 1.0, 최저 0.9939) — 25점이 모든 셀에 같이
    들어간다. 항을 빼지 않는 것은 재게시가 많은 소스(NAVER·커머스)가 붙으면 변별력이 생기기 때문이고,
    지금은 **정보가 없다는 사실**이 산출물에 남는다.
  - 저장 전 소수 **1자리**로 반올림한다. `EVIDENCE_FLOOR` 비교도 `opportunity_score` 의 항도 그 반올림된
    값을 쓴다 — 자리수가 곧 그 게이트의 해상도다(§수식 "저장 자리수" 와 같은 문장).
- **판정 순서** — 위에서 먼저 걸리면 종료한다. 순서 자체가 정의다.
  1. 그 source 의 **마지막 분기**면 `미확정(진행 중)`. 진행 중이라 문서 수가 덜 찼다.
  2. `evidence_strength < EVIDENCE_FLOOR` 또는 `mentions < MIN_DOCUMENTS` 면 `근거 부족`.
  3. 직전 3분기가 **존재하고** 그 셋의 `composition` 이 모두 `NEW_TOPIC_MAX_SHARE` 미만이고
     `mentions >= MIN_DOCUMENTS` 이고 `channel_count >= 2` 면 `신규 등장`.
  4. `velocity_yoy` 가 NULL 이면 `판정 보류`(비교 상대가 없다). **3 보다 뒤인 것이 뜻이다** — 새로 나타난
     주제는 전년 동분기 표본이 없는 것이 정상이라, 그 셀을 보류로 흘리면 `신규 등장` 이 서지 않는다.
  5. `velocity_yoy > TAU` 면 `persist_quarters == 1` 일 때 `단기 피크`, 아니면 `급상승`.
  6. 전년 동분기 행이 있고 `channel_diffusion - 전년 동분기 channel_diffusion > DIFFUSION_TAU` 이고
     `velocity_yoy <= TAU` 면 `채널 확산`.
  7. `abs(velocity_yoy) <= TAU` 이고 `persist_quarters >= 3` 이면 `지속 인기`.
  8. `velocity_yoy < -TAU` 이고 `composition < (그 주제의 전 기간 최고 composition) / 2` 면 `사라짐`.
  9. 어디에도 안 걸리면 `판정 보류`.
- **유형 어휘는 아홉이고 그중 일곱이 "유형"이다.** 나머지 둘(`판정 보류` · `미확정(진행 중)`)은
  유형이 아니라 판정하지 않았다는 말이다. `judged` = 일곱에서 `근거 부족` 을 뺀 여섯에 들었는가.
  유형 일곱: `급상승` `사라짐` `지속 인기` `단기 피크` `신규 등장` `채널 확산` `근거 부족`
- **hold_reason** — `판정 보류` 가 나온 이유. 빈칸으로 두면 규칙의 구멍이 안 보인다. 닫힌 어휘 넷이다:
  `no_prior_year`(전년 동분기 표본 부족, 순서 4) · `above_half_peak`(`velocity < -TAU` 인데 구성비가
  최고 분기의 절반 이상이라 `사라짐` 에 못 든다) · `within_tau_short_persistence`(변화가 TAU 이내인데
  `persist_quarters < 3`) · `no_rule`(규칙 미해당). 보류가 아닌 행은 `''` 다. ydc 는 이 사유를 사람이 읽는
  한 문장으로 적고 `above_half_peak` 에는 최고 분기 구성비를 끼워 넣는데, 그 수는 같은 run 의
  `metrics_topic_quarter` 에서 다시 나오는 파생이라 여기 저장하지 않는다.
  - 이 컬럼이 실제로 규칙의 구멍 하나를 드러냈다: 이 코퍼스에서 가장 큰 하락(`톤업_메이크업베이스` 댓글,
    `velocity_yoy = -0.56`)이 `above_half_peak` 으로 떨어진다. `사라짐` 이 두 조건을 **함께** 요구하기
    때문이다. 유형을 늘리는 것은 팀 합의 사항이라 규칙은 그대로 두고 사유만 남긴다.
- **opportunity_score** = 네 항을 0~1 로 맞춰 가중합한 뒤 **그 source 안에서** 0~100 으로 min-max 정규화.
  소수 1자리. 점수를 매기는 집합(`scored`)은 그 source 의 셀 중 `velocity_yoy` 가 NULL 이 아니고, 마지막
  분기가 아니고, `trend_type` 이 `근거 부족`·`판정 보류` 가 아닌 것이다. 그 밖의 셀은 NULL —
  **0 이 아니다.** 0 은 "가장 낮은 기회"이고 NULL 은 "점수를 매기지 않았다"다.
  `raw = W_SCORE.velocity * (velocity_yoy - min) / (max - min) + W_SCORE.persistence * persistence
  + W_SCORE.channel_diffusion * channel_diffusion + W_SCORE.evidence_strength * evidence_strength / 100`
  이고, `min`·`max` 는 `scored` 안의 `velocity_yoy` 범위다(폭이 0이면 1.0 으로 둔다). 그 `raw` 를 다시
  `scored` 안에서 min-max 정규화한 것이 저장값이다. **그래서 이 점수는 그 산출 안에서만 비교된다** —
  `persistence` 와 같은 뜻으로 run 상대이고, 다른 run 의 점수와 크기를 비교하면 틀린다.
  `judged` 인데 점수가 NULL 인 셀이 있을 수 있다(전량 실측 2셀): `신규 등장` 은 순서 4보다 앞이라
  `velocity_yoy` 가 NULL 인 채로 판정된다.
- **gap_pp** = `100 * (youtube_comment 의 composition - youtube_video 의 composition)`, 소수 2자리.
  (주제, 분기) 단위 사실이라 **두 source 행이 같은 값을 든다.** 한쪽 source 에 그 (주제, 분기) 행이 없으면
  NULL 이다. 0.6:0.4 같은 가중합으로 두 계열을 섞지 않는 이유가 이 칸이다 — 갭 자체가 신호다(`백탁` 은
  영상 0/13분기 대 댓글 12/13분기이고, 섞으면 그 공백이 사라진다).
- **single_source** — 이 판정이 소스 하나만 보고 내려졌는가. v1 은 **언제나 true** 다. TEAM_DECISIONS_v0.2
  §3.2 의 `근거 부족` 조건 셋 중 `source_count < 2` 를 **적용하지 않기** 때문이고, YouTube 안에서 영상과
  댓글은 상호 검증 소스가 아니라 성격이 다른 두 계열(설명은 스펙·포뮬러, 댓글은 사용감·불만)이라서다.
  값이 언제나 같은 칸을 두는 것은 그 게이트가 **꺼져 있다는 사실**이 행에서 읽혀야 하기 때문이다 —
  NAVER·커머스가 붙어 이 칸이 false 가 되는 날 그 조건이 켜진다.

### 판정 상수 (`analysis/judge` 한 곳에 모여 있고 `tests/test_judge_constants.py` 가 이 표와 대조한다)
#3 등급 A 리뷰가 "저장된 값 위에서 맞춰진 산물" 이라고 넘긴 다섯이다. 아래는 **그 값이 무엇 위에서
나왔는가**와 **그대로 채택하는가 다시 맞추는가**의 답이고, 재현은 2026-08-26 포크 #40 이 원 산출
(`reports/trend_sunscreen_v0.2.csv` 338행 = 이 표의 338행, #5 가 셀 차이 0 으로 대조)에서 했다.

| 상수 | 값 | 무엇 위에서 나왔나 (재현 결과) | 판단 |
|---|---|---|---|
| `TAU` | `0.35` | 관측 `abs(velocity_yoy)` 분포의 75분위. **소스별로 따로 뽑아 거의 같은 값이 나온 것이 근거다** — 재현: 영상 76셀 중앙 0.215 · 75분위 **0.366** · 90분위 0.594 · 최대 0.887, 댓글 108셀 중앙 0.218 · 75분위 **0.357** · 90분위 0.526 · 최대 1.290. **분위는 이 표 전체가 아래 `DIFFUSION_TAU` 줄과 같은 `sorted(v)[int(q*n)]` 정의를 쓴다** — `statistics.median` 으로 세면 중앙이 0.207/0.216 이고, 두 수는 정의가 다른 두 답이지 반올림 차가 아니다(포크 #41 이 정정). 댓글 90분위는 TEAM_DECISIONS_v0.2 §3.1.1 이 0.525 로 적은 그 수다(실측 0.5257 — 표는 버림, 여기는 반올림이고 컷에는 닿지 않는다). 나머지는 그 표와 같은 수 | **그대로 채택.** 다시 맞춰도 0.357~0.366 으로 돌아오고, 둘을 하나로 내려 고정한 것이 팀 결정이다. **한 번만 뽑아 고정하는 것**이 이 값의 요점이다 — 매 산출마다 다시 뽑으면 조용한 분기에도 언제나 상위 25%가 `급상승` 이 된다 |
| `DIFFUSION_TAU` | `0.089` | 전년 동분기 행이 존재하는 **234셀**(13주제 × 9분기 × 2소스)의 `abs(Δchannel_diffusion)` 75분위. 재현: n=**234** · 중앙 **0.042** · 75분위 **0.089** · 90분위 **0.496** — `judge.py` 주석의 세 수와 자리까지 일치. 분위는 `sorted(v)[int(q*n)]` 로 뽑는다 | **그대로 채택.** 0 으로 두면(= "오르기만 하면 확산") 판정된 89셀 중 **52셀(58%)** 이 `채널 확산` 한 곳으로 몰려 분류의 정보량이 사라진다. 이 컷에서 52 → **14셀**(재현 일치). 소스가 늘면 그 소스의 분포에서 다시 뽑아야 한다 |
| `EVIDENCE_FLOOR` | `50.0` | **적합된 값이 아니다.** v1 에서 온 0~100 척도의 중간이고 TEAM_DECISIONS 는 값만 적는다. 이 코퍼스에서의 실측 결과: `evidence_strength` 중앙 59.95(`statistics.median`; 위 `TAU` 줄의 `sorted(v)[int(q*n)]` 정의로는 60.1 — 포크 #41 이 정정), 338셀 중 111셀(33%)이 `근거 부족` 이고 그중 **51셀은 이 컷 하나로만** 걸린다(`mentions < 5` 로만 걸리는 셀은 **0**). 감도: 40 → 73셀 · 50 → 111셀 · 60 → 156셀 | **재적합하지 않고 채택한다 — 맞출 정답이 없기 때문이다.** `backtest.csv` 11행은 이미 **판정된** 셀의 적중만 보고(`trend_type` 은 급상승·신규 등장·사라짐·단기 피크뿐) `근거 부족` 판정에 대해서는 아무 말도 하지 않는다. 근거가 "팀 합의" 하나뿐이라는 사실을 여기 적는 것이 이 줄의 일이다 |
| `MIN_DOCUMENTS` | `5` | `metrics_topic_quarter.sample_ok` 의 게이트와 **같은 수**(022 의 `CHECK (sample_ok = (mentions >= 5))`, §수식 의 `velocity_yoy` 조건) | 채택하되 **따로 정의하지 않는다** — `analysis.trend.MIN_MENTIONS` 를 그대로 든다. 실측상 이 게이트가 단독으로 거르는 셀은 0이라 `EVIDENCE_FLOOR` 에 완전히 가려져 있다 |
| `NEW_TOPIC_MAX_SHARE` | `0.01` | TEAM_DECISIONS §3.2 의 "직전 3분기 구성비 < 1%". 적합값이 아니라 읽기 좋은 팀 합의 수다. 감도: 0.005 → 1셀 · 0.01 → 5셀 · 0.02 → 10셀 | 채택. 근거가 합의뿐이라는 것을 적는다 |
| `W_EVIDENCE` | `documents 43.75` · `channels 31.25` · `unique 25.0` | v1 의 4요소(근거 수 35 · 채널 25 · 비중복 20 · 제품·주제 매칭 신뢰도 20)에서 **`entity_link` 가 없어 계산할 수 없는 넷째를 빼고 남은 셋을 0.8 로 나눈 재정규화**다(35/.8 · 25/.8 · 20/.8). 산술이 곧 근거이고 테스트가 그 나눗셈을 검사한다 | 채택. 넷째 항을 0으로 깔면 모든 주제가 조용히 20점 깎여 `EVIDENCE_FLOOR` 가 오작동한다. `entity_link` 가 생기면 4요소 원안으로 되돌아간다 |
| `W_SCORE` | `velocity .35` · `persistence .25` · `channel_diffusion .20` · `evidence_strength .20` | **이 코퍼스에서 적합된 값이 아니다.** TEAM_DECISIONS_v0.2 §1 "v1에서 그대로 채택하는 것" 목록에 있는 v1 합의값이다 | 채택. 근거가 "v1 합의" 하나뿐임을 적는다 — 넷의 합이 1.0 이라는 것 말고는 이 값을 지지하는 실측이 없다 |

- 위 표를 **값만** 옮기는 것은 이 계약의 실패다. 각 줄의 3열이 없으면 "왜 0.35 인가"에 답할 자리가 없다.
- 판정 상수가 바뀌면 그것은 정의가 바뀐 것이므로 `analysis_run.versions.judgement` 를 올린다
  (`versioning.md`). ydc 는 같은 사실을 행마다 `tau`·`diffusion_tau` 컬럼으로 적는데, 이 레포는 A19 에
  따라 집계·파생 표에 `*_version` 컬럼을 두지 않으므로 그 자리가 run 이다.
- **저장 자리수** (`topic_quarter_judgement`) — 024 가 `numeric(p,s)` 로 그 자리수를 들고 있어, 저장이
  자리수를 지키는 것은 DDL 이 강제한다.
  판정 자리수: `evidence_strength` 1 · `opportunity_score` 1 · `gap_pp` 2

## 민감도 (결론이 흔들리는가 — 저장하지 않는 세 답, 포크 #41)

셋(`panel_sensitivity` · `backtest` · `spam_ad_flags`)은 **지표를 만들지 않는다.** §수식 과 §판정 을 모집단만
바꿔 다시 돌려, 그 run 의 결론이 그 선택에 흔들리는지를 잰다. 그래서 이 셋은 §판정 **뒤에** 온다 — 흔들릴
결론이 먼저 있어야 한다.

**어느 표에도 쓰지 않는다.** 이유는 규율이 아니라 어휘다: 세 측정이 만드는 행은 반사실 모집단의 것이고,
`panel_role='product+expert'` 도 "광고 영상을 뺀 산출"도 "2025Q2 까지만 아는 산출"도 022 의 닫힌 어휘
(`panel_role IN ('product','expert')`)와 `analysis_run` 에 자리가 없다. 자리를 만드는 것은 추가만의 범위가
아니라 저장된 비율의 뜻을 바꾸는 일이므로, 산출은 표가 아니라 **답**이다(`cosmai trend sensitivity` 의
stdout). 쓰지 않는 덕에 이 명령은 운영 DB 에 그대로 돌아간다.

**기저는 다시 센다.** 저장된 `metrics_topic_quarter` 행을 그대로 쓰지 않는 것은, 차이가 뜻을 가지려면 기저와
변형이 **같은 코드 경로**에서 나와야 하기 때문이다. 대신 다시 센 기저가 저장된 행과 같은지 되묻고, 다르면
그 사실(`baseline_drift`)이 먼저 나온다 — 그때 이 명령의 모든 차이는 뜻이 없다.

**두 창은 유도한다.** `최근 4분기` = 마지막(진행 중) 분기 **앞**의 네 달력 분기, `직전 4분기` = 그 앞의 네
달력 분기. ydc 는 이 여덟을 값으로 박아 뒀는데(`RECENT` = 2025Q3~2026Q2 · `PRIOR` = 2024Q3~2025Q2) 그것은 이
코퍼스에 묶인 상수라, 여기서는 같은 문장을 유도한다(2026-08-19 코퍼스에서 같은 여덟 분기다). **관측 목록의
인덱스가 아니라 달력으로 세는 것**이 뜻이다 — 언급이 없어 행이 빠진 분기도 창의 한 칸을 차지해 0 으로 들어가야
한다.

### 패널 민감도 (`PanelSensitivityRow` — ydc `panel_sensitivity.py`)
- 묻는 것: **패널 구성이 결론을 바꾸는가.** product 34채널만인 산출과 43채널 전부(product+expert)인 산출을
  나란히 돌려 두 델타(최근 4분기 구성비 − 직전 4분기 구성비)를 비교한다.
- 개별 채널을 재분류하는 대신 선택 자체를 재는 것은, ydc 가 재분류를 시도해 **텍스트 지표로는 두 집단이
  구분되지 않는다**는 것을 실측했기 때문이다(팀이 직접 분류한 20채널에서 성분·스펙 주제 비중 expert 중앙
  17.7% 대 product 15.3%, 최고값 39.7%가 product 인 채널이다). 기획안 §4 의 "결론이 필터 조건에 따라 크게
  달라지면 필터 민감 신호로 표시한다"가 요구하는 검사가 이것이다.
- `sample_ok` = 그 주제의 **충족 분기**(언급이 §수식 의 표본 게이트 5를 넘는 분기)가 관측 분기의 **절반을
  넘는가**. ydc 는 13분기 산출에서 이 문장을 `>= 7` 로 박아 뒀고, 여기서는 관측 분기 수에서 유도한다.
  충족 분기가 절반 미만인 셀은 애초에 판정 대상이 아니라 뒤집힘을 세지 않는다.
- **뒤집힘** = 판정 대상 셀 중 두 델타의 부호가 다르고 한쪽이라도 `MATERIAL_PP = 0.5`%p 만큼 움직인 것.
  폭을 요구하지 않으면 0 근처를 오가는 셀이 전부 뒤집힘으로 잡힌다 — 전량에서 실제로 한 셀
  (`youtube_comment` / `백탁`, −0.03 → +0.03)이 그렇게 잡힌다. 0.5 는 관측된 3년 변화량 범위(−5.5 ~ +2.6%p)에서
  눈에 보이는 최소 폭이다.

### 후향 검증 (`BacktestRow` — ydc `backtest.py`)
- 묻는 것: 판정이 "지나고 보니 그랬다"가 아니라 **"그때 알 수 있었다"인가.** 과거 분기 C 까지만 알던 것처럼
  지표를 다시 세어 직전 분기 T 를 판정하고, C 이후 `HORIZON` 분기에 그 방향이 유지됐는지 본다.
- **T 가 아니라 C 로 자른다.** 판정은 마지막 분기를 `미확정(진행 중)` 으로 두므로(§판정 순서 1), T 를
  판정하려면 C = T 다음 분기까지 데이터가 있어야 한다. 운영도 그렇게 돈다.
- **자르는 것이 핵심이다.** `persistence` 의 기준선이 전 기간 중앙값이라(§수식), 자르지 않고 과거를 판정하면
  아직 오지 않은 분기를 보고 기준선을 정한 셈이 된다. `velocity_yoy` 는 전년 동분기만 쓰므로 누출이 없다.
- 방향이 있는 유형만 검증한다: `급상승`·`신규 등장`(상승 유지) · `사라짐`(하락 유지) · `단기 피크`(피크 소멸).
  `지속 인기`·`채널 확산` 은 방향 예측이 아니라 상태 서술이라 뺀다 — 넣으면 적중률이 부풀려진다.
- **기준을 두 개 낸다.** 기준 A 의 직전 구간에는 급상승한 분기 T 자체가 들어 있어 "T 보다 더 올라야 적중"이
  되고, 평균 회귀만으로 실패가 나온다. 기준 B(`before_excl_pp`)는 T 를 뺀 직전 구간과 비교해 **"올라간 수준이
  유지됐는가"**를 묻는다. 두 질문이 다르고, 둘 중 하나만 내면 결과를 고른 것이 된다. `단기 피크` 는 두 기준이
  같은 질문(T 분기보다 낮아졌는가)이 된다.
- **기저율을 같이 낸다.** 판정과 무관하게 전체 셀 중 몇 %가 올랐는지를 함께 계산한다(직전·이후가 둘 다 0인
  셀은 세지 않는다). 적중률이 기저율보다 높지 않으면 그 판정에는 정보가 없다 — 적중률만 내는 후향 검증은
  검증이 아니라 홍보다.
- 구간을 1년(`HORIZON` = `LOOKBACK` = 4)으로 둔 것은 계절성이다: 직전·이후 둘 다 네 분기를 다 담아야 여름 효과가
  상쇄된다. §수식 의 `persistence` 창과 같은 4다.

### 광고·협찬 표시 (`AdSensitivityRow` — ydc `spam_ad_flags.py`)
- 묻는 것: 기획안 §4 가 요구하는 두 가지 — 광고·협찬을 **표시**하고, 빼도 결론이 같은지 **확인**한다. 앞의
  것만 하면 표시해 놓고 아무도 안 읽는 컬럼이 된다.
- 표시하는 것 셋(어휘는 `variant` 의 닫힌 넷: `ad_video` · `creator_comment` · `promo_comment` · `all_flagged`):
  | 표식 | 무엇 | 어디서 |
  |---|---|---|
  | `ad_video` | 광고·협찬 영상 | `source_metadata.has_paid_product_placement`(유튜버 자체 신고)와 설명란 문구의 **합집합**. 신고는 누락이 있어(TEAM_DECISIONS §9) 전량 실측 신고 254편 · 문구 **407**편 · 합집합 465편 · 겹침 196편이고, **문구로만 잡히는 211편**은 신고 필드로는 보이지 않는다. 출처의 docstring 은 문구 410 · 문구만 214 라고 적는데 그 수는 **어느 상태에서도 재현되지 않는다** — 포크 #41 이 그 파일의 birth 커밋(`9fd7ec0`, 그 뒤로 `AD_RE` 도 모집단 정의도 안 바뀌었다)을 같은 두 run 에 돌려 407 을 다시 받았다. 함께 적힌 겹침 196 은 407 과 맞는 수다(254+407−465). 낡은 것은 410/214 다 |
  | `creator_comment` | 채널 운영자 본인 댓글 | `source_metadata.author_channel_hash` = `sha256("youtube:" + channel_id)[:24]` 라 채널 id 로 되만들 수 있다. 추정이 아니라 정확한 매칭이다. 운영자 고정 댓글은 설명란을 옮긴 것에 가까워, 댓글 계열에 두면 **소비자 반응이라는 계열의 정의가 깨진다** |
  | `promo_comment` | 판매 링크·공동구매·마켓 공지 댓글 | 정규식. 운영자 댓글이 **먼저**라 문서 하나는 두 집합에 겹쳐 들지 않는다. **묶음 단위에서는 겹칠 수 있다** — 같은 (부모 영상, 텍스트) 에 운영자 사본과 남의 사본이 섞이면 그 묶음이 두 표식을 다 든다(ydc 와 같은 동작이고, 그래서 `all_flagged` 의 제외 집합은 두 집합의 합보다 작을 수 있다) |
- **버린 규칙**: 전화번호 정규식(6건)과 도박·대출 사전(4건)은 재보니 걸린 것이 거의 전부 오검출이었다
  (`토토톡` · `40대출산맘` · `무향`). 0.01%를 잡으려고 오검출을 남기지 않는다. 흔한 스팸 유형(도박·리딩방)이 이
  패널에는 없다.
- **제외는 (부모 영상, 정규화 텍스트) 묶음 단위다.** 복붙 한 쪽만 빼면 `unique_ratio` 의 분자와 분모가 다른
  모집단을 세게 된다(코퍼스 규칙 9의 `duplicate_in_parent` 가 그 묶음의 나머지다). 영상이 빠지면 그 영상의
  댓글도 함께 빠진다 — 분기 귀속이 부모 영상이기 때문이다(규칙 3).
- **사라진 셀과 뒤집힌 셀을 나눈다.** 표본이 줄어 판정이 사라진 것(`lost`)과 유형이 바뀐 것(`flipped_cells`)을
  섞으면 "제외하니 결론이 다 바뀐다"로 보이는데, 실은 대부분 표본 미달이다.

### 민감도 상수 (`analysis/sensitivity` 한 곳에 모여 있다)
| 상수 | 값 | 무엇 위에서 나왔나 | 판단 |
|---|---|---|---|
| `MATERIAL_PP` | `0.5` | 관측된 3년 변화량 범위(−5.5 ~ +2.6%p)에서 눈에 보이는 최소 폭. 적합값이 아니라 읽기 기준이다 | 채택. 0 으로 두면 전량에서 `백탁` 댓글 셀(−0.03 → +0.03)이 뒤집힘으로 잡혀 답이 뒤집힌다 |
| `HORIZON` · `LOOKBACK` | `4` · `4` | 계절성. §수식 의 `persistence` 창(`WINDOW_QUARTERS`)과 같은 수이고, 따로 정의하지 않고 그것을 든다 | 채택. 4 미만이면 직전·이후 구간이 여름을 한쪽에만 담는다 |
| 판정 대상 게이트 | 관측 분기의 **절반 초과** | ydc 는 13분기 산출에서 `>= 7` 로 박아 뒀다. 같은 문장을 관측 분기 수에서 유도하면 13분기에서 7 이 나온다 | 유도로 채택. 값으로 박으면 분기가 늘어난 산출에서 조용히 헐거워진다 |
| `MIN_MENTIONS` | `5` | §수식 의 표본 게이트와 **같은 수**다 | 따로 정의하지 않는다 — `analysis.trend.MIN_MENTIONS` 를 그대로 든다 |

- **표본 골든이 못 보는 갈래 둘**(`sample_ok` 가 서는 셀 · 홍보 댓글)은 `tests/test_sensitivity_golden.py`
  가 "없다"고 **명시적으로 주장**해서 표본이 바뀌는 날 그 줄이 먼저 깨지게 해 뒀다. 밟게 만드는 것은 #57 이
  진다 — product 모집단을 건드리면 골든을 **다섯 벌**(#5 · #40 · #41 셋) 한 번에 재생성해야 하고, 그 비용이
  이 갈래들이 지금까지 미뤄진 이유다.
- **전량 대조는 CI 가 지킬 수 없다**(261,317문서는 `archive/` 에 있고 읽기 전용이다). 절차와 대조 코드는
  `tool/compare-ydc-sensitivity` 한 자리에 있다 — ydc 세 스크립트를 손대지 않고 돌려 산출본 셋을 만들고 행
  단위로 맞댄다. 2026-08-26 실행: 패널 26행 · 후향 11행 · 표시 104행, **차이 0**.

## 근거 (판정 셀을 받치는 소비자 발화 — `topic_quarter_evidence`, 포크 #6)

근거는 집계도 파생도 아니라 **포인터**다. 판정이 지표 행 하나에서 행 하나를 만든다면(§판정), 근거는 그
셀을 만든 문서 몇 개를 도로 가리킨다 — 본문을 베끼지 않는 것이 그 뜻이고, 셀에서 원문까지는 뷰
`needs.topic_quarter_evidence_quote` 가 잇는다. **판정 격자의 셀 하나에서 근거 원문까지 손으로 조인하지
않고 닿는다**는 이 이슈의 완료 기준이 그 뷰 한 줄이다.

**그 한 줄에는 `run_id` 가 든다.** 뷰는 run 을 가리지 않으므로 한 스냅샷·명부에 run 이 둘 이상 있으면 같은
셀이 겹쳐 나오고 `rank` 가 1..n 이 아니게 된다. 정본 필터는 둘 중 하나다 — `run_id` 를 명시하거나,
`analysis_run.note` 로 그 스냅샷·명부의 run 을 찾아 거는 것(`analysis/trend/pipeline.py` 의 `note_of`,
파이프라인 셋이 쓰는 바로 그 길)이다. 화면이 "최신 run" 을 원하면 그 note 로 고른 `run_id` 가 최신이다.

- **모집단은 지표·판정과 같다.** `analysis/evidence/pipeline.py` 는 모집단을 다시 적지 않고
  `analysis/trend/pipeline.py` 의 `POPULATION` CTE 를 그대로 든다. 다시 적으면 카드가 인용하는 발화와
  그 카드에 적힌 숫자의 분모가 갈리고, 갈린 것은 둘 다 그럴듯해서 보이지 않는다.
- **선별 규칙 넷** (ydc `evidence_comments.py` 의 규칙이고, 순서가 아니라 넷이 모두 걸린다):
  1. 후보는 그 셀의 `source` 가 낸 문서 중 `quality_flags = ''` 인 것이다. 빈 본문(`empty_text`)과 같은
     영상 안 복붙(`duplicate_in_parent`)은 인용하지 않는다 — 지표는 후자를 `unique_ratio` 의 분모에
     세지만(§수식) 그것은 세는 일이고 인용은 다른 일이다.
  2. **제작자 본인 댓글은 뺀다.** 그 영상 채널의 해시(`sha256('youtube:' || channel_id)` 앞 24자, 수집기가
     작성자 채널 ID 를 해시한 그 규칙)와 `source_metadata.author_channel_hash` 가 같으면 본인이다.
     좋아요 상위가 대부분 고정 댓글(타임라인·인사말·요약)이라 소비자 발화가 아니다. 픽스처의 후보 댓글
     147건 중 **14건**(후보 쌍으로는 281 중 32)이 여기서 빠진다.
  3. 주제는 `corpus_mention` 이 이미 단 것을 쓰고 본문을 다시 매칭하지 않는다. `matched_term` 도 그 행의
     값이다 — 다시 매칭하면 지표가 센 언급과 근거가 고른 언급이 다른 규칙 위에 서게 된다.
  4. 순서는 `like_count` 내림차순이고 **동점은 `doc_id`** 다. 2차 키가 계약인 것은 ydc 가 CSV 읽기 순서에
     기대고 있기 때문이다(파이썬 정렬은 안정적이라 동점의 승자를 파일 순서가 정한다). 저장되는 표는
     재실행이 같은 행을 내야 하므로 그 자리를 비워 둘 수 없다: 근거가 선 픽스처 46셀 중 **23셀**에 동점이
     있고(동점 = 그 셀 안에 좋아요가 **같은 후보가 둘 이상** 있는 셀이다. 후보가 둘 이상인 셀이 아니다),
     2차 키를 넣으면 102행 중 **24행**에서 고르는 문서가 달라진다. 달라지지 않는 것은 좋아요 사다리다.
  - 셀당 상한은 `TOP_PER_CELL = 3`. 카드 한 장에 들어가는 수라 025 의 CHECK 이 아니라 여기가 그 자리다 —
    DDL 은 추가만이라 한번 적은 상한을 되돌릴 수 없다. **그 수의 자리는 여기 하나여야 한다**: 카드가 인용
    상한을 따로 들면 근거만 늘려도 카드는 셋에 머문다. `analysis/cards.build` 의 기본값이 이 상수를
    import 하고, `tests/test_cards_rules.py` 가 두 이름이 같은 객체인지 본다.
- **이 절의 픽스처 수치는 재는 길이 있다.** `tool/measure-evidence-fixture` 가 코퍼스 CSV 에서 다시 재고
  (`--json`), `tests/test_evidence_numbers.py` 가 그 값과 이 문장들·`analysis/evidence/pipeline.py` 의 DB
  산출을 함께 맞댄다 — 픽스처가 자라면 테스트가 먼저 빨개진다. 숫자를 적고 재는 길을 안 남기면 그 숫자는
  조용히 거짓이 된다 (#41 의 `tool/compare-ydc-sensitivity` 와 같은 자리).
- **전량 실측** (2026-08-26 · 일회용 컨테이너 · 261,317문서 · 105,358언급): 후보 **15,602**행 ·
  근거 **480**행 · 셀 **163** · `topic_quarter_evidence_violation` **0행** · 두 번 돌려 같은 수(멱등).
  후보 질의는 **178ms**(`EXPLAIN (ANALYZE, BUFFERS)`, 023 의
  `corpus_document (snapshot_id, parent_item_id) WHERE content_type='comment'` 부분 인덱스를 탄다) 이고
  `cosmai trend evidence` 전체가 프로세스 기동까지 **0.52s** · 최대 상주 **73MB** 다. 본문을 싣지 않고
  포인터와 좋아요만 끌어오기 때문이고, 이 수가 없으면 "가볍다"는 잰 적 없는 단언이다.
- **축은 판정 격자다.** `trend_use = false` 인 주제(`선크림`·`추천_재구매`)는 판정 셀이 없으니 근거도 없다.
  ydc 는 15주제 139행을 냈고 그중 13주제 **102행**만 이 표에 자리가 있다.
- **좋아요는 행에 남긴다.** collected_at 시점의 스냅샷이라(§모집단의 한계) 나중에 다시 세면 다른 수가
  나온다 — 그때 이 정렬을 설명할 수 있는 것은 저장된 값뿐이다.

### `cosmai retrieval search` 로 대체하지 않는다 (그 답과 실측 — 포크 #11 이 기다린 답)
근거를 고르는 일은 검색처럼 보이지만 세 자리에서 어긋난다.
- **모집단**: `retrieval_chunk` 의 원천은 수집기 스키마(`tubedepth`·`trend_radar`, `analysis/retrieval/corpus.py`)
  이고 지표·판정의 원천은 `needs.corpus_*` 의 2026-08-19 관측 판본이다. 검색이 낸 댓글이 그 셀의 분모에
  든 문서라는 보장이 없고, 보장이 없는 근거는 근거가 아니다.
- **단위**: 검색의 답은 `chunk_id` 이고 근거의 단위는 문서다. 500자를 넘는 댓글은 조각으로 쪼개진다
  (픽스처의 모집단 댓글 418건 중 15건).
- **순서**: 검색은 질의와의 어휘 유사도로, 근거는 좋아요로 줄을 세운다. 그리고 "이 주제를 말한 문서"는
  `corpus_mention` 이 이미 답한 것이라, 검색을 다시 돌리는 것은 같은 질문에 **다른 규칙으로** 두 번째
  답을 만드는 일이다.

**이 표는 §검색 실측 과 같은 자가 아니다.** 축(질의 = 별칭 하나, 정답 = 주제 단위)은 `retrieval eval` 의
literal 모드와 같지만 코퍼스가 다르다 — 전 소스 381,950청크가 아니라 **그 판정 모집단의 댓글**뿐이다. 작은
코퍼스에서는 P@10 에 코퍼스가 정하는 천장이 생긴다: 정답이 3건인 주제는 어떤 엔진도 P@10 이 0.3 을 넘지
못한다. 그래서 아래는 언제나 천장을 함께 적는다. 천장 없이 옮겨 적으면 전 소스의 `.864` 와 나란히 놓여
"BM25 가 약하다"로 읽히는데, 그것은 이 표가 하는 말이 아니다.

실측 (2026-08-26 · 픽스처의 모집단 댓글 418건 = 청크 439개 · 질의는 주제 별칭 61개 · `analysis/retrieval/bm25`
의 색인과 `analysis/retrieval/eval` 의 채점, 정답은 `corpus_mention`. 재는 길은
`tool/measure-evidence-fixture` 이고 `tests/test_evidence_numbers.py` 가 이 표와 맞댄다):

| 무엇을 재는가 | 값 |
|---|---|
| BM25 top-10 이 `corpus_mention` 의 답과 겹치는 정도 | P@10 **.604** (이 코퍼스의 천장 **.892**) · MRR@10 .601 · Hit@10 65.6% |
| 고른 근거 102행이 그 주제 **어느 별칭의** top-10 안에라도 드는 비율 | **79/102 = 77.5%** |

둘째 줄이 이 절의 답이다 — 천장이 없는 수이고, 검색으로 갈아끼우면 근거 다섯 중 하나가 사라진다는 뜻이다.
그것도 별칭 합집합(주제마다 최대 10질의 × 10건)이라는 후한 조건에서이고, top-10 하나로 좁히면 더 내려간다.
첫째 줄은 그 둘째 줄이 엔진의 고장에서 온 것이 아님을 보이려고 있다: BM25 는 천장의 68% 를 가져왔고,
남은 몫은 "이 주제를 말한 문서"라는 물음에 이미 `corpus_mention` 이 다른 규칙으로 답해 두었기 때문이다.

**엔진 선택**: 이 용도는 셋 중 **하나도 쓰지 않는다.** 답을 이미 `corpus_mention` 이 갖고 있어서 고를
순위가 없다. S6 이 #11 에 주는 것은 기본 엔진의 근거가 아니라 그 반대다: **근거 수집이라는 구체적 용도가
검색의 순위를 필요로 하지 않는다.** 그래서 **위 두 줄은 #11 의 기본 엔진 판단에 입력으로 쓰지 않는다** —
그 판단의 입력은 전 소스에서 같은 자로 잰 §검색 실측 여섯 줄이다. 검색이 자기 자리를 갖는 것은 사전에 없는
말(`백탁` 이 아니라 `허옇게 떠요`)로 코퍼스를 뒤질 때이고, 그 자리는 heldout 모드이며 벡터가 유일하게 0 을
넘은 자리다(§검색 실측).

### 기회 카드 (유형은 규칙이 정한다 — `cosmai trend cards`, ydc `cards.py`)
- **카드 0건은 실패가 아니다.** 규칙이 다 돌고 나온 정상적으로 계산된 답이고, 이 표본에서도 11분기 중
  8분기가 0장이다. 종료 코드로 그것을 말하지 않는 이유와 그 대신 `partial(1)` 이 되는 하나(**규칙에
  걸렸는데 근거 원문이 없어 카드로 서지 못한 셀**)는 `entrypoints.md` §근거·카드 가 든다 — #41 이
  §민감도 에서 "흔들린다는 1 이 아니다"로 못 박은 그 자리와 같은 자리다.
- **카드는 행을 만들지 않는다.** 이미 저장된 세 표(`metrics_topic_quarter` · `topic_quarter_judgement` ·
  `topic_quarter_evidence`)를 읽어 렌더한 것이고, 표를 하나 더 두면 같은 수가 두 곳에 살아 어느 쪽이
  정본인지 다툰다 — ydc `cards.py` 의 설계 원칙 2("모든 수치는 이미 만든 산출물에서 그대로 가져온다")가
  저장에서는 이 문장이다. 파일로도 떨구지 않는다(`retrieval terms` 와 같은 자리): 자라는 코퍼스의
  스냅숏이라 레포에 두면 낡고, 남기려면 리다이렉트한다. 사람이 적는 칸(`accept / watch / reject`)이 있는
  것도 이 산출물이 표가 아닌 이유다 — 그 결정은 아직 이 레포의 어느 표에도 주인이 없다.
- **유형은 규칙이 배정한다. LLM 이 "이건 기회야"라고 판단하지 않는다** (설계 원칙 1). 요약 문장 자리에는
  근거 원문을 그대로 싣는다. **근거 원문이 없으면 카드로 만들지 않는다** (설계 원칙 3).
- 유형 어휘는 여섯이고 **넷만 설 수 있다.** 판정 순서와 같은 뜻으로 위에서 먼저 걸리면 끝난다.

  | 유형 | 규칙 | 이 레포에서 |
  |---|---|---|
  | 표현 공백 | 제품 전성분 비중 / 댓글 구성비 >= 5배 | **못 선다** — 전성분 축(ydc `ingredient_axis.py`)의 원천이 없다 |
  | 제품 공백 기회 | `gap_pp >= GAP_PRODUCT_GAP` 이고 댓글 셀이 판정된 셀 | 선다 |
  | 검증된 성장 | 어느 한쪽이 `급상승`·`단기 피크` 이고 `abs(gap_pp) < GAP_PRODUCT_GAP` | 선다 |
  | 단기 유행 위험 | 같은 조건에 갭이 그 이상 | 선다 |
  | 포화 시장 | 양쪽 다 `지속 인기`·`채널 확산` 이고 댓글 구성비 >= `SATURATED_COMPOSITION` | 선다 |
  | 선행 연구 기회 | 논문 계열이 앞서고 소비자 언급이 낮다 | **못 선다** — ydc 에서도 데이터 미도착으로 보류 |

  못 서는 둘을 어휘에서 지우지 않는 것은 그 입력이 오는 날 규칙이 그대로 서기 때문이다. 없는 입력을 0 으로
  깔면 그 유형이 조용히 영원히 안 나온다 (`W_EVIDENCE` 의 넷째 항을 0 으로 깔지 않은 것과 같은 문장).
- 상수 둘 — `GAP_PRODUCT_GAP = 2.0`(%p) · `SATURATED_COMPOSITION = 15.0`(%). 둘 다 ydc `cards.py` 의 값이고
  **적합된 값이 아니다**: 보고서의 손잡이라 팀이 고른 읽기 좋은 수다. `EVIDENCE_FLOOR` 와 같은 자리이고,
  근거가 합의뿐이라는 것을 적는 것이 이 줄의 일이다.
- **인용 순서는 저장된 `rank`(좋아요)가 아니라 (별칭 구체성, 좋아요) 다.** 별칭은 주제 사전에 구체적인
  것부터 적혀 있어서(`발림성` 의 `발림성` 이 `제형`·`텍스처` 보다 앞) 일반어로 걸린 댓글이 뒤로 간다.
  그런데 상한이 3 이라 이 2차 정렬은 **고르지 않고 줄만 다시 세운다** — ydc 주석이 걱정한 "일반어로 걸린
  댓글이 좋아요 때문에 뽑히는" 일은 `TOP_PER_CELL` 을 늘려야 막힌다. 지금 하는 일은 그 사실을 한계
  문장으로 카드에 싣는 것이다.
  그 한계 문장이 보는 일반어 목록(`발림성`↔`제형`·`텍스처` 등 ydc 실측이 낸 네 줄)은 `analysis/cards` 에
  상수로 산다. **색인·추출 축의 불용어 목록이 아니다**(포크 #37 이 처분한 것은 그 축이다) — 카드가 자기
  근거를 의심하라고 다는 주석이고, 제 자리는 주제 사전의 `extra`(포크 #8)다. 거기로 옮기는 일은 사전
  판본을 올리는 일이라 이 단계가 하지 않는다.
- 한 유형은 한 장이다(같은 유형 카드 셋은 데모에서 한 장과 같다). 줄 세우는 세기는 유형마다 다르다 —
  `제품 공백 기회` 는 `abs(gap_pp)`, 나머지는 `opportunity_score`. 전부 점수로 세우면 점수가 NULL 인 셀의
  카드가 언제나 밀리는데, 그 카드는 점수가 아니라 갭으로 서는 것이다.
- 한계를 카드 안에 넣는다(설계 원칙 4): `hold_reason` · `single_source` · 최근 분기 과소 집계 · 일반어
  별칭. ydc 가 함께 실은 교차 검증 셋(커머스 구성비·설문 긍정률·성분 구성)의 원천은 그 뒤 §대조 가 세웠다 —
  다만 카드는 아직 그 답을 싣지 않는다(카드 본문을 바꾸는 일이라 #7 이 하지 않았다). 자막 이득은
  여전히 원천이 없어 빠져 있다. 빠졌다는 사실은 이 줄에 남고, 카드에 빈 절로 남지 않는다.

## 대조 (소스를 나란히 놓는다 — 저장하지 않는 세 답, 포크 #7)

셋(ydc `source_composition.py` · `commerce_crosscheck.py` · `cross_source.py` 의 성분 축)은 **합산하지
않는다.** 소스마다 분모가 다르다 — 구성비는 그 소스의 13주제 언급 합 대비, 플랫폼 속성 평가는 `topic_group`
안의 응답 비중, 성분 담론은 문서 수다. 더하거나 평균 내면 그 순간 뜻이 없어진다. 그래서 **크기가 아니라
순위와 방향**을 본다. **어긋나는 자리가 R&D 공백이다** — 예측이 아니라 현재 상태의 비대칭이라 §민감도 의
후향 검증에서 살아남는 종류다.

**어느 표에도 쓰지 않는다. 이 승격의 DDL 은 0장이다.** §민감도 와 같은 자리이되 어긋나는 칸이 다르다:
이 세 답의 행은 (주제) 또는 (성분) 하나가 키인데 022 의 분기 입자는 여덟 칸이 키다. 커머스 쪽에는 그 여덟
중 **분기도 명부도 없다** — 리뷰 수집이 최신 편향이라(2026년 **86.0%** = 25,818/30,021, 2026-08-27 실측) 분기로 쪼개면 "2026년 폭증"이 나오고
그것은 수집 방식의 산물이며, 플랫폼 속성 평가의 관측 창은 며칠뿐이다. 성분 축에는 `topic_key` 축 자체가
없다(`INGREDIENT_KEYS` 는 `aspect_lexicon(ruleset='retrieval-topic')` 의 레지스트리가 아니다).

새 표를 만드는 것 자체는 추가만의 범위 안이다 — #6 이 025 를 그렇게 만들었다. 무게를 지는 것은 그것이
아니라 **입자와 최신 편향**이다: 저장하려면 그 행이 어느 시점의 무엇에 대한 비율인지가 행에 실려야 하는데,
커머스 리뷰 수집은 최신 편향이고(2026년 **86.0%** = 25,805/30,008, 2026-08-27 실측) 속성 평가의 관측 창은
며칠이라, 어떤 시간 칸을 붙여도 그 칸이 뜻하는 바가 `metrics_topic_quarter` 의 분기와 다르다. 뜻이 다른
값을 같은 어휘의 칸에 넣는 것이 여기서 막는 일이다. 그래서 산출은 표가 아니라 **답**이고
(`cosmai trend crosscheck` 의 stdout), 읽기 전용이라 운영 DB 에 그대로 돌린다.

**한계**: 세 답 중 §구성 하나만은 `(스냅샷, 소스, 주제)` 라는 자리가 실재한다 — 시간 칸이 없고 소스가
`retrieval_chunk.source` 어휘라 022 와 충돌하지 않는다. 그것을 표로 만들지 않은 것은 나머지 둘과 한
명령의 답이기 때문이고, 다음 사람이 이 판단을 다시 물을 수 있게 여기 적어 둔다.

**네 소스가 드는 자리** (이슈 #7 완료 기준의 유튜브 댓글·자막·커머스 리뷰·랭킹):
| 소스 | 어디서 | 이 명령에서 하는 일 |
|---|---|---|
| 유튜브 댓글 | `retrieval_chunk(source='youtube_comment')` | 구성 — 소비자 반응 쪽 |
| 유튜브 자막 | `retrieval_chunk(source='youtube_transcript')` | 구성 — **제작자 쪽** |
| 커머스 리뷰 | `retrieval_chunk(source='commerce_review')` | 구성 — 실사용 쪽 · 성분 담론 |
| 커머스 랭킹 | `trend_radar.rank_snapshot` | **커머스 쪽 선케어 모집단을 정한다** |

랭킹이 모집단을 정하는 것이 뜻이다. ydc 는 제품명에 선크림 별칭이 든 것으로 골랐는데, 이름 부분문자열로
모집단을 정하는 것은 §성분 의 `시카` 사고와 **같은 실수**다(짧은 별칭이 다른 것을 잡는다). 여기서는
플랫폼이 선케어 보드·카테고리에 실제로 올린 제품이 그 모집단이고, 그 술어는 `SUN_BOARD='suncare'` 와
`SUN_CATEGORY`(`선케어`·`선크림`·`선블록`·`선스틱`·`선쿠션` 을 담은 `category_name`) 다.

### 구성 (`SourceShare` — ydc `source_composition.py`)
- 묻는 것: **같은 주제를 소스마다 얼마나 말하는가.** 유튜브만으로는 "이 주제가 실제로 중요한가"에 답할 수
  없다 — 언급량으로 만든 값을 언급량으로 검증하면 순환이고, 유튜브 안의 자막·댓글은 같은 플랫폼이라 편향을
  공유한다. 소스가 다르면 편향도 다르므로, 여러 소스가 같은 방향을 말하면 그것이 근거가 된다.
- **소스마다 다른 것은 문서의 성격뿐이고 계산은 하나로 고정한다.** 사전은 활성 주제 사전 하나
  (`analysis.retrieval.topics.match_topics`, `trend_use` 주제만) · 단위는 **문서 1건**(한 문서의 청크 여럿에
  같은 주제가 걸려도 한 번) · 분모는 그 소스의 `trend_use` 주제 언급 문서 수 합이다. 소스 간 문서 수를
  합산하지 않는다.
- **`metrics_topic_quarter` 를 읽지 않는다.** 저장된 구성비는 패널 명부·선크림 필터·분기를 거친 값이라
  커머스 쪽과 계산이 다르고, 다른 코드 경로에서 나온 두 값을 나란히 놓으면 차이가 소스의 것인지 경로의
  것인지 갈리지 않는다. 네 소스가 **같은 한 함수**를 타는 것이 이 블록의 전부다.
- **제작자 쪽은 `youtube_transcript` 다.** ydc 의 `youtube_video` 는 영상 설명이었지만 우리 `youtube_video`
  청크는 **제목 한 줄**이라(`analysis/retrieval/corpus.py` 의 `VIDEOS` 가 `title` 을 뜬다) 제작자 언어의
  그릇이 아니다 — 5,908문서에서 주제 언급이 1,123건뿐이다. 그래서 ydc 가 `video` 자리에 넣은 규칙 둘은
  자막 위에서 돈다. 제목 열은 표에 남지만 해석 규칙을 물지 않는다.
- 해석 둘은 ydc 의 문장 그대로다: `commerce >= 5 and creator < 2` → "영상 설명으로는 관측 불가 · 실사용
  발화에만 있음" · `|commerce − creator| >= 5`%p → 어느 쪽이 훨씬 많이 말하는가. `cross_source` 의
  "영상은 안 다루는데 댓글·리뷰에는 있음"(`commerce > 0 and creator < 0.5 and comment > creator * 3`)도
  같이 온다.

### 평가 (`RatingRow` — ydc `commerce_crosscheck.py`)
- 묻는 것: **언급량과 독립된 검증.** 우리 판정은 전부 언급량에서 나왔다. `trend_radar.review_topic` 은
  올리브영·다이소가 자체 리뷰 설문으로 집계한 속성 평가라, 언급량과 독립된 유일한 검증 재료다.
- **값이 아니라 방향을 본다.** 두 지표는 분모가 다르다 — 우리 `composition` 은 주제 간 구성비, 커머스
  `share_pct` 는 `topic_group` 안의 응답 분포다. 그래서 우리 쪽은 순위·`gap_pp`, 커머스 쪽은 긍정률로 바꿔
  놓고 해석한다.
- **`share_pct` 가 NULL 인 행은 들지 않는다.** 그 소스는 비중 대신 가중치(`score`)를 싣는데, 가중치와
  백분율은 다른 단위라 섞어 평균 내면 아무것도 보여 주지 않고 틀린다(`review_topic.score` 의 DDL 주석이 그 문장을 이미 든다). 전량에서 그 소스는 **10,920행 · 35제품**이다(2026-08-27 실측 — 수집기가 계속 쓰므로 이 수는 자란다. 판단을 지는 것은 행수가 아니라 `share_pct` 가 NULL 이라는 사실이다).
- **시점별 스냅샷이라 (제품, 선택지)별 최신 `captured_at` 한 행만 쓴다.** 같은 선택지가 수집 시점마다 한
  행씩 쌓이고, 속성 평가는 리뷰가 쌓여야 바뀌므로 시점 간 값이 거의 같다. 전부 세면 제품 수가 시점 수만큼
  부풀려진다.
- **극성의 정본은 사람이 확인한 표(`analysis/crosscheck/audit/polarity_v1.csv`)이고 힌트는 마지막
  수단이다.** ydc 처럼 힌트만 쓰면 성분 키와 **같은 병**을 앓는다 — 둘 다 벤더 문자열 위의 부분문자열
  목록이다. 운영 `review_topic` 의 `GROUP_MAP` 그룹 어휘 23개에 힌트만 먹여 본 결과(2026-08-27 실측)
  **다섯이 뒤집혔다**:
  | 그룹/선택지 | 힌트의 답 | 옳은 답 |
  |---|---|---|
  | `자극도/자극이 있어요` · `보습력/약간 건조해요` · `지속력/예상보다 짧아요` · `커버력/예상보다 짧아요` | positive | negative |
  | `가루날림/날림이 없어요` | negative (`없어요` 힌트) | positive |
  `GROUP_MAP` 일곱 그룹 중 다섯이 뒤집힌 라벨을 하나 이상 갖는다. **오늘 값은 옳다** — 선케어 집합에는
  바르게 분류되는 세 선택지만 오고, 확인된 표를 얹어도 71.7 / 74.5 그대로다. 그러나 `GROUP_MAP` 이
  존재하는 이유가 나머지 그룹이 오는 날이고, 그날 긍정률이 조용히 뒤집힌다(`보습력` 이 오면 힌트만으로는
  80%, 실제 40%). `수분감/매트해요` 를 negative 로 둔 것은 판단이다 — **축이 수분감이라 그 축의 낮은
  쪽**이고, 제품 선호로 매트가 장점인 것과는 다른 물음이다.
- 표가 모르는 문구는 힌트가 답하되(답을 안 하면 그 제품이 통째로 사라진다) 그 문구가 왔다는 사실을
  `tool/measure-crosscheck-keys` 가 말한다 — 성분 키와 **같은 도구, 같은 규약**이다. 그룹을 주지 않으면
  힌트만 도는데, 그것이 ydc 와 같은 답이라 `tool/compare-ydc-crosscheck` 의 1:1 이 그대로 선다.
- 긍정률 = 한 제품·한 `topic_group` 안에서 긍정 선택지가 차지하는 비중이고, 중립만 있으면 0 이다.
- 대조하는 분기는 **그 run 격자의 마지막에서 두 번째**다. 마지막 분기는 판정이 `미확정(진행 중)` 으로
  두는 진행 중 분기라 과소 집계된다. 인자로 받지 않는 이유는 `quarter`·`judge` 와 같다 — 고르는 길이
  둘이면 분모도 둘이 된다.
- **`MIN_PRODUCTS = 5` 미만인 주제에는 해석을 쓰지 않는다.** 우리 판정에 `document_count >= 5` 를 요구하면서
  이 대조에만 예외를 두면 이중 기준이다. 그 사실은 종료 코드가 아니라 표와 `note` 가 싣는다.

### 성분 (`IngredientRow` — ydc `cross_source.py` 의 성분 축)
- 묻는 것: **성분을 말하는 곳과 쓰는 곳이 같은가.** ydc 는 NAVER 검색·논문·선케어 처방·담론 넷을 놓았다.
  여기서는 **담론 셋**(유튜브 전체 · 유튜브의 선크림 문맥 · 커머스 리뷰)만 선다. 나머지 셋은 아래 이유로
  이 레포에서 서지 않는다.
- **NAVER 축은 없다.** `needs.naver_datalab_point` 가 0행이다 — 수집기(#9)가 아직 안 돌았다. 0 으로 채우면
  없는 값이 있는 값처럼 보이므로 열 자체를 두지 않는다(ydc 가 검색어 없는 성분을 빈칸으로 둔 것과 같은
  규칙).
- **논문 축은 `PAPER_HOLD` 그대로 꺼져 있다.** ydc 의 정정판 근거를 그대로 옮긴다: **분자는 전분야 검색어
  (잔존율 20~48%)이고 분모 `cosmetic` 은 화장품 검색어다 — 모집단이 다른 값으로 나눴다.** 초판이 근거로
  적었던 "`cosmetic` 잔존율 100.1%" 는 **쓰지 않는다**(필터가 `AND (skin OR cosmetic OR dermatology)` 이고
  검색어가 `cosmetic` 이라 아무것도 걸러내지 못하는 항등식이다). 원천 자체도 이 레포에 없다.
- **처방 축은 `FORMULA_HOLD` 로 잠근다.** `trend_radar.product.ingredients` 가 있는 제품은 180개인데 그중
  **선케어는 2개**다(전량 실측 2026-08-27). 180개로 채택률을 내면 "선케어 처방 채택률"이라는 이름 아래
  다른 모집단의 비율이 서는데, 그것이 바로 바로 위 `PAPER_HOLD` 가 정정한 그 오류다. 성분 데이터셋이
  정해지면(#10) 같은 명령으로 켠다. **감사 경로는 그때를 위해 지금 선다.**
- **두 글자 별칭을 성분명 부분문자열로 쓰지 않는다.** ydc `v0.3.0 e5a1b00` 의 정정이고, **우리 표에서 다시
  감사해 같은 오매칭을 확인했다**(2026-08-27, 180제품 · 성분행 22,705 · 고유명 2,051):
  | 별칭 | 우리 표에서 무엇을 잡나 | ydc | 처분 |
  |---|---|---|---|
  | `시카` | **216행 전부 트라이에톡시카프릴릴실레인(209)·트리에톡시카프릴릴실란(7)** — 실리콘 분산제다 | 263행 전부 같은 물질 | 버린다 |
  | `레티놀` | 7행. `레티날` 은 0행이고 둘은 다른 물질이다 | 8행 | 버린다 |
  | `센텔라` | **0행.** 성분표는 병풀·마데카소사이드·아시아티코사이드로 적는다 | 0행 | 키에 남긴다 — 0행일 뿐이고, 그래서 `병풀` 이 필요했다 |
  고친 키는 `"레티날": ("레티날",)` 와 `"시카센텔라": ("병풀","센텔라","마데카","아시아티코","아시아틱")` 다.
- **ydc 의 `[의심]` 규칙은 이 사고를 잡지 못한다 — 옮기지 않았다.** 그 규칙은 "잡힌 성분명에 키가 하나도
  안 들어 있는가" 인데, 매처는 대소문자·공백을 접고 그 규칙은 원문 그대로 보므로 실제로 잡을 수 있는 것이
  폴딩 아티팩트(`pdrn` 대 `PDRN`)뿐이다. 정작 `시카` 는 그 규칙을 **만족한다** — 트라이에톡시카프릴릴실레인
  안에 `시카` 가 진짜로 들어 있다. 그것을 잡은 것은 규칙이 아니라 **찍힌 이름을 읽은 사람**이다.
- **그래서 기계 게이트는 사람이 한 번 읽어 금지한 목록이다.** 그 목록에 걸리면 `key_mismatch` 이고 종료
  코드 1 이다(§entrypoints). 잡은 행이 0인 것은 오매칭이 아니라 부재이므로 통과다 — `레티날`·`PDRN`·
  `엑소좀`·`트라넥삼산` 이 우리 표에서 그 자리다. 금지에는 두 층이 있다:
  | 층 | 무엇 | 왜 |
  |---|---|---|
  | `DENIED_NAMES` | 트라이에톡시카프릴릴실레인 · 트리에톡시카프릴릴실란 | 어느 키도 잡으면 안 되는 물질 |
  | `DENIED_FOR` | (`레티날`, 레티놀) | **금지의 단위는 물질이 아니라 (키, 물질)이다.** 전역으로 두면 실측 한 줄이 무고한 키를 빨갛게 만든다 — 공백으로만 나열한 성분표에 `벼에스에이치-올리고펩타이드-1   * 레티놀 함량 509 IU/g` 가 통째로 한 이름이라, `펩타이드` 키가 그것을 잡는 것은 오매칭이 아니다 |
  게이트는 **매처와 같은 폭**이다(공백·대소문자를 접은 부분문자열). 완전 일치로 물으면 매처가 잡은
  `레티놀(0.04 ppm)` 을 게이트가 못 보는데, 운영 표의 `레티놀` 7행 중 **4행이 이미 그 접미사형**이라
  맨 `레티놀` 3행이 사라지는 날 게이트가 조용해진다.
- **그 목록이 못 보는 자리 — 아직 모르는 오매칭 — 는 `tool/measure-crosscheck-keys` 가 진다.**
  키가 무엇을 잡는지의 정본은 `analysis/crosscheck/audit/known_names_v1.csv`(2026-08-27 운영 표를 사람이
  읽어 확인한 **190 이름**)이고, 그 도구가 지금 표를 다시 재어 목록과 맞댄다: 금지에 걸리거나 목록에 없는
  이름이 어떤 키에 들어오면 종료 코드 **1** 로 그 이름과 제품 수를 찍는다(있던 이름이 사라진 것은 빨갛지
  않다 — 제품이 빠진 것은 오매칭이 아니다). **CI 는 이 일을 할 수 없다**: 운영 표에 닿지 못하고, 픽스처에
  고정한 문자열 몇 개는 코퍼스가 자라도 깨지지 않는다. CI 가 지는 것은 목록의 **자기 정합**뿐이다(모든
  이름이 정말 그 키에 잡히는가 · 금지에 걸리는 것이 없는가 · 키가 빠지지 않았는가).
  실측으로 확인했다: `INGREDIENT_KEYS` 에 `"세라마이드": ("세라",)` 를 넣으면 그 도구가 **카프릴릭/카프릭
  트라이글리세라이드(59제품, 에몰리언트)** 를 찍고 종료 코드 1 을 낸다 — `시카` 사고의 재현이다.
- **성분표를 성분명으로 쪼개는 규칙 둘**(우리 원천에만 있는 함정이라 ydc 에 대응이 없다): 대괄호 구간
  표시(`[마데카소사이드] 정제수` · `[시카에센스]`)는 성분명이 아니라 기획 세트의 구성품 이름이라 버린다
  (`콜라겐` 72→64행 · `시카센텔라` 179→174행이 이것으로 걸러진다) · 괄호 안의 쉼표는 자르지 않는다
  (`나이아신아마이드(20,000 ppm)` 가 두 성분으로 쪼개진다). 쉼표 없이 공백으로만 나열한 성분표는 한
  덩어리로 남고, 그 사실을 `note` 가 센다 — 조용히 쪼개면 배합 순위가 틀린 값으로 선다.
- **담론 수를 "선크림 담론"으로 읽으면 안 된다.** 색인 전체에서 센 값이라 같은 채널이 소개한 앰플·
  스킨부스터가 다 들어 있다. 그래서 `SUN_WORDS`(`선크림`·`썬크림`·`선스크린`·`자차`·`선세럼`·`선쿠션`·
  `자외선차단`)가 **같은 청크 안에** 있는 것을 따로 센다. 같은 문서가 아니라 같은 청크인 것은 자막 한
  편이 500자 청크 12개 남짓이라 문서 단위로 보면 영상 전체가 선크림 문맥이 되기 때문이다. 전량에서 PDRN 은
  유튜브 933문서 중 **149문서**(16.0%)뿐이다(ydc 는 1,522건 중 187건). 담론은 성분명 매칭과 달리 원문
  그대로 본다(ydc `count_terms`) — 자유 문장에서 공백을 접으면 낱말 경계를 넘어 붙어 없는 언급이 생긴다.

### 대조 상수 (`analysis/crosscheck` 한 곳에 모여 있다)
| 상수 | 값 | 무엇 위에서 나왔나 | 판단 |
|---|---|---|---|
| `MIN_PRODUCTS` | `5` | §수식 의 표본 게이트와 **같은 수**다 — 판정에 5를 요구하면서 대조에만 예외를 두면 이중 기준이다 | 채택. `analysis.trend.MIN_MENTIONS` 와 같은 수이되 세는 것이 문서가 아니라 제품이라 따로 이름을 갖는다 |
| `LEAD_PP` | `5.0` | ydc `source_composition.reading` 의 세 갈래가 전부 이 폭이다 | 채택. 우리 실측에서 `백탁`(커머스 9.80 대 자막 3.32)·`지속력_워터프루프`(1.10 대 6.36)가 이 폭으로 갈린다 |
| `THIN_PP` | `2.0` | 같은 규칙의 "영상 설명으로는 관측 불가" 문턱 | 채택 |
| `SPARSE_PP` · `TALK_RATIO` | `0.5` · `3` | ydc `cross_source.topic_table` 의 "영상은 안 다루는데 댓글·리뷰에는 있음" | 채택. 우리 표에서는 한 주제도 걸리지 않는다 — 자막이 설명보다 두껍기 때문이고, 규칙을 지우면 원천이 바뀌는 날 되살릴 근거가 없다 |
| `SUN_SHARE_LOW` | `25` | 선크림 문맥 비율이 이 아래면 그 수를 "선크림 담론" 으로 읽지 말라고 말한다. 우리 실측 PDRN 149/933(16.0%, **문서**)이 이 아래다. ydc 의 187/1,522(12.3%)는 **청크**를 센 값이라 같은 자 위에 있지 않다 — 방향이 같다는 것만 말한다 | 채택. 적합값이 아니라 읽기 기준이다 — 4분의 1 은 "대체로 선크림 얘기다" 라고 말할 수 있는 최소선이고, 전량에서 열 성분이 **전부** 이 아래라 이 열은 지금 경고 하나를 뜻한다 |
| `POSITIVE_RATE_HIGH` | `80` | ydc `commerce_crosscheck.reading` 의 만족도 문턱 | 채택. 우리 실측 두 셀(`발림성` 71.7% · `자극_눈시림` 74.5%)이 둘 다 이 아래다 |
| `GAP_PP_MATERIAL` | `1.0` | 같은 규칙의 "많이 말한다" 문턱(`gap_pp` = 댓글 구성비 − 영상 구성비, §판정) | 채택 |
| `FORMULA_HOLD` · `PAPER_HOLD` | `True` · `True` | 위 두 줄의 모집단 근거 | 채택. 켜는 조건은 각각 #10 의 성분 데이터셋과 검증 프로토콜이다 |

### 전량 실측 (2026-08-27, 운영 DB 읽기 전용)
- 명령 한 번이 **12.9초 · 최대 상주 150MB**(2026-08-27 `cosmai trend crosscheck`, 종료 코드 0). 그중
  청크 한 번 훑기가 381,950청크 48MB **11.3초**다(키셋 2만 행 페이지, 페이지마다 커밋 —
  `analysis/retrieval/eval.py` 의 `gold_from_chunks` 와 같은 방식이고 같은 이유다). 커머스 쪽 세 질의는
  각각 0.4초 아래다.
- 구성: 커머스 리뷰 6,349문서(선케어 랭킹 제품의 리뷰 7,324건 중 청크가 선 것) · 댓글 285,735 · 자막 5,303 ·
  제목 5,908. `백탁` 이 커머스 **9.80%** 대 댓글 1.55% 로 여섯 배 갈린다.
- 평가: 선케어 랭킹 제품 중 속성 평가가 있는 것 19개 · 468행. 우리 주제로 오는 `topic_group` 은 `자극도`·
  `발림성` 둘이고 `피부타입` 은 `GROUP_MAP` 밖이다.
- 극성: `GROUP_MAP` 그룹의 선택지 어휘 23개가 전부 확인된 표에 있다(미확인 0건). 힌트만으로는 다섯이
  뒤집힌다 — 위 표.
- 성분: 감사 의심 **0건**(고친 키 기준, 금지 목록을 잡은 키 없음). 별칭 셋의 값은 위 표에 있고, 쉼표 없이
  공백으로만 나열한 성분표는 성분행 22,705 중 **60**행(고유명 59)이다.

### ydc 와의 대조 (2026-08-27 실행, 38줄 **차이 0**)
**승격 원본은 핀(`v0.1.0 02440ab`)이 아니다** — `cross_source.py` 는 그 뒤 `v0.3.0`(`e5a1b00`)에서 성분 키와
선크림 문맥이 정정됐고 승격한 것은 그 판이다. `analysis/slices/ydc/` 의 핀은 그대로 두었고(그 디렉터리는
읽기 전용 참조다), 태그에서 그 파일을 꺼내 **손대지 않고 돌리는** 절차와 대조 코드는
`tool/compare-ydc-crosscheck` 한 자리에 있다. 맞대는 것 셋:
- **상수** 열(키 표 · `SUN_WORDS` · `PAPER_HOLD` · `GROUP_MAP` · 극성 힌트 둘 · `MIN_PRODUCTS` · 해석 문구
  셋)을 ydc 모듈에서 직접 읽어 비교한다 — 옮겨 적다 어긋난 한 글자가 여기서 걸린다.
- **규칙** 열여덟(`ranks` · `positive_rate` · `polarity` 다섯 · 평가 해석 넷 · `count_terms` 넷)에 같은
  입력을 먹여 답을 비교한다. `count_terms` 가 든 것은 담론 매칭이 성분명 매칭에서 갈라졌기 때문이고
  (§성분), 그 갈래는 상수 비교로는 보이지 않는다.
- `cross_source.topic_table` 이 **함수 안에 리터럴로** 들고 있는 셋(`SPARSE_PP` 0.5 · `TALK_RATIO` 3 ·
  `READ_COMMENT_ONLY` 문구)은 함수를 부를 수 없어 그 소스를 읽어 맞댄다. 안 그러면 "차이 0" 이 이 셋을
  덮지 않는데 덮는 것처럼 읽힌다.
- **`polarity` 는 그룹을 주지 않고 맞댄다** — 우리 쪽 정본은 확인된 표이고(§평가) ydc 에는 그 표가 없다.
  그룹 없는 답이 힌트만 도는 답이라 그 자리에서 둘이 같다.
- **감사**: ydc 의 성분표 CSV(31,246행 · 577제품)를 양쪽 감사에 그대로 먹여 키별 (행, 제품)을 비교한다.
  원천이 같은 유일한 자리라 **이 이슈에서 가능한 유일한 1:1** 이고, 열 키 전부 일치한다(`시카센텔라`
  429행 · 202제품 = **35.0%** · `레티날` 0행 — 정정 전 `시카` 의 41.1% 가 아니다).

**전량 대조는 CI 가 지킬 수 없다**: ydc 의 성분표 CSV 도 패널 run 도 그 레포에 있고 이 레포에 넣을 것이
아니다. 그래서 CI 가 지는 것은 규칙과 키의 catch 집합이고(`tests/test_crosscheck_rules.py` ·
`tests/test_crosscheck_keys.py`), 원천을 맞대는 일은 사람이 한 번 돌린다.

### `commerce_ranking.py` 는 승격하지 않는다 (이슈 #7 §확인할 것 — 처분은 **보류**)
`analysis/aggregate/ranking.py` 와 겹친다. `rank_daily` 가 (소스, 보드, 카테고리, 제품, 날짜)마다
`n_present`·`present_share`(= ydc 의 관측 밀도·진입·이탈의 재료) · `rank_mean`/`rank_min`/`rank_max`
(= `best_rank`/`worst_rank`/`swing`)를 이미 `needs` 에 쓰고 있고, ydc 가 더하는 것은 그 위의 창 단위
말아 올리기 셋(`swing`·`moved`·`entered`/`left`)뿐이다. 저장된 행에서 SQL 한 줄로 나오는 값을 위해 두
번째 산출 경로를 만들면 그 순간 정본을 다툰다. 그 세 값을 화면이 원하면 뷰가 답이지 승격이 아니다.

## 모집단의 한계 (숫자를 읽는 법 — `manifest.limitations`, 포크 #4)
2026-08-19 코퍼스(`needs.corpus_*`, `formats.md` §코퍼스 스냅샷)를 걷은 두 런이 스스로 적어 둔 여덟
문장이다. 위 §수식 이 값을 **어떻게 만드는가**를 말한다면 이 절은 만들어진 값이 **무엇이 아닌가**를
말한다 — 이 문장이 계약에 없으면 나중에 숫자만 남고, 같은 숫자가 다른 뜻으로 읽힌다. 여덟 줄은
`db/corpus/contract.py` 에 상수로 서 있고 적재기가 매니페스트와 대조한다.

- 모집단은 시드 채널 집합이며 전체 YouTube가 아니다(고정 패널).
  - 고정 패널이므로 이 비율은 "한국 유튜브에서"가 아니라 "이 43채널에서"다. 분모가 행 안에 있는 것(`panel_version`·`panel_role`)이 그래서다.
- 패널 밖 신규 채널·신규 브랜드의 등장은 관측되지 않는다.
  - 따라서 **신규 브랜드의 부재는 신호가 아니다.** 확산도(`channel_diffusion`)가 낮은 주제를 "아직 안 퍼졌다"로 읽기 전에 패널 밖일 가능성을 먼저 본다.
- 조회수·좋아요는 collected_at 시점 스냅샷이다.
  - `source_metadata.view_count`·`like_count` 는 시계열이 아니다. 열로 올린 `corpus_document.collected_at` 이 그 시점이고, 두 스냅샷의 같은 영상은 조회수가 다른 것이 정상이다.
- 업로드 플레이리스트 최신순 가정에 기반해 cutoff에서 조기 종료한다.
  - 수집이 `published_after` 아래를 다 훑었다는 보장이 아니다. 가장 오래된 분기는 절단된 표본일 수 있다.
- 댓글은 주제 사전에 걸린 영상만 받는다. 전체 영상의 댓글 분모는 존재하지 않는다.
  - **댓글 쪽 비율의 분모는 언제나 "주제가 걸린 영상의 댓글"이다.** 전체 영상의 댓글 분모는 만들 수 없으므로, 댓글 구성비를 "소비자 관심의 점유율"로 읽으면 틀린다.
- 댓글 published_at은 댓글 자체 시각이다. 분기 귀속은 video_id로 부모 영상에 붙인다.
  - 그래서 분기는 댓글 시각이 아니라 부모 영상의 분기다(§수식 의 "분기 문서 모집단", `parent_item_id`).
- 댓글은 계속 쌓이므로 최근 분기는 구조적으로 과소 집계된다.
  - **최근 분기의 하락은 트렌드가 아닐 수 있다.** YoY 판정이 최근 분기를 만질 때 이 한계가 먼저다.
- order=relevance는 유튜브 비공개 알고리즘이며 좋아요 순이 아니다.
  - 댓글 표본은 인기순도 무작위도 아니다. 댓글 기반 지표를 "상위 반응"으로 읽지 않는다.

이 한계들은 재수집(#38)으로 사라지지 않는다 — 같은 방법으로 다시 걷기 때문이다. 사라지는 것은
2026-08-19 이라는 시점뿐이고, 그래서 스냅샷을 덮지 않고 판본으로 나란히 둔다.

## 평가 하네스가 대조하는 기준선 (규칙 구현, 2026-08-23 실측)
| task | 평가셋 | 규칙 기준선 | 채택 조건 (단일 임계값) |
|---|---|---|---|
| polarity (선케어 홀드아웃) | sun holdout 100 | acc .77 · 불만 P .89 / R .70 | acc ≥ .77 그리고 P:불만 ≥ .89 |
| polarity (카테고리 횡단) | P1 blind40 (holdout) | acc .47 · 불만 P .67 | acc ≥ .47 그리고 P:불만 ≥ .67 |
| wish_class | P9 blind60_v2 (holdout, 2026-08-23 라벨) | a: P .94 / R .94 (holdout60, 비블라인드) | blind60_v2 에서 P:a ≥ .90 |
| brand_link | P3 120 | 정밀도 119/120 | P:OK ≥ .97 |
| product_match | P2 blind 40 (holdout, `match_check40_v2_blind`) | strict .77 / 변형허용 .95 (채택 39쌍) | 채택 쌍에서 strict ≥ .769 |
- T10/T11: 채택 조건은 **단일 숫자**다. 구간으로 적힌 기준선은 기계 대조가 불가능하다.
- **이 표의 숫자는 규칙이 낸 원값의 반올림 표기이므로 대조도 적힌 자리수에서 한다.** 하네스는 지표를
  임계값과 같은 소수 자리로 반올림한 뒤 비교한다 (`analysis.baselines.meets`, 하네스가 점수를 찍는
  `.3f` 와 같은 반올림). 자리수가 곧 이 게이트의 해상도다: p1 의 `.67` 은 두 자리라 규칙이 낸
  `2/3 = .6667` 이 통과하고, product_match 의 `.769`(= `30/39`, 원천 `slice-p2/README.md` 의 `.77` 을
  세 자리로 되살린 값)는 세 자리라 그만큼 촘촘하다. 옮겨 적을 때 자리수를 늘리면 기준이 촘촘해지고
  줄이면 느슨해지니, 자리수도 숫자와 함께 계약이다 — `tests/test_baselines.py` 가 표의 글자 그대로
  대조한다. 원값과 곧이곧대로 비교하면 기준선을 만든 규칙 자신이 `--check-baseline` 에 진다(#2 실측);
  그 성질은 같은 파일이 규칙의 원값(`43/47`, `2/3` …)과 실제 구현 실행으로 지킨다.
- 채택 조건의 이름은 하네스가 내는 지표 키 그대로다 — `acc` · `P:<라벨>` · `R:<라벨>` · `strict` · `변형허용`.
  `tests/test_baselines.py` 가 이 표를 파싱해 `analysis/baselines.py` 의 (이름, 숫자)와 대조한다.
- **product_match 의 strict / 변형허용은 정확도가 아니라 채택 집합에 대한 정밀도다.** 구현이 행마다 채택(`Y`)·
  비채택(`N`)을 내고, 분모는 **채택한 쌍의 수**다: `strict = |채택 ∧ gold='Y'| / |채택|`,
  `변형허용 = |채택 ∧ gold ∈ {'Y','V'}| / |채택|`. 40행 정확도로 재면 같은 구현이 다른 숫자를 낸다.
  기준선 .77/.95 는 v2 규칙이 채택한 39쌍(`match_check40_v2_blind.csv` 의 `in_final=1`)에서 나온
  30/39 = .769 · 37/39 = .949 이고, `tests/test_cli_eval.py` 가 그 채택 집합으로 재현을 검사한다.
- 구현 교체(규칙→LLM, 사전 버전 업)는 이 표를 갱신하는 PR 로만 들어온다.

### 규칙 실측 (2026-08-24, #3 구현체) — 구현 교체의 기준
| 평가셋 | 규칙 실측 |
|---|---|
| sun holdout 100 | acc .870 · P:불만 .915 |
| p1 blind40 | acc .475 · P:불만 .667 |
- 위 표의 `채택 조건`은 계약이 요구하는 **바닥**이다. 구현 교체(규칙→LLM)는 여기 숫자를 **넘어야** 한다 —
  `--check-baseline` 이 녹색이어도 이 표에서 지면 `polarity_version` 을 갈지 않는다 (이슈 #6).
- `analysis/baselines.py` 의 `RULE_MEASURED` 가 이 표의 사본이고 `tests/test_baselines.py` 가 대조한다.
- 대조는 위 기준선 표와 같은 자다 — 적힌 자리수로 반올림한 뒤 비교한다. `.915` 는 원값 `43/47 = .914893…`,
  `.667` 은 `2/3` 의 세 자리 표기다.

### LLM 실측 (2026-08-24, 블라인드 홀드아웃 — 이슈 #6 §산출물 6)
| 평가셋 | 규칙 실측 | Sonnet 5 (llm-claude-sonnet-5-20260824) | Opus 5 (llm-claude-opus-5-20260824) | gemma4 (llm-ollama-gemma4:latest-fs2-20260824) |
|---|---|---|---|---|
| sun holdout 100 | acc .870 · P:불만 .915 / R .915 | acc .910 · P:불만 .979 / R .979 | acc .940 · P:불만 .979 / R 1.000 | acc .900 · P:불만 .978 / R .936 (만족 P .974 / R .864) |
| p1 blind40 | acc .475 · P:불만 .667 / R .455 | acc .950 · P:불만 .955 / R .955 | acc .950 · P:불만 .917 / R 1.000 | acc .850 · P:불만 1.000 / R .773 (만족 P 1.000 / R .933) |
- 프롬프트 판본 `PROMPT_DATE=20260824`(튜닝 무수정, tune과 같은 판본), 홀드아웃 각 모델 1회(블라인드 유지).
- 비용: Sonnet $0.289 / Opus $0.398 (둘 다 Batches API).
- 채택 권고(조정자, 2026-08-24): 두 모델 다 계약 바닥·규칙 실측을 전부 넘었다 — **Sonnet 5** 권고(p1 P:불만 .955 vs .917, 가격 60%). `polarity_version` 교체 실행(전량 패스)은 #21 예산 결정 대기.
- **gemma4** (ollama, gemma4:latest 8B Q4_K_M, RTX 4060, think:false + few-shot fs2, 블라인드 홀드아웃 최종 1회, 비용 $0 로컬): 계약 바닥과 규칙 실측을 모두 넘었다 — sun acc .900 > 규칙 .870, P .978 > .915 / p1 acc .850 > .475, P 1.000 > .667.
  few-shot 없는 thinking OFF(sun acc .850 · P .976 / p1 acc .850 · P 1.000) 대비 few-shot 이 sun acc 를 5pt 올렸고 정밀도는 지켰다.
  이 프롬프트 판본은 **ollama 전용**이라 Claude 경로의 `PROMPT_DATE=20260824` 와 무관하다 — 위 Sonnet/Opus 숫자는 그대로 유효하다.
- 이 표는 **기록**이다 — 하네스가 대조하는 기준선(계약 바닥 표·위 규칙 실측 표)은 이 표로 바뀌지 않는다.

## 검색 실측 (#28 단계 4 — 2026-08-25, 전 소스 · 청크 381,950 · 질의는 주제 별칭)
| mode | engine | 질의 | P@10 | MRR@10 | Hit@10 |
|---|---|---|---|---|---|
| literal | bm25 | 61 | .864 | .893 | 91.8% |
| literal | vector | 61 | .618 | .785 | 91.8% |
| literal | hybrid | 61 | .839 | .911 | 95.1% |
| heldout | bm25 | 60 | .000 | .000 | 0.0% |
| heldout | vector | 60 | .062 | .114 | 25.0% |
| heldout | hybrid | 60 | .025 | .029 | 8.3% |
- **채택 조건은 heldout 의 bm25 줄이다: P@10 > .000.** heldout 의 정답은 질의 토큰이 하나도 없는 같은 주제
  문서라 어휘 검색은 구조적으로 0 이고, 그 0 이 벡터가 넘어야 하는 선이다(두 모드의 정의는
  `analysis/retrieval/eval.py`). vector 는 .062 · Hit 25.0% 로 넘었고 hybrid 는 .025 · 8.3% 로 넘었다 —
  **벡터 채택의 근거가 이 한 줄이고, 이 표가 그 숫자의 거처다.**
- literal 은 성능이 아니라 **고장 감지**다. 정답 자체가 문자열 매칭으로 만들어졌으니 bm25 가 이기는 것이
  정상이고(.864), 여기가 무너지면 토큰화가 깨진 것이다(사전 미적용·정규화 불일치).
- 질의 수가 모드마다 다른 것은 별칭이 하나인 주제(`혼합자차`)가 heldout 에서 빠지고, 정답이 빈 질의는
  채점하지 않기 때문이다.
- 이 표는 **기록**이다 — 하네스가 대조하는 기준선(위 두 표)은 이 표로 바뀌지 않고, `--check-baseline` 도
  이 숫자를 보지 않는다. `tests/retrieval/test_contract.py` 는 표의 모양(mode×engine 여섯 줄과 채택 조건)만
  붙든다. 자동 라벨(주제 사전)로 만든 점수라 손잡이를 고르는 데 쓰고, 사람이 만든 골든셋은 최종 보고에 한 번만 쓴다.
- 이 숫자를 만든 주제 사전은 **`needs.aspect_lexicon` 의 활성 버전**(`ruleset='retrieval-topic'`, v1)이다.
  포크 #8 이 원천을 `analysis/retrieval/topics.py` 의 상수에서 그리로 옮겼고, 옮긴 사전이 상수판과 **같은
  15개 주제·같은 별칭·같은 `match_topics` 결과**라는 것을 `tests/retrieval/test_topics.py` 가 얼어붙은 사본
  (`tests/retrieval/frozen_topics.py`)과 맞대어 붙든다 — 그 동등성이 깨지면 이 표는 조용히 낡은 표가 된다.
- 원값은 `var/retrieval/score_{mode}_{engine}.csv` 여섯 벌(`var/` 는 레포에 들어가지 않는다). 잰 시점은 정답셋
  소스 좁힘(포크 #16)과 정답 키셋 페이징(포크 #17 S4) **이전**이다 — 둘 다 전 소스 실행이 훑는 행 집합을
  바꾸지 않으므로, 다시 재서 값이 움직이면 그것은 이 두 변경이 아니라 코퍼스가 자란 것이다.
- **질의 불용어(포크 #46)는 이 여섯 줄을 움직이지 않는다.** 재실행이 아니라 구성으로 그렇다: 질의 61/60개는
  전부 주제 별칭이고, 별칭 73개 중 어느 것도 토큰화하면 그 목록과 겹치지 않는다(실측 2026-08-26 · 겹침 0 ·
  `tests/retrieval/test_query_stopwords.py`). 겹침이 0 이면 `tokenize_query` 는 `tokenize` 와 같은 토큰을
  내므로 bm25 세 줄이 그대로고, vector 는 질의를 토큰화하지 않으며(원문을 인코딩한다) heldout 의 정답 제외
  (`eval.docs_with_tokens`)와 색인은 둘 다 `tokenize` 축이라 애초에 이 목록을 보지 않는다. ydc 도 자기
  별칭 61개로 같은 것을 쟀고 같은 0 이었다.

## 패스 기준 (2026-08-23 결정: 6단계까지 무정지 → 2차 패스에서 기준선)
| 패스 | 유닛 완료 기준 | 검사 |
|---|---|---|
| 1차 | 계약 시그니처 구현 + `cosmai eval <task>`가 그 유닛의 평가셋에서 **점수를 산출**한다(기준선 미달 허용) + `analyze <stage>` 멱등 | eval 출력 행이 `needs.analysis_run.note`에 기록, 점수는 이슈 코멘트 |
| 2차 | 위 표의 기준선 이상 | 같은 평가셋, 블라인드 홀드아웃 |
기준선 표는 계약이고, 패스는 순서다. 1차 패스 점수가 기준선을 이미 넘으면 2차는 생략한다.
