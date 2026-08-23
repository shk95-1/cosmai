# 분석 패키지 인터페이스 (Python, 타입은 dataclass/TypedDict)

```python
# analysis/types.py
@dataclass(frozen=True)
class TextUnit:  # 분석 입력의 최소 단위
    src: str  # review | yt_comment | yt_transcript | naver_blog
    site: str
    ref: str  # 안정 키 (review: product_key/review_key, comment: video_id/comment_id)
    text: str
    observed_at: date
    observed_at_resolution: str  # day | month | year
    rating: float | None = None
    like_count: int | None = None
    product_key: str | None = None
    category: str | None = None
    channel_id: str | None = None


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


@dataclass(frozen=True)
class PolarityResult:
    aspect: str | None
    polarity: str  # 불만 | 만족 | 중립
    reason: str
    version: str  # rule-v2.2 | llm-<model>-<date>


@dataclass(frozen=True)
class WishResult:
    wish_class: str  # a | b | c | n
    brand: str | None
    format: str | None
    attribute: str | None
    marker: str | None
```

```python
class Linker(Protocol):
    version: str

    def link(self, unit: TextUnit, lexicon: Lexicon) -> list[EntityHit]: ...
    def match_products(self, products: Iterable[ProductRow]) -> list[ProductRefRow]: ...  # 사이트 간 식별


class Extractor(Protocol):
    version: str

    def candidates(self, unit: TextUnit, aspects: AspectLexicon) -> list[Candidate]: ...
    def wishes(self, unit: TextUnit, lexicon: Lexicon) -> WishResult | None: ...


class Polarity(Protocol):  # ← LLM 삽입점. 규칙 구현과 LLM 구현이 같은 시그니처
    version: str

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult: ...


class Aggregator(Protocol):
    version: str

    def need_metrics(
        self, mentions: Iterable[NeedMentionRow], denominators: Iterable[DenominatorRow], scope: str
    ) -> list[MetricsNeedRow]: ...
    def wish_metrics(self, wishes: Iterable[WishMentionRow], scope: str) -> list[MetricsWishRow]: ...
```

## 평가 하네스가 대조하는 기준선 (규칙 구현, 2026-08-23 실측)
| task | 평가셋 | 규칙 기준선 | 채택 조건 |
|---|---|---|---|
| polarity (선케어 홀드아웃) | sun 100 | acc 77% · 불만 P .89 / R .70 | 블라인드 홀드아웃에서 acc 와 불만 P 둘 다 ≥ 기준선 |
| polarity (카테고리 횡단) | P1 60+40 | acc 43–47% · 불만 P .56–.67 | 동일 |
| wish_class | P9 holdout 60 | a: P .94 / R .94 (v2.1, 비블라인드) | 새 블라인드 60에서 a P ≥ .90 |
| brand_link | P3 120 | 정밀도 119/120 | ≥ .97 |
| product_match | P2 blind 40 | strict .77 / 변형허용 .95 | strict ≥ .77 |
구현 교체(규칙→LLM, 사전 버전 업)는 이 표를 갱신하는 PR 로만 들어온다.

## 패스 기준 (2026-08-23 결정: 6단계까지 무정지 → 2차 패스에서 기준선)
| 패스 | 유닛 완료 기준 | 검사 |
|---|---|---|
| 1차 | 계약 시그니처 구현 + `cosmai eval <task>`가 그 유닛의 평가셋에서 **점수를 산출**한다(기준선 미달 허용) + `analyze <stage>` 멱등 | eval 출력 행이 `needs.analysis_run.note`에 기록, 점수는 이슈 코멘트 |
| 2차 | 위 표의 기준선 이상 | 같은 평가셋, 블라인드 홀드아웃 |
기준선 표는 계약이고, 패스는 순서다. 1차 패스 점수가 기준선을 이미 넘으면 2차는 생략한다.
