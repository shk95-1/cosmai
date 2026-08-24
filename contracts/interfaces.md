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
  - `site_low_pct` = `(review_stats.pct_1 + review_stats.pct_2) / 100` (사이트가 보고한 저평점 비율)
  - `low_share` = `low_mentioning / denom_low` (저평점 표본 내 비율)
  - B7: 시드의 `seed:slice-p1` 행은 이 식이 아니라 수집 표본 근사(`100 * low_mentioning / denom_site`)로 계산된 값이다. 2차 패스 목표는 두 값의 차 ±0.05 이고 골든이 아니다.
- **like_cap_sum** (`metrics_wish`) = `sum(min(like_count, LIKE_CAP))`, **LIKE_CAP = 100** (A8: 슬라이스에 cap 이 없어 상수를 계약이 정한다). 상한을 쓰지 않는 구현은 이 컬럼을 NULL 로 둔다.
- **low_complete** (`product_denominator`) = `(low_collected < 150) or has_3star` — RATING_ASC 표본 안에 3★ 이 섞였거나 ≤2★ 가 150 미만이면 ≤2★ 는 전수다. 150 은 수집 표본 상한(`REVIEW_PAGES 3 x 50`)이고 `collectors/commerce/scope.json`(#7)과 `formats.md` 가 같은 값을 갖는다.

## 평가 하네스가 대조하는 기준선 (규칙 구현, 2026-08-23 실측)
| task | 평가셋 | 규칙 기준선 | 채택 조건 (단일 임계값) |
|---|---|---|---|
| polarity (선케어 홀드아웃) | sun holdout 100 | acc .77 · 불만 P .89 / R .70 | acc ≥ .77 그리고 P:불만 ≥ .89 |
| polarity (카테고리 횡단) | P1 blind40 (holdout) | acc .47 · 불만 P .67 | acc ≥ .47 그리고 P:불만 ≥ .67 |
| wish_class | P9 blind60_v2 (holdout, 2026-08-23 라벨) | a: P .94 / R .94 (holdout60, 비블라인드) | blind60_v2 에서 P:a ≥ .90 |
| brand_link | P3 120 | 정밀도 119/120 | P:OK ≥ .97 |
| product_match | P2 blind 40 (holdout, `match_check40_v2_blind`) | strict .77 / 변형허용 .95 (채택 39쌍) | 채택 쌍에서 strict ≥ .769 |
- T10/T11: 채택 조건은 **단일 숫자**다. 구간으로 적힌 기준선은 기계 대조가 불가능하다.
  임계값은 반올림이 아니라 규칙 구현이 실제로 낸 값이어야 한다: `규칙 기준선` 칸의 product_match `.77` 은
  원천 `slice-p2/README.md` 의 반올림 표기이고, 기계 대조는 그 출처인 `30/39 = .769` 로 한다
  (`.77` 로 두면 기준선을 만든 규칙 자신이 `--check-baseline` 을 통과하지 못한다 — #2 실측).
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

## 패스 기준 (2026-08-23 결정: 6단계까지 무정지 → 2차 패스에서 기준선)
| 패스 | 유닛 완료 기준 | 검사 |
|---|---|---|
| 1차 | 계약 시그니처 구현 + `cosmai eval <task>`가 그 유닛의 평가셋에서 **점수를 산출**한다(기준선 미달 허용) + `analyze <stage>` 멱등 | eval 출력 행이 `needs.analysis_run.note`에 기록, 점수는 이슈 코멘트 |
| 2차 | 위 표의 기준선 이상 | 같은 평가셋, 블라인드 홀드아웃 |
기준선 표는 계약이고, 패스는 순서다. 1차 패스 점수가 기준선을 이미 넘으면 2차는 생략한다.
