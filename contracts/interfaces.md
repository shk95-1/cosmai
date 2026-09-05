# Analysis package interfaces (Python; the types are dataclass/Protocol)

`analysis/types.py` and this code block **must be the same** — `tests/test_contract_types.py` compares the dataclass fields.
The audit ids (B·A·T) are the item numbers of the 2026-08-23 contract audit (issue #17).

```python
# analysis/types.py — must be the same as the code block in contracts/interfaces.md (tests/test_contract_types.py).
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol


# ---------- input ----------
@dataclass(frozen=True)
class TextUnit:  # the smallest unit of analysis input
    src: str  # review | yt_comment | yt_transcript | yt_title | naver_blog (reserved, #96: formats.md §ref)
    site: str
    ref: str  # a stable key. The grammar is formats.md §ref
    text: str
    observed_at: date
    observed_at_resolution: str  # day | month | year
    rating: float | None = None
    like_count: int | None = None
    view_count: int | None = None  # A11: the weight of a transcript unit
    product_key: str | None = None
    category: str | None = None  # the site's own string; dictionary choice is lexicon_category (formats.md)
    channel_id: str | None = None


# ---------- dictionaries ----------
@dataclass(frozen=True)
class EntitySurface:  # one dictionary row = entity_lexicon
    # product_line is not here: a line is not a headword but is composed from brand + line_tokens (A14).
    kind: str  # brand | format | attribute | ingredient | stopword | alias (001's CHECK vocabulary)
    canonical: str
    surface: str
    tier: str | None  # brand: normal | cooc_required | stop
    source: str | None


@dataclass(frozen=True)
class Lexicon:
    """One entity_lexicon version. Everything the linker and the wish extractor need."""

    version: int
    surfaces: tuple[EntitySurface, ...]
    surface_to_canonical: Mapping[str, str]  # lower-case keys included
    surface_re: re.Pattern[str]  # longest first, particles allowed
    stop: frozenset[str]
    cooc_required: frozenset[str]
    product_word_re: re.Pattern[str]  # for deciding co-occurrence with a product word
    cooc_window: int = 25  # 25 characters either side
    format_patterns: tuple[tuple[str, re.Pattern[str]], ...] = ()
    attribute_patterns: tuple[tuple[str, re.Pattern[str]], ...] = ()


@dataclass(frozen=True)
class AspectPattern:
    aspect: str  # need_key
    scope: str  # generic | category
    category: str  # only when scope=category; '' for generic
    pattern: re.Pattern[str]
    is_neutral_noun: bool  # a neutral-noun twin
    priority: int  # B5: matched in ascending order, ties by id
    ruleset: str  # B4: suncare-v2.2 | p1-v2.2 | shared


@dataclass(frozen=True)
class AspectLexicon:
    """Shared by polarity.classify and extractor.candidates. The loader reads ruleset IN (requested, 'shared')."""

    version: int
    ruleset: str
    patterns: tuple[AspectPattern, ...]
    discourse_marker_re: re.Pattern[str]
    wish_marker_re: re.Pattern[str]

    def for_category(self, category: str | None) -> tuple[AspectPattern, ...]:
        """priority ascending, ties by id — a category-only pattern hides a generic of the same name."""
        ...

    def complaint_marker_re(self, category: str | None) -> re.Pattern[str]:
        """discourse markers | every pattern of that category."""
        ...


# ---------- product identity ----------
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
    name_norm: str  # T19: the linker always emits it
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
    match_score: float | None  # A13: the dice of the same pair in candidates


@dataclass(frozen=True)
class ProductVariantRow:  # → needs.product_variant (B3: no algorithm produces it, so it is outside #2)
    source: str
    product_key: str
    variant_of: str
    variant_kind: str  # refill | size | scent | shade | option | set
    variant_label: str | None


@dataclass(frozen=True)
class ProductCandidateRow:  # → needs.product_ref_candidate (A13: a human review queue)
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
class ProductMatch:  # B2: one union-find run produces all four at once
    refs: tuple[ProductRefRow, ...]
    members: tuple[ProductMemberRow, ...]
    variants: tuple[ProductVariantRow, ...] = ()
    candidates: tuple[ProductCandidateRow, ...] = ()


# ---------- extraction ----------
@dataclass(frozen=True)
class EntityHit:  # linker output
    kind: str  # brand | format | attribute | ingredient | product_line
    canonical: str
    surface: str
    start: int
    end: int
    cooc: bool  # whether a product word co-occurs


@dataclass(frozen=True)
class Candidate:  # extractor output (per sentence)
    unit_ref: str
    sentence: str
    kind: str  # complaint | wish | low_rating
    marker: str
    subject: str | None = None  # A10: product name or video title — context when a human reads it


@dataclass(frozen=True)
class PolarityRequest:  # one classify_many item. classify's arguments, bundled as they are
    sentence: str
    rating: float | None = None
    category: str | None = None


@dataclass(frozen=True)
class PolarityResult:
    aspect: str | None  # B8: absent is stored as need_key=''
    polarity: str  # 불만 | 만족 | 중립
    reason: str
    version: str  # rule-v2.2 | llm-<model>-<date>


@dataclass(frozen=True)
class WishResult:
    wish_class: str  # a | b | c | n
    brand: str | None
    format: str | None  # A12: ';'-separated, at most 3, the first is the main value (formats.md)
    attribute: str | None
    marker: str | None
    sentence: str = ""  # B1: which sentence matched — wish_mention.sentence is NOT NULL


# ---------- mention rows ----------
@dataclass(frozen=True)
class NeedMentionRow:  # → needs.need_mention
    src: str
    site: str
    ref: str
    product_ref: str | None
    source_product_key: str | None
    category: str | None  # the site's own category string
    lexicon_category: str | None  # B10: the category used to select the dictionary
    need_key: str  # B8: no aspect = ''
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
    wish_class: str  # a | b | c ('n' is not stored)
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
    category: str | None  # B6: required for summing the category denominator
    site_review_count: int | None
    low_collected: int | None
    low_complete: bool | None
    site_low_est: float | None


# ---------- aggregates ----------
@dataclass(frozen=True)
class MetricsNeedRow:  # → needs.metrics_need
    run_id: int
    scope: str  # a category name | 'all'
    need_key: str
    month: str = ""  # '' = the whole period
    product_ref: str = ""  # '' = the category total
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
class PanelRosterRow:  # → needs.panel_roster (fork #3). One roster version — the parent panel_version points at
    version: int
    note: str | None = None  # what this version is (seed:channels_v1 …)


@dataclass(frozen=True)
class PanelChannelRow:  # → needs.panel_channel (fork #3). The 43-channel panel roster; the seed fills the values (#31)
    channel_id: str
    version: int  # the roster version. The same shape as a dictionary (formats.md §Panel roster CSV)
    panel_role: str  # product | expert — a channel off the roster is off the panel and the denominator
    handle: str | None = None
    channel_title: str | None = None
    role_basis: str | None = None  # why the role was set that way (team_message | name_rule_verified …)
    source_list: str | None = None
    active: bool = True


@dataclass(frozen=True)
class MetricsTopicQuarterRow:  # → needs.metrics_topic_quarter (canonical for the quarter, formats.md §Time)
    run_id: int
    scope: str  # a category name | 'all' (the metrics_need.scope vocabulary)
    # The registry of the topic axis is aspect_lexicon(ruleset='retrieval-topic').aspect, not needs.need_key
    topic_key: str  # 두 축은 `백탁` 하나만 겹친다 (tests/test_panel_quarter_contract.py)
    quarter: str  # 'YYYYQn'
    source: str  # youtube_video | youtube_comment — video descriptions and comments are shown side by side, not merged
    content_type: str  # long_form | short_form — the denominator is long-form only (§Formulas)
    panel_version: int  # the population of this ratio: panel_channel.version
    panel_role: str  # which population of that roster. product | expert
    mentions: int  # numerator: documents this topic matched
    documents: int  # documents of that population in that quarter
    quarter_mentions: int  # composition denominator: that quarter's trend_use topic mentions summed
    denom_channels: int  # panel channels in that quarter's output. Both sources use one value (§Formulas)
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
class TopicQuarterJudgementRow:  # → needs.topic_quarter_judgement (a derivation, not an aggregate — §Verdict)
    # The first eight columns are metrics_topic_quarter's primary key as they stand. A verdict takes one row
    # of that table and emits one row, so those eight are the FK — a verdict row cannot exist without the metric row that grounds it.
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
    evidence_strength: float  # 0~100 (§Verdict)
    single_source: bool  # was this verdict made on one source only. v1 (YouTube alone) is always true
    opportunity_score: float | None = None  # normalised 0-100 in the product group. NULL when unscored
    gap_pp: float | None = None  # comment composition - video composition (%p). A (topic, quarter) fact
    hold_reason: str = ""  # `판정 보류` 의 사유 코드. 보류가 아니면 '' (§판정 의 닫힌 어휘)


@dataclass(frozen=True)
class TopicQuarterEvidenceRow:  # → needs.topic_quarter_evidence (the speech under a verdict cell — §Evidence)
    # The first eight columns are topic_quarter_judgement's primary key as they stand. That the evidence
    # points at the verdict cell rather than the metric row — whoever asks for evidence read the type.
    run_id: int
    scope: str
    topic_key: str
    quarter: str
    source: str
    content_type: str
    panel_version: int
    panel_role: str
    rank: int  # the like-count descending slot inside that cell. From 1 with no gaps (§Evidence)
    snapshot_id: int  # the observation version the evidence document lives in. doc_id alone does not part it from a re-collection
    doc_id: str  # the body is not here — the corpus is canonical and the view topic_quarter_evidence_quote joins them
    like_count: int  # why it was chosen. A snapshot as of collected_at; a later count differs
    matched_term: str | None = None  # the term corpus_mention already recorded. No rematching here


# ---------- sensitivity (a counterfactual output. Stored in no table — §Sensitivity) ----------
@dataclass(frozen=True)
class PanelSensitivityRow:  # does the panel composition change the conclusion (ydc panel_sensitivity.py)
    source: str
    topic_key: str
    quarters_ok_product: int  # quarters whose mentions clear the sample gate — the product-only computation
    quarters_ok_all: int  # the same measured over all 43 channels (product+expert)
    delta_product_pp: float  # composition of the last 4 quarters − composition of the 4 before them (%p)
    delta_all_pp: float
    difference_pp: float  # the difference of the two deltas; the unrounded values are subtracted
    sample_ok: bool  # do the passing quarters exceed half the observed ones. If not it is not judged


@dataclass(frozen=True)
class BacktestRow:  # could it have been known then (ydc backtest.py)
    cutoff: str  # the quarter T under judgement. The metrics were recounted as if only up to the quarter after T were known
    source: str
    topic_key: str
    trend_type: str  # 방향이 있는 넷뿐이다 — 급상승·신규 등장·사라짐·단기 피크
    before_pp: float  # mean composition of the previous 4 quarters (basis A)
    before_excl_pp: float  # mean of the previous 4 quarters excluding T (basis B — did the level hold)
    after_pp: float  # mean of the 4 quarters after C
    at_cutoff_pp: float  # T 분기의 구성비. `단기 피크` 의 비교 상대다
    expected: str  # 상승 유지 | 하락 유지 | 피크 소멸
    actual: str  # 상승 | 하락 (기준 A 의 비교 결과)
    hit: bool  # basis A
    hit_level: bool  # basis B


@dataclass(frozen=True)
class AdSensitivityRow:  # is the conclusion the same with ads and sponsorships removed (ydc spam_ad_flags.py)
    variant: str  # ad_video | creator_comment | promo_comment | all_flagged
    source: str
    topic_key: str
    composition_base_pp: float  # composition of the last 4 quarters (the baseline)
    composition_kept_pp: float  # the same measured under that variant
    diff_pp: float
    judged_cells: int  # cells the baseline judged in that (source, topic)
    flipped_cells: int  # of those, cells whose type changed. Cells lost to the sample gate are not in it


# ---------- protocols ----------
class Linker(Protocol):
    version: str

    def link(self, unit: TextUnit, lexicon: Lexicon) -> list[EntityHit]: ...
    def match_products(self, products: Iterable[ProductRow]) -> ProductMatch: ...  # B2


class Extractor(Protocol):
    version: str

    def candidates(self, unit: TextUnit, aspects: AspectLexicon) -> list[Candidate]: ...
    def wishes(self, unit: TextUnit, lexicon: Lexicon) -> WishResult | None: ...


class Polarity(Protocol):  # ← the LLM insertion point. Rules and LLM share this signature
    version: str

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult: ...  # category is the lexicon_category (not the site's own string)
    def classify_many(
        self, items: Sequence[PolarityRequest], aspects: AspectLexicon
    ) -> list[PolarityResult]: ...  # only a batch-API implementation (#6) gains. Same length and order


class Aggregator(Protocol):
    version: str

    def need_metrics(
        self, mentions: Iterable[NeedMentionRow], denominators: Iterable[DenominatorRow], scope: str
    ) -> list[MetricsNeedRow]: ...
    def wish_metrics(self, wishes: Iterable[WishMentionRow], scope: str) -> list[MetricsWishRow]: ...


# ---------- evaluation ----------
@dataclass(frozen=True)
class LabeledRow:  # one needs.labeled_set row. The only input the eval harness passes on
    task: str
    ref: str
    split: str
    gold: str
    text: str
    extra: Mapping[str, object]  # the set name (`set`), rating, in_final and the other CSV columns


class Predictor(Protocol):  # an eval implementation. Batch in, labels in the same length and order
    def __call__(self, rows: Sequence[LabeledRow]) -> Sequence[str]: ...
```

## Formulas (the implementation follows these definitions)

- **population_share_pct** (`metrics_need`) = `100 * (low_mentioning / denom_low) * site_low_pct`
  - `low_mentioning` = the number of ≤2-star reviews mentioning this need_key among the products of that
    category whose low-rating rows are complete (`low_complete`)
  - `denom_low` = the sum of `low_collected` over the same product set · `denom_site` = the sum of
    `site_review_count` over the same set
  - `site_low_pct` = per product, `(review_stats.pct_1 + review_stats.pct_2) / 100` (the low-rating share
    the site reports). A `metrics_need` row is a category rather than a product, so that aggregate is not
    a plain mean over products but a **review-count weighted mean**: `Σ site_low_est / denom_site`, summed
    over the same `low_complete` product set as the two denominators above, where
    `site_low_est` = `round(site_review_count × site_low_pct)` is the per-product value
    (`product_denominator.site_low_est`). The numerator is the estimated total of ≤2-star reviews that set
    has on the site, and over a one-product set it collapses back to the per-product definition.
  - `low_share` = `low_mentioning / denom_low` (the share within the low-rating sample)
  - B7: the seed's `seed:slice-p1` rows were computed not by this formula but by a collection-sample
    approximation (`100 * low_mentioning / denom_site`). The second pass targets a difference of ±0.05
    between the two values, and it is not a golden.
- Every formula of the quarterly grain uses the **panel** as its denominator (`metrics_topic_quarter`, formats.md §Panel roster CSV).
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
- **The quarterly table's row set** — inside one (`run_id`, `scope`, `source`, `content_type`,
  `panel_version`, `panel_role`) this table is **a dense grid**: there is one row for each topic with
  `trend_use=true` (13 today) × every quarter existing in that computation, and a cell with 0 mentions
  becomes a row too (`mentions=0` · `composition=0` · `unique_ratio=1` ·
  `sample_ok=false`). `trend_use=false` 인 주제(`추천_재구매`·`선크림` — 각각 영상의 76%·93%를 쳐서
  판별력이 없다)는 필터·장르 표시로만 쓰이고 이 표에 행을 갖지 않는다. 그래서 두 불변식이 참이고, 뷰
  `needs.metrics_topic_quarter_violation`(`db/views/`)이 저장된 행에 대고 그것을 되묻는다 — 비어 있으면 참이다.
  1. The grid is dense: `count(*) = count(distinct topic_key) * count(distinct quarter)`.
  2. The denominator closes: a quarter's `sum(mentions)` is the `quarter_mentions` that all the rows of that
     quarter hold together.
  For someone running `SUM(mentions) GROUP BY quarter` over the stored table to be right, those two have to
  stand. Delete the 0-mention cells and the first breaks, and `persistence`'s baseline rises with it.
- **composition** (`metrics_topic_quarter`) = `mentions / quarter_mentions` — not a document-based share
  but **the composition across topics**. The median YouTuber description length fell from 1,253 to 709
  characters over three years, so a share loses only its numerator while the denominator stands and 10 of
  the 13 topics fall together (-28.6%p in total). A composition cancels because numerator and denominator
  shrink together. In a quarter where `quarter_mentions` is 0 it is `0`, not NULL.
- **velocity_yoy** (`metrics_topic_quarter`) = `ln(composition[q]) - ln(composition[same quarter last year])`.
  There are three conditions: the same quarter of the previous year has to be **a quarter that exists** in
  that computation (with none there is nothing to compare against), and **both quarters must have
  `mentions >= 5`**. Fail any one and it is NULL — a thin sample is not read as a surge. That the partner is
  the same quarter of the previous year is because of seasonality (formats.md §Time).
- **persistence** (`metrics_topic_quarter`) = the share of quarters in the window whose `composition`
  exceeded that topic's median over the whole period. The window is **at most 4 existing quarters ending
  at that row's quarter** (not the globally latest 4) and `window_quarters` is its length. The baseline's
  "whole period" is every quarter existing in that computation and **includes quarters with zero
  mentions**. Baseline and window are taken separately per `source`. So this value is **relative to the
  run** — a later run with more quarters legitimately gives a different value for the same quarter, and
  `run_id` being in the key keeps both. The verdict rules are written in counts, so
  `persist_quarters`·`window_quarters` keep the counts too — in an early quarter with a short window the
  counts cannot be recovered from the ratio alone.
- **unique_ratio** (`metrics_topic_quarter`) = `mentions / mentions including duplicates`. Within one
  video, comments identical after normalisation are counted once (copy-paste spam, measured at 1.1%),
  while duplicates across videos are not removed — the same words under different videos are each a real
  reaction. In a cell where the duplicate-inclusive mention count is 0 it is `1`, not NULL.
- **sample_ok** — `mentions >= 5`. The same number as the condition for emitting `velocity_yoy`, and
  022's CHECK enforces that equality. This column is NOT NULL, so without the definition a row would say
  something other than its own name.
- **channel_diffusion** (`metrics_topic_quarter`) =
  `0.5 * (panel channels that produced that topic / denom_channels) + 0.5 * normalised Shannon entropy (the per-channel mention distribution)`.
  Both terms use **the channel distribution taken from the videos** — how many videos per channel that
  quarter matched that topic. So this column does not depend on `source`, and the `youtube_comment` row
  of the same (topic, quarter) holds **the same value** as the `youtube_video` row. The entropy's
  normalising denominator is `ln(the number of channels in that distribution)`, not `denom_channels` —
  one channel monopolising gives 0, an even spread across those channels gives 1. When there is no panel
  video at all in that quarter the first term is 0. The neighbouring `channel_count` is **a different
  number**: the count of channels that produced that topic on that row's `source` (on a
  `youtube_comment` row, the channels of the videos the topic's comments hang on), and using it as the
  first term's numerator because the names look alike changes the comment rows' diffusion.
- **Stored decimal places** (the ratio columns of `metrics_topic_quarter`) — stored rounded to the decimal
  places below. The verdict thresholds (`TAU`·`DIFFUSION_TAU` in ydc `judge.py`) are numbers fitted on
  rounded values, so the decimal places are the resolution of that gate. 022 holds those places as
  `numeric(p,s)`, so the DDL enforces that storage keeps them.
  Decimal places: `composition` 5 · `velocity_yoy` 4 · `persistence` 3 · `unique_ratio` 4 · `channel_diffusion` 3.
- **like_cap_sum** (`metrics_wish`) = `sum(min(like_count, LIKE_CAP))`, **LIKE_CAP = 100** (A8: the slice has no cap, so the contract sets the constant). An implementation that uses no cap leaves this column NULL.
- **low_complete** (`product_denominator`) = `(low_collected < 150) or has_3star` — if a 3-star review is mixed into the RATING_ASC sample, or there are fewer than 150 at ≤2 stars, then the ≤2-star rows are complete. 150 is the collection sample ceiling (`REVIEW_PAGES 3 x 50`) and `collectors/commerce/scope.json` (#7) and `formats.md` hold the same value.

## `metrics_need`'s month rows (`month <> ''`, #129)

`month <> ''` rows exist **only as the category total (`product_ref = ''`)** — the product axis has
whole-period rows alone. A month row is remeasured from that month's mentions alone, and a value that
does not exist for that month is NULL rather than 0:
`low_share`·`population_share_pct`·`low_mentioning`·`denom_low`·`denom_site` are NULL because
`product_denominator` is a `captured_at` snapshot and there is no such thing as "that month's
denominator", and `persist_*` are NULL because `persist_months` is always 1 over a one-month
population and so means nothing. `unresolved_new` is NULL on a month row too, but for a different
reason — the implementation has not been given a product's first_seen yet, so it is NULL on the
whole-period row as well. `yt_neg`·`yt_pos` count only comments with
`observed_at_resolution = 'month'`, and if even one of that month's comments is otherwise both values
for that month are NULL (restored relative times pile into the one collection-basis month —
`formats.md`). A review's `neg`·`pos` are not filtered: that fallback is `day` resolution, so the
month is always right. The whole-period row (`month = ''`) is unaffected by this rule and counts every
comment — so the sum of the month rows' `yt_*` can be smaller than, or NULL against, the whole-period
row's `yt_*`.
## Verdict (the seven trend types and the two scores — `topic_quarter_judgement`, fork #40)

The verdict is **a derivation, not an aggregate**. Its input is not documents but all the
`metrics_topic_quarter` rows of one run, and its output is **1:1** with those rows (the same eight columns
are both key and FK). So this table has no line in "the canonical table per aggregate grain" of
`formats.md` §Time — the question that table closes is "where do I ask for **the mention count, the channel
count and the persistence** of this grain", and the verdict table carries none of the three. With no
counting column it has no rival to be canonical over. That its name does not start with `metrics_` is the
same sentence.

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
    On a `youtube_video` row the first two ratios happen to be the same number, and so **looking at videos
    alone hides this difference.** What parts is the comment rows.
  - The `unique_ratio` term is effectively a constant on this corpus (median 1.0, minimum 0.9939) — 25 points
    go into every cell alike. The term is not removed because a source with much reposting (NAVER, commerce)
    joining would give it discriminating power, and for now **the fact that there is no information** stays
    in the output.
  - Rounded to **1 decimal place** before storage. Both the `EVIDENCE_FLOOR` comparison and the
    `opportunity_score` term use that rounded value — the decimal places are the resolution of that gate (the
    same sentence as "stored decimal places" in §Formulas).
- **Verdict order** — caught higher up, it stops there. The order itself is the definition.
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

### Verdict constants (gathered in `analysis/judge` alone, and `tests/test_judge_constants.py` compares this table against it)
#3 등급 A 리뷰가 "저장된 값 위에서 맞춰진 산물" 이라고 넘긴 다섯이다. 아래는 **그 값이 무엇 위에서
나왔는가**와 **그대로 채택하는가 다시 맞추는가**의 답이고, 재현은 2026-08-26 포크 #40 이 원 산출
(`reports/trend_sunscreen_v0.2.csv` 338행 = 이 표의 338행, #5 가 셀 차이 0 으로 대조)에서 했다.

| 상수 | 값 | 무엇 위에서 나왔나 (재현 결과) | 판단 |
|---|---|---|---|
| `TAU` | `0.35` | 관측 `abs(velocity_yoy)` 분포의 75분위. **소스별로 따로 뽑아 거의 같은 값이 나온 것이 근거다** — 재현: 영상 76셀 중앙 0.215 · 75분위 **0.366** · 90분위 0.594 · 최대 0.887, 댓글 108셀 중앙 0.218 · 75분위 **0.357** · 90분위 0.526 · 최대 1.290. **분위는 이 표 전체가 아래 `DIFFUSION_TAU` 줄과 같은 `sorted(v)[int(q*n)]` 정의를 쓴다** — `statistics.median` 으로 세면 중앙이 0.207/0.216 이고, 두 수는 정의가 다른 두 답이지 반올림 차가 아니다(포크 #41 이 정정). 댓글 90분위는 TEAM_DECISIONS_v0.2 §3.1.1 이 0.525 로 적은 그 수다(실측 0.5257 — 표는 버림, 여기는 반올림이고 컷에는 닿지 않는다). 나머지는 그 표와 같은 수 | **그대로 채택.** 다시 맞춰도 0.357~0.366 으로 돌아오고, 둘을 하나로 내려 고정한 것이 팀 결정이다. **한 번만 뽑아 고정하는 것**이 이 값의 요점이다 — 매 산출마다 다시 뽑으면 조용한 분기에도 언제나 상위 25%가 `급상승` 이 된다 |
| `DIFFUSION_TAU` | `0.089` | 전년 동분기 행이 존재하는 **234셀**(13주제 × 9분기 × 2소스)의 `abs(Δchannel_diffusion)` 75분위. 재현: n=**234** · 중앙 **0.042** · 75분위 **0.089** · 90분위 **0.496** — `judge.py` 주석의 세 수와 자리까지 일치. 분위는 `sorted(v)[int(q*n)]` 로 뽑는다 | **그대로 채택.** 0 으로 두면(= "오르기만 하면 확산") 판정된 89셀 중 **52셀(58%)** 이 `채널 확산` 한 곳으로 몰려 분류의 정보량이 사라진다. 이 컷에서 52 → **14셀**(재현 일치). 소스가 늘면 그 소스의 분포에서 다시 뽑아야 한다 |
| `EVIDENCE_FLOOR` | `50.0` | **적합된 값이 아니다.** v1 에서 온 0~100 척도의 중간이고 TEAM_DECISIONS 는 값만 적는다. 이 코퍼스에서의 실측 결과: `evidence_strength` 중앙 59.95(`statistics.median`; 위 `TAU` 줄의 `sorted(v)[int(q*n)]` 정의로는 60.1 — 포크 #41 이 정정), 338셀 중 111셀(33%)이 `근거 부족` 이고 그중 **51셀은 이 컷 하나로만** 걸린다(`mentions < 5` 로만 걸리는 셀은 **0**). 감도: 40 → 73셀 · 50 → 111셀 · 60 → 156셀 | **재적합하지 않고 채택한다 — 맞출 정답이 없기 때문이다.** `backtest.csv` 11행은 이미 **판정된** 셀의 적중만 보고(`trend_type` 은 급상승·신규 등장·사라짐·단기 피크뿐) `근거 부족` 판정에 대해서는 아무 말도 하지 않는다. 근거가 "팀 합의" 하나뿐이라는 사실을 여기 적는 것이 이 줄의 일이다 |
| `MIN_DOCUMENTS` | `5` | **the same number** as the gate of `metrics_topic_quarter.sample_ok` (022's `CHECK (sample_ok = (mentions >= 5))`, the `velocity_yoy` condition in §Formulas) | Adopted but **not defined separately** — `analysis.trend.MIN_MENTIONS` is taken as it is. Measured, this gate alone filters 0 cells, so it is entirely hidden behind `EVIDENCE_FLOOR` |
| `NEW_TOPIC_MAX_SHARE` | `0.01` | TEAM_DECISIONS §3.2 의 "직전 3분기 구성비 < 1%". 적합값이 아니라 읽기 좋은 팀 합의 수다. 감도: 0.005 → 1셀 · 0.01 → 5셀 · 0.02 → 10셀 | 채택. 근거가 합의뿐이라는 것을 적는다 |
| `W_EVIDENCE` | `documents 43.75` · `channels 31.25` · `unique 25.0` | v1 의 4요소(근거 수 35 · 채널 25 · 비중복 20 · 제품·주제 매칭 신뢰도 20)에서 **`entity_link` 가 없어 계산할 수 없는 넷째를 빼고 남은 셋을 0.8 로 나눈 재정규화**다(35/.8 · 25/.8 · 20/.8). 산술이 곧 근거이고 테스트가 그 나눗셈을 검사한다 | 채택. 넷째 항을 0으로 깔면 모든 주제가 조용히 20점 깎여 `EVIDENCE_FLOOR` 가 오작동한다. `entity_link` 가 생기면 4요소 원안으로 되돌아간다 |
| `W_SCORE` | `velocity .35` · `persistence .25` · `channel_diffusion .20` · `evidence_strength .20` | **이 코퍼스에서 적합된 값이 아니다.** TEAM_DECISIONS_v0.2 §1 "v1에서 그대로 채택하는 것" 목록에 있는 v1 합의값이다 | 채택. 근거가 "v1 합의" 하나뿐임을 적는다 — 넷의 합이 1.0 이라는 것 말고는 이 값을 지지하는 실측이 없다 |

- 위 표를 **값만** 옮기는 것은 이 계약의 실패다. 각 줄의 3열이 없으면 "왜 0.35 인가"에 답할 자리가 없다.
- 판정 상수가 바뀌면 그것은 정의가 바뀐 것이므로 `analysis_run.versions.judgement` 를 올린다
  (`versioning.md`). ydc 는 같은 사실을 행마다 `tau`·`diffusion_tau` 컬럼으로 적는데, 이 레포는 A19 에
  따라 집계·파생 표에 `*_version` 컬럼을 두지 않으므로 그 자리가 run 이다.
- **Stored decimal places** (`topic_quarter_judgement`) — 024 holds those places as `numeric(p,s)`, so the
  DDL enforces that storage keeps them.
  Verdict decimal places: `evidence_strength` 1 · `opportunity_score` 1 · `gap_pp` 2

## Sensitivity (does the conclusion wobble — three answers that are not stored, fork #41)

The three (`panel_sensitivity` · `backtest` · `spam_ad_flags`) **make no metrics.** They re-run §Formulas and
§Verdict with only the population changed and measure whether that run's conclusion wobbles under that
choice. So the three come **after** §Verdict — there has to be a conclusion to wobble first.

**Nothing is written to any table.** The reason is vocabulary rather than discipline: the rows the three
measurements make belong to a counterfactual population, and neither `panel_role='product+expert'` nor "the
output with ad videos removed" nor "the output that knows only up to 2025Q2" has a place in 022's closed
vocabulary (`panel_role IN ('product','expert')`) or in `analysis_run`. Making a place is not within
additive-only scope but a change to what a stored ratio means, so the output is not a table but **an answer**
(the stdout of `cosmai trend sensitivity`). Writing nothing is what lets this command be run against the
production DB as it is.

**The baseline is recounted.** The stored `metrics_topic_quarter` rows are not used as they are because, for
a difference to mean anything, baseline and variant have to come out of **the same code path**. Instead the
recounted baseline is asked back against the stored rows, and on a difference that fact (`baseline_drift`)
comes first — every difference this command reports is meaningless then.

**The two windows are derived.** `the last 4 quarters` = the four calendar quarters **before** the last (in
progress) quarter, `the previous 4 quarters` = the four calendar quarters before those. ydc nailed these
eight down as values (`RECENT` = 2025Q3-2026Q2 · `PRIOR` = 2024Q3-2025Q2), but that is a constant tied to
this corpus, so here the same sentence is derived (on the 2026-08-19 corpus they are the same eight
quarters). **Counting by the calendar rather than by the index of the observed list** is the meaning — a
quarter whose row is missing for want of mentions still takes a slot of the window and enters as 0.

### Panel sensitivity (`PanelSensitivityRow` — ydc `panel_sensitivity.py`)
- What it asks: **does the panel composition change the conclusion.** The product-only-34-channel output and
  the all-43-channel (product+expert) output are run side by side and their two deltas (last-4-quarter
  composition − previous-4-quarter composition) compared.
- Measuring the choice itself rather than reclassifying individual channels is because ydc tried the
  reclassification and measured that **the two groups are not told apart by text metrics** (over the 20
  channels the team classified by hand, the ingredient-and-spec topic share was a median 17.7% for expert
  against 15.3% for product, and the highest value, 39.7%, is a product channel). This is the check the
  proposal's §4 demands with "when the conclusion changes greatly with the filter condition, mark it as a
  filter-sensitive signal".
- `sample_ok` = whether that topic's **passing quarters** (quarters whose mentions clear the sample gate of 5
  in §Formulas) are **more than half** of the observed quarters. ydc nailed this sentence down as `>= 7` on
  its 13-quarter output; here it is derived from the observed quarter count. A cell whose passing quarters
  are under half is not a verdict target to begin with, so its flips are not counted.
- **뒤집힘** = 판정 대상 셀 중 두 델타의 부호가 다르고 한쪽이라도 `MATERIAL_PP = 0.5`%p 만큼 움직인 것.
  폭을 요구하지 않으면 0 근처를 오가는 셀이 전부 뒤집힘으로 잡힌다 — 전량에서 실제로 한 셀
  (`youtube_comment` / `백탁`, −0.03 → +0.03)이 그렇게 잡힌다. 0.5 는 관측된 3년 변화량 범위(−5.5 ~ +2.6%p)에서
  눈에 보이는 최소 폭이다.

### Backtest (`BacktestRow` — ydc `backtest.py`)
- 묻는 것: 판정이 "지나고 보니 그랬다"가 아니라 **"그때 알 수 있었다"인가.** 과거 분기 C 까지만 알던 것처럼
  지표를 다시 세어 직전 분기 T 를 판정하고, C 이후 `HORIZON` 분기에 그 방향이 유지됐는지 본다.
- **T 가 아니라 C 로 자른다.** 판정은 마지막 분기를 `미확정(진행 중)` 으로 두므로(§판정 순서 1), T 를
  판정하려면 C = T 다음 분기까지 데이터가 있어야 한다. 운영도 그렇게 돈다.
- **The cut is the point.** `persistence`'s baseline is the whole-period median (§Formulas), so judging the
  past without cutting amounts to fixing the baseline by looking at quarters that have not arrived.
  `velocity_yoy` uses the same quarter of the previous year alone, so it leaks nothing.
- 방향이 있는 유형만 검증한다: `급상승`·`신규 등장`(상승 유지) · `사라짐`(하락 유지) · `단기 피크`(피크 소멸).
  `지속 인기`·`채널 확산` 은 방향 예측이 아니라 상태 서술이라 뺀다 — 넣으면 적중률이 부풀려진다.
- **기준을 두 개 낸다.** 기준 A 의 직전 구간에는 급상승한 분기 T 자체가 들어 있어 "T 보다 더 올라야 적중"이
  되고, 평균 회귀만으로 실패가 나온다. 기준 B(`before_excl_pp`)는 T 를 뺀 직전 구간과 비교해 **"올라간 수준이
  유지됐는가"**를 묻는다. 두 질문이 다르고, 둘 중 하나만 내면 결과를 고른 것이 된다. `단기 피크` 는 두 기준이
  같은 질문(T 분기보다 낮아졌는가)이 된다.
- **The base rate is emitted alongside.** Regardless of the verdict, what percentage of all cells rose is
  computed too (a cell whose before and after are both 0 is not counted). A hit rate no higher than the base
  rate means that verdict carries no information — a backtest that emits the hit rate alone is not
  verification but promotion.
- The horizon is a year (`HORIZON` = `LOOKBACK` = 4) because of seasonality: both the previous and the
  following window have to hold all four quarters for the summer effect to cancel. The same 4 as
  `persistence`'s window in §Formulas.

### Ad and sponsorship marking (`AdSensitivityRow` — ydc `spam_ad_flags.py`)
- What it asks: the two things the proposal's §4 demands — **mark** ads and sponsorships, and **confirm**
  whether the conclusion is the same with them removed. Do only the first and it becomes a column that is
  marked and read by nobody.
- The three things marked (the vocabulary is `variant`'s closed four: `ad_video` · `creator_comment` · `promo_comment` · `all_flagged`):
  | mark | what | from where |
  |---|---|---|
  | `ad_video` | an ad or sponsored video | the **union** of `source_metadata.has_paid_product_placement` (the uploader's own report) and a phrase in the description. The report has gaps (TEAM_DECISIONS §9), so over everything the measurement is 254 reported · **407** by phrase · 465 in union · 196 overlapping, and the **211 caught by phrase only** are invisible to the report field. The source's docstring writes 410 by phrase and 214 by phrase only, and those numbers **do not reproduce in any state** — fork #41 ran that file's birth commit (`9fd7ec0`; neither `AD_RE` nor the population definition changed after it) over the same two runs and got 407 back. The 196 overlap written alongside is the number that agrees with 407 (254+407−465). What is stale is 410/214 |
  | `creator_comment` | a comment by the channel's own operator | `source_metadata.author_channel_hash` = `sha256("youtube:" + channel_id)[:24]`, so it can be rebuilt from a channel id. It is an exact match, not an estimate. An operator's pinned comment is closer to a copy of the description, so leaving it in the comment series **breaks the definition of that series as consumer reaction** |
  | `promo_comment` | a sales-link, group-buy or market-notice comment | a regular expression. Operator comments come **first**, so one document never falls into both sets. **At bundle grain they can overlap** — when an operator's copy and someone else's copy share the same (parent video, text), that bundle carries both marks (the same behaviour as ydc, which is why `all_flagged`'s exclusion set can be smaller than the sum of the two sets) |
- **버린 규칙**: 전화번호 정규식(6건)과 도박·대출 사전(4건)은 재보니 걸린 것이 거의 전부 오검출이었다
  (`토토톡` · `40대출산맘` · `무향`). 0.01%를 잡으려고 오검출을 남기지 않는다. 흔한 스팸 유형(도박·리딩방)이 이
  패널에는 없다.
- **Exclusion is at (parent video, normalised text) bundle grain.** Remove only one side of a copy-paste and
  `unique_ratio`'s numerator and denominator count different populations (corpus rule 9's
  `duplicate_in_parent` is the rest of that bundle). When a video drops out its comments drop out with it —
  because quarter attribution is the parent video's (rule 3).
- **Lost cells and flipped cells are kept apart.** Mix the verdicts lost to a shrunk sample (`lost`) with
  the ones whose type changed (`flipped_cells`) and it looks like "the exclusion changed every conclusion",
  when in truth most of it is a sample falling short.

### Sensitivity constants (gathered in `analysis/sensitivity` alone)
| constant | value | what it came out of | judgment |
|---|---|---|---|
| `MATERIAL_PP` | `0.5` | 관측된 3년 변화량 범위(−5.5 ~ +2.6%p)에서 눈에 보이는 최소 폭. 적합값이 아니라 읽기 기준이다 | 채택. 0 으로 두면 전량에서 `백탁` 댓글 셀(−0.03 → +0.03)이 뒤집힘으로 잡혀 답이 뒤집힌다 |
| `HORIZON` · `LOOKBACK` | `4` · `4` | seasonality. The same number as `persistence`'s window (`WINDOW_QUARTERS`) in §Formulas, and it is taken from there rather than defined separately | Adopted. Under 4, the previous and following windows hold the summer on one side only |
| the verdict-target gate | **more than half** of the observed quarters | ydc nailed it down as `>= 7` on its 13-quarter output. Deriving the same sentence from the observed quarter count gives 7 at 13 quarters | Adopted as a derivation. Nailed down as a value it quietly loosens on an output with more quarters |
| `MIN_MENTIONS` | `5` | **the same number** as the sample gate in §Formulas | Not defined separately — `analysis.trend.MIN_MENTIONS` is taken as it is |

- **The two branches the sample golden could not see** (a cell where `sample_ok` stands · a promo comment)
  were asserted absent by `tests/test_sensitivity_golden.py`, so that line broke first the day the sample
  changed. #57 re-cut the sample (product channels 4 → 11) and both branches are live — the flip verdict
  runs and reaches a cell, and promo rows move — with the seven goldens (#5 · #40 · #41's three · #6's two)
  regenerated at once by `tool/measure-trend-sample`.
- **CI cannot hold the full comparison** (the 261,317 documents are in `archive/` and it is read-only). The
  procedure and the comparison code are in one place, `tool/compare-ydc-sensitivity` — it runs ydc's three
  scripts untouched, makes three outputs and compares them row by row. Run 2026-08-26: panel 26 rows ·
  backtest 11 rows · marking 104 rows, **difference 0**.

## Evidence (the consumer speech that holds up a verdict cell — `topic_quarter_evidence`, fork #6)

Evidence is neither an aggregate nor a derivation but **a pointer**. If the verdict makes one row out of one
metric row (§Verdict), evidence points back at a few of the documents that made that cell — not copying the
body is what that means, and the view `needs.topic_quarter_evidence_quote` joins a cell to the original text.
**Reaching from one cell of the verdict grid to the evidence's original text without joining by hand** is
this issue's completion criterion, and that one view is it.

**That one line carries `run_id`.** The view does not filter by run, so with more than one run under a single
snapshot and roster the same cell comes out twice and `rank` stops being 1..n. The canonical filter is one of
two — name the `run_id`, or find that snapshot and roster's run through `analysis_run.note` and filter on it
(`note_of` in `analysis/trend/pipeline.py`, the very path the three pipelines use). When a screen wants "the
latest run", the `run_id` chosen by that note is the latest.

- **The population is the same as the metrics' and the verdict's.** `analysis/evidence/pipeline.py` does not
  write the population again; it takes the `POPULATION` CTE of `analysis/trend/pipeline.py` as it is. Written
  again, the speech a card quotes and the numbers written on that card would stand on different denominators,
  and once parted both look plausible enough to stay invisible.
- **Four selection rules** (they are ydc `evidence_comments.py`'s rules, and all four apply rather than in
  order):
  1. The candidates are the documents that cell's `source` produced with `quality_flags = ''`. An empty body
     (`empty_text`) and a copy-paste inside the same video (`duplicate_in_parent`) are not quoted — the
     metrics count the latter in `unique_ratio`'s denominator (§Formulas), but counting is one job and
     quoting is another.
  2. **The creator's own comments are dropped.** The author is the creator when the hash of the
     video's channel (`sha256('youtube:' || channel_id)`, first 24 characters — the collector's own rule
     for hashing the author channel id) equals `source_metadata.author_channel_hash`. Most top-liked
     comments are pinned ones (timelines, greetings, summaries), not consumer speech. Of the fixture's
     719 candidate comments, **21** are dropped here (41 of 1216 candidate pairs).
  3. The topic is the one `corpus_mention` already attached; the body is not rematched. `matched_term` is
     that row's value too — rematch and the mentions the metrics counted and the mentions the evidence chose
     would stand on different rules.
  4. The order is `like_count` descending and **ties break on `doc_id`**. The second key is a contract
     because ydc leaned on CSV read order (Python's sort is stable, so file order decided the winner of a
     tie). A stored table must yield the same rows on a rerun, so that seat cannot be left empty: of the
     96 fixture cells that carry evidence, **71** have a tie (a tie is a cell with **two or more
     candidates on the same like count**, not merely a cell with two or more candidates), and with the
     second key **57** of the 251 rows pick a different document. What does not change is the like ladder.
  - The per-cell ceiling is `TOP_PER_CELL = 3`. It is the number that goes into one card, so this rather than
    025's CHECK is its place — the DDL is additive only, so a ceiling once written cannot be taken back.
    **That number must have this one place**: if a card held its own quotation ceiling, adding evidence alone
    would leave the card at three. `analysis/cards.build`'s default imports this constant, and
    `tests/test_cards_rules.py` checks that the two names are the same object.
- **The fixture numbers of this section have a way to be measured.** `tool/measure-evidence-fixture`
  re-measures them from the corpus CSVs (`--json`), and `tests/test_evidence_numbers.py` compares that value
  against these sentences and against the DB output of `analysis/evidence/pipeline.py` together — when the
  fixture grows, the test goes red first. Write a number down and leave no way to measure it and that number
  quietly becomes false (the same place as #41's `tool/compare-ydc-sensitivity`).
- **Full measurement** (2026-08-26 · throwaway container · 261,317 documents · 105,358 mentions): candidates
  **15,602** rows · evidence **480** rows · cells **163** · `topic_quarter_evidence_violation` **0 rows** ·
  the same numbers on a second run (idempotent). The candidate query is **178ms**
  (`EXPLAIN (ANALYZE, BUFFERS)`; it rides 023's partial index
  `corpus_document (snapshot_id, parent_item_id) WHERE content_type='comment'`), and the whole of
  `cosmai trend evidence` is **0.52s** including process start, with a peak resident of **73MB**. That is
  because it pulls pointers and like counts rather than carrying the body, and without these numbers "it is
  light" is an assertion never measured.
- **The axis is the verdict grid.** Topics with `trend_use = false` (the sunscreen and
  repurchase-recommendation topics) have no verdict cell, so no evidence either. ydc produced 313 rows over
  15 topics; only **251 rows** over 13 topics have a seat in this table.
- **The like count stays on the row.** It is a snapshot as of collected_at (§Limitations of the population),
  so counting again later gives a different number — and then the stored value is the only thing that can
  explain this ordering.

### `cosmai retrieval search` does not replace it (that answer and its measurement — the answer fork #11 waited for)
Choosing evidence looks like search, but it parts from it in three places.
- **Population**: `retrieval_chunk`'s source is the collector schemas (`tubedepth`·`trend_radar`,
  `analysis/retrieval/corpus.py`), while the metrics' and the verdict's source is the 2026-08-19 observation
  version of `needs.corpus_*`. There is no guarantee that a comment search returned is a document inside that
  cell's denominator, and evidence with no guarantee is not evidence.
- **Unit**: search answers with a `chunk_id`; the unit of evidence is the document. A comment over 500
  characters is split into pieces (30 of the fixture's 2605 population comments).
- **Order**: search lines them up by lexical similarity to the query, evidence by like count. And "which
  documents spoke of this topic" has already been answered by `corpus_mention`, so running search again is
  making a second answer to the same question **by a different rule**.

**This table is not the same footing as §Retrieval measurements.** The axis (query = one alias, gold at topic
grain) is the same as `retrieval eval`'s literal mode, but the corpus differs — not the 381,950 chunks of
every source but **the comments of that verdict population** alone. On a small corpus P@10 acquires a ceiling
the corpus sets: on a topic with 3 correct answers no engine can pass P@10 0.3. So the ceiling is always
written alongside below. Copied without its ceiling it sits beside the all-source `.864` and reads as
"BM25 is weak", which is not what this table says.

Measured (2026-09-04 on the #57 re-cut, first measured 2026-08-27 · topic lexicon **v3** · the fixture's
population comments 2605 = 2646 chunks · the queries are the 63 topic aliases · the index of
`analysis/retrieval/bm25` and the scoring of `analysis/retrieval/eval`, gold from `corpus_mention`. The way
to re-measure is `tool/measure-evidence-fixture`, and `tests/test_evidence_numbers.py` holds this table to it):

| what is measured | value |
|---|---|
| How far BM25's top-10 overlaps `corpus_mention`'s answer | P@10 **.770** (this corpus's ceiling **1.000**) · MRR@10 .771 · Hit@10 84.1% |
| Share of the 251 chosen evidence rows inside the top-10 of **any alias** of their topic | **103/251 = 41.0%** |

The second row is this section's answer — a number with no ceiling, and it says that swapping search in
would lose three evidence rows in five. That is under the generous condition of the alias union (up to
10 queries × 10 hits per topic); narrowed to one top-10 it goes lower still. The first row is there to
show that the second does not come from a broken engine: BM25 took 77% of the ceiling, and the rest is
that `corpus_mention` has already answered "which documents speak of this topic" by a different rule.

**엔진 선택**: 이 용도는 셋 중 **하나도 쓰지 않는다.** 답을 이미 `corpus_mention` 이 갖고 있어서 고를
순위가 없다. S6 이 #11 에 주는 것은 기본 엔진의 근거가 아니라 그 반대다: **근거 수집이라는 구체적 용도가
검색의 순위를 필요로 하지 않는다.** 그래서 **위 두 줄은 #11 의 기본 엔진 판단에 입력으로 쓰지 않는다** —
그 판단의 입력은 전 소스에서 같은 자로 잰 §검색 실측 여섯 줄이다. 검색이 자기 자리를 갖는 것은 사전에 없는
말(`백탁` 이 아니라 `허옇게 떠요`)로 코퍼스를 뒤질 때이고, 그 자리는 heldout 모드이며 벡터가 유일하게 0 을
넘은 자리다(§검색 실측).

### Opportunity cards (the rules decide the type — `cosmai trend cards`, ydc `cards.py`)
- **Zero cards is not a failure.** It is the normally computed answer after every rule has run, and in
  this sample too 9 of 13 quarters have none. Why the exit code does not say so, and the one case that
  becomes `partial(1)` instead (**a cell the rules caught but with no evidence text to stand a card on**),
  are in the evidence-and-cards section of `entrypoints.md` — the same seat #41 pinned in the sensitivity
  section with "shaking is not a 1".
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

## Crosscheck (the sources put side by side — three answers that are not stored, fork #7)

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

### Composition (`SourceShare` — ydc `source_composition.py`)
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

### Rating (`RatingRow` — ydc `commerce_crosscheck.py`)
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

### Ingredients (`IngredientRow` — the ingredient axis of ydc `cross_source.py`)
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

### Crosscheck constants (gathered in `analysis/crosscheck` alone)
| 상수 | 값 | 무엇 위에서 나왔나 | 판단 |
|---|---|---|---|
| `MIN_PRODUCTS` | `5` | **the same number** as the sample gate in §Formulas — requiring 5 for the verdict while making an exception for the crosscheck alone is a double standard | Adopted. The same number as `analysis.trend.MIN_MENTIONS`, but it carries its own name because what it counts is products rather than documents |
| `LEAD_PP` | `5.0` | ydc `source_composition.reading` 의 세 갈래가 전부 이 폭이다 | 채택. 우리 실측에서 `백탁`(커머스 9.80 대 자막 3.32)·`지속력_워터프루프`(1.10 대 6.36)가 이 폭으로 갈린다 |
| `THIN_PP` | `2.0` | 같은 규칙의 "영상 설명으로는 관측 불가" 문턱 | 채택 |
| `SPARSE_PP` · `TALK_RATIO` | `0.5` · `3` | ydc `cross_source.topic_table` 의 "영상은 안 다루는데 댓글·리뷰에는 있음" | 채택. 우리 표에서는 한 주제도 걸리지 않는다 — 자막이 설명보다 두껍기 때문이고, 규칙을 지우면 원천이 바뀌는 날 되살릴 근거가 없다 |
| `SUN_SHARE_LOW` | `25` | 선크림 문맥 비율이 이 아래면 그 수를 "선크림 담론" 으로 읽지 말라고 말한다. 우리 실측 PDRN 149/933(16.0%, **문서**)이 이 아래다. ydc 의 187/1,522(12.3%)는 **청크**를 센 값이라 같은 자 위에 있지 않다 — 방향이 같다는 것만 말한다 | 채택. 적합값이 아니라 읽기 기준이다 — 4분의 1 은 "대체로 선크림 얘기다" 라고 말할 수 있는 최소선이고, 전량에서 열 성분이 **전부** 이 아래라 이 열은 지금 경고 하나를 뜻한다 |
| `POSITIVE_RATE_HIGH` | `80` | ydc `commerce_crosscheck.reading` 의 만족도 문턱 | 채택. 우리 실측 두 셀(`발림성` 71.7% · `자극_눈시림` 74.5%)이 둘 다 이 아래다 |
| `GAP_PP_MATERIAL` | `1.0` | the same rule's threshold for the comments saying much more (`gap_pp` = comment composition − video composition, §Verdict) | Adopted |
| `FORMULA_HOLD` · `PAPER_HOLD` | `True` · `True` | 위 두 줄의 모집단 근거 | 채택. 켜는 조건은 각각 #10 의 성분 데이터셋과 검증 프로토콜이다 |

### Full measurement (2026-08-27, production DB read-only)
- **이 값들은 주제 사전 v2 위의 기록이다**(포크 #56). v3 의 델타는 `파데프리` 하나다 —
  `톤업_메이크업베이스` 가 +636(+14.6%)이 되고, `선크림` 의 다섯 표기는 `trend_use=false` 라, `속건조` 는
  `new` 0 이라 이 절에 영향이 없다. 그래서 v3 를 켠 뒤 다시 재야 하는 것은 그 주제를 분모로 쓰는 구성비
  두 줄(`백탁` 커머스 9.80% 대 댓글 1.55%)과 `LEAD_PP`·`SPARSE_PP` 예시이고, 나머지 줄은 그대로다.
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

### Comparison against ydc (run 2026-08-27, 38 lines, **difference 0**)
**The promoted source is not the import pin** (`v0.4.0` `76db718`, `versioning.md`) — `cross_source.py` had its
ingredient keys and its sunscreen context corrected in `v0.3.0` (`e5a1b00`), and that revision is what was
promoted. The pinned copy (`analysis/slices/ydc/`, `v0.1.0`) never held that file and #9 deleted the copy — the
procedure that takes the file out of the tag and runs it **untouched**, and the comparison code, sit in one
place, `tool/compare-ydc-crosscheck`. Three things are put side by side:
- **상수** 열(키 표 · `SUN_WORDS` · `PAPER_HOLD` · `GROUP_MAP` · 극성 힌트 둘 · `MIN_PRODUCTS` · 해석 문구
  셋)을 ydc 모듈에서 직접 읽어 비교한다 — 옮겨 적다 어긋난 한 글자가 여기서 걸린다.
- **규칙** 열여덟(`ranks` · `positive_rate` · `polarity` 다섯 · 평가 해석 넷 · `count_terms` 넷)에 같은
  입력을 먹여 답을 비교한다. `count_terms` 가 든 것은 담론 매칭이 성분명 매칭에서 갈라졌기 때문이고
  (§성분), 그 갈래는 상수 비교로는 보이지 않는다.
- `cross_source.topic_table` 이 **함수 안에 리터럴로** 들고 있는 셋(`SPARSE_PP` 0.5 · `TALK_RATIO` 3 ·
  `READ_COMMENT_ONLY` 문구)은 함수를 부를 수 없어 그 소스를 읽어 맞댄다. 안 그러면 "차이 0" 이 이 셋을
  덮지 않는데 덮는 것처럼 읽힌다.
- **`polarity` is compared without being handed a group** — our canonical form is the table a person
  confirmed (§Rating) and ydc has no such table. An answer with no group is an answer riding on hints alone,
  so at that place the two are the same.
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

## Holdout (asking again with a new sample — an answer that is not stored, fork #51)

ydc `holdout_commerce.py` 승격분. **새로 쌓인 커머스 리뷰로 기존 결론을 검증한다 — 숫자를 갈아치우지
않는다.** 같은 코드로 **한 번도 안 본 리뷰만** 세서 기존 비율이 재현되는지 본다. 재현되면 결론이 표본에
얹혀 있지 않다는 뜻이고, 안 되면 그것이 더 중요한 발견이다. §민감도 가 패널·구간·광고 표시로 흔들고
§대조 가 소스를 나란히 놓는 자리에서, 이것은 **표본**으로 흔든다 — 셋이 같은 자리에 서야 "판정을 믿는
근거"가 한 벌이 된다.

**어느 표에도 쓰지 않는다. 이 승격의 DDL 은 0장이다.** §대조 가 든 이유(입자와 최신 편향) 위에 하나가
더 있다: 이 답의 행은 (팔, 주제)가 키인데 **`팔` 의 경계가 우리 청크 색인이다.** 그 경계는 `cosmai
retrieval chunk` 가 한 번 돌 때마다 움직이고(오늘의 홀드아웃이 내일의 기존이다), 값은 함께 읽은 기존 팔
옆에서만 뜻을 갖는다. 저장하면 움직이는 경계를 한 시점에 얼려 놓고 그 사실이 행에 안 실린다. 그래서
산출은 표가 아니라 **답**이고(`cosmai trend holdout` 의 stdout), 읽기 전용이라 운영 DB 에 그대로 돌린다.

### What splits the two arms is not a date but the chunk index
ydc 는 `captured_at < 2026-08-24` 라는 **손으로 고른 컷오프**로 갈랐다. 그 레포에는 "우리가 무엇을
봤는가" 가 행으로 남아 있지 않아서다(분석 입력이 CSV 한 장이었다). 우리에게는 그 행이 있다 —
`needs.retrieval_chunk(source='commerce_review')` 의 `doc_id` 가 곧 **분석이 실제로 본 리뷰의 명부**다.
그래서 우리 컷오프는 날짜가 아니라 그 명부다:

| 팔 | 무엇 | 왜 |
|---|---|---|
| `seen` (기존) | `doc_id` 가 청크 색인에 있는 리뷰 | 청크 색인 위에 선 우리 산출(§대조 의 구성·성분, BM25 색인)이 실제로 센 그 리뷰들이다 |
| `holdout` | 같은 모집단인데 청크 색인에 없는 리뷰 | **한 번도 안 본 리뷰.** 수집기가 그 뒤로 쓴 것이다 |

There are two places where this is better than a date cutoff. **There is no argument** — two ways of choosing
makes two denominators (the same convention as §Rating not taking a quarter as an argument). And `captured_at`
is **the collection time, not the time we saw it**, so a review collected before the cutoff whose chunk was
baked after it falls quietly into the seen arm under a date rule.
`captured_at` 은 대신 **답의 일부로 실린다** — 아래 §창.

**모집단은 §대조 와 같은 술어다**: `SUN_BOARD`·`SUN_CATEGORY` 가 정한 선케어 랭킹 제품의 리뷰. 두 팔이
같은 술어 위에 서야 차이가 표본의 것이지 필터의 것이 아니다. **본문이 빈 리뷰는 두 팔 모두에서 뺀다** —
빈 본문은 청크를 만들지 않으므로 그것을 남기면 "안 본 리뷰" 가 아니라 "볼 것이 없는 리뷰" 가 홀드아웃을
채운다. 뺀 수는 `note` 가 센다.

### The extraction rules — where the stop, the total order and the row-count comparison land
ydc 는 2026-08-23 에 **정렬 없는 페이징으로 "중복 37%" 라는 없는 결과**를 만들었고, 그 뒤 세 가지를 손으로
얹었다(`count=exact` → 전순서 정렬 페이징 → 행수·유일성 대조). **우리는 그 셋을 손으로 하지 않는다 —
자리가 다르기 때문이다.** 없는 방어를 있다고 적지 않기 위해 어디가 지는지 적는다:

| ydc 가 손으로 한 것 | 우리 자리 | 그래서 |
|---|---|---|
| `Prefer: count=exact` 뒤 행수 대조 | **한 트랜잭션 스냅샷**(`REPEATABLE READ`) | 네 읽기가 같은 시점을 본다. 세어 놓고 다시 세는 것은 이 자리에서 항등식이라 검사가 아니다 |
| `order=captured_at.asc,review_key.asc` | 페이징이 없다 (질의 하나) | 정렬이 페이지 경계를 고정하는 규칙인데 경계가 없다 |
| `review_key` 유일성 | `review_pkey (source, review_key)` | DB 제약이다. 다시 묻는 것은 항등식이다 |

**대신 이 자리에만 있는 함정을 하나 진다.** 청크는 원천의 파생물이고 외래키가 없다(020 의 주석). 원천
리뷰 행이 사라져도 그 청크는 남으므로, **원천에 없는 커머스 청크**(`chunk_orphan`)가 있으면 기존 팔은
분석이 실제로 본 그 팔이 아니다 — 그때 이 산출은 믿을 것이 못 되고 종료 코드 **1** 이다(§entrypoints).
수집기가 리뷰를 지우지 않으므로 평상 상태는 0이다.

**네 읽기(청크 명부 · 리뷰 키 명부 · 빈 본문 수 · 모집단)가 한 시점을 보는 것이 이 절의 유일한 기계
방어다.** 수집기와 청커는 계속 도는 중이라, 밖에 두면 팔의 크기와 뺀 빈 본문 수와 고아 청크 수가 서로
다른 모집단의 수가 된다 — 그때 `seen + holdout + empty` 는 **어떤 모집단의 크기도 아니다.**
`tests/test_holdout_pipeline.py` 가 두 읽기 사이에 다른 커넥션이 커밋한 리뷰가 어느 팔에도 들지 않는
것으로 그것을 검사한다 — 격리 수준을 낮추면 그 테스트가 빨개진다.

**스냅샷이 덮지 않는 읽기가 하나 있다**: 활성 주제 사전(`use_active`)은 그 앞에서 따로 읽는다(§대조 도
같은 모양이다). 사전은 `cosmai lexicon activate` 로만 바뀌고 수집기·청커와 달리 저절로 돌지 않으므로
같이 묶지 않았지만, "네 읽기가 한 시점을 본다"가 **다섯째 읽기까지 덮지는 않는다**는 사실은 여기 적어
둔다 — 활성 판본이 이 명령 도중에 바뀌면 두 팔이 다른 사전으로 매칭된 것이 아니라(사전은 한 번 읽어
둘 다에 쓴다) 표가 어느 판본의 것인지가 `note` 에 없다는 뜻이다.

### Metrics (`TopicRow`) — there are two denominators and they are not mixed
같은 사전(`analysis.retrieval.topics.match_topics`, `trend_use` 주제)·같은 단위(**리뷰 1건**)로 두 팔을
센다. 한 리뷰에 같은 주제가 여러 표기로 나와도 한 번이다(ydc `rates` 의 규칙). 분모는 **둘 다 싣되 차를
분모를 넘어 내지 않는다**:

| 열 | 분모 | 무엇에 답하나 |
|---|---|---|
| `rate` (언급률) | **그 팔의 리뷰 수** | ydc 의 축이다. 수준이 통째로 움직였는가 |
| `share` (composition) | **that arm's sum of `trend_use` topic mentions** | the axis of §Composition. Did the place among the topics move |

둘이 갈릴 수 있고 그 갈림이 답의 일부다 — 모든 주제의 언급률이 함께 오르면 구성비는 그대로다(수집이
바뀐 것), 한 주제만 오르면 둘 다 움직인다(그 말이 실제로 는 것). **판정은 ydc 의 축(언급률)에서 한다**
— 승격이 답을 바꾸지 않았다는 말이 서려면 같은 축이어야 한다.

**구성비 축의 재현 수는 싣지 않는다 — 그 수는 안정성이 아니라 눈금을 잰다.** `share == rate / scale`
이다(`scale` = 그 팔의 리뷰당 주제 언급 수). 계수가 1보다 크면 같은 `MATERIAL_PP` 가 구성비 축에서
**체계적으로 헐거워지고**(작으면 빡빡해진다), 그 값은 모집단마다 다르므로 어느 쪽인지 단언하지 않고
계수를 찍는다. 전량 실측(2026-08-27)에서 계수는 기존 **1.4218**(9,027/6,349) · 홀드
**1.3405**(1,307/975)였고, 두 축의 재현 수가 7/13 대 10/13 으로 갈렸다. 갈린 셋이 정확히 눈금이 나눈
셋이다 — 방향도 크기도 그대로인데 1.4 로 나뉘어 문턱을 못 넘었다:

| 주제 | Δ언급률(%p) | Δ구성비(%p) |
|---|---|---|
| `발림성` | −2.20 | −0.78 |
| `톤업_메이크업베이스` | −2.01 | −0.95 |
| `무기자차` | −1.61 | −1.01 |

그래서 **10/13 을 7/13 의 독립 근거로 읽으면 안 된다.** `note` 는 재현 수를 판정 축 하나만 싣고
(`reproduced=`), 대신 그 눈금 자체를 싣는다(`scale=1.42→1.34`). 축의 차이는 표가 행마다 `Δ률%p` 와
`Δ비%p` 를 나란히 놓아 보인다 — **두 수를 빼지도 두 축의 재현 수를 비교하지도 않는다.** 구성비 축의
문턱을 따로 고르는 것은 그 축 위에서 다시 적합해야 하는 일이고, 여기서 하지 않는다.

**순위에는 게이트가 있다.** 기존 팔의 문서 수가 `MIN_MENTIONS`(5) 미만인 주제는 순위를 갖지 않는다
(`None`). 게이트가 **기존 팔**에 걸리는 것이 뜻이다 — 홀드아웃에 걸면 새 표본이 얇다는 이유로 기존 순위가
사라져 물음이 거꾸로 선다. 전량 실측(2026-08-27)에서는 13주제가 전부 통과해(`topics=13 ranked=13`) 이
게이트가 **한 번도 걸리지 않았다**; 그래도 두는 것은 코퍼스가 아니라 랭킹이 모집단을 정하므로 선케어
보드가 좁아지는 날 기존 팔의 꼬리도 얇아지기 때문이다. **꼬리가 0으로 묶이는 것은 홀드 팔의 사실이고**,
그 자리는 게이트가 아니라 아래 `RANK_TOP` 이 진다.

### Why they part when they part — the three branches are each measured
비율이 통째로 움직였을 때 ydc 가 실제로 밟은 순서다. **원인을 셋으로 갈라 각각 재고, 답이 남는 것을 본다.**

- **창** (`window_reading`) — 홀드아웃이 **새 기간**인가 **같은 창이 길어진 것**인가. 두 팔의 `captured_at`
  최소·최대를 싣고, 홀드아웃의 시작이 기존의 끝 이후면 새 기간, 겹치면 같은 창이 길어진 것이다. ydc 가
  "늘어난 것도 새 기간이 아니라 같은 창이 하루 반 길어진 것뿐"이라고 적은 그 물음이고, 우리 쪽은 그것을
  선언하지 않고 **읽어서 답한다**.
- **플랫폼 구성** (`PlatformRow` · `StandardRow`) — 수준이 통째로 오르는 두 번째 원인은 플랫폼 구성이
  바뀐 것이다. 플랫폼별로 갈라 세고 **기존 팔의 구성비로 홀드아웃을 재가중한다** — 구성 효과를 뺀
  값(`standardized`)과 기존 값의 차(`residual_pp`)가 남는 것이 실제 변화다. 가중치는 **기존 팔에 있는
  플랫폼**의 것이므로 홀드아웃에만 있는 플랫폼은 이 가중합에 들지 않는다(그 사실은 `PlatformRow` 가
  0으로 싣는다).
  **오늘 이 모집단에서 그 기제는 놀고 있다**(2026-08-27 실측): 두 팔의 플랫폼은 `oliveyoung`·`glowpick`
  둘뿐이고 `daisomall` 은 리뷰 6,769건이 있어도 **선케어 랭킹 제품이 0개**라 표에 아예 안 선다. ydc 가
  이 절을 쓴 이유(다이소몰 비중이 흔들려 전체가 따라 움직인 것)는 **우리 원천에서 재현되지 않았다** —
  그쪽은 이름으로 모집단을 골랐고 우리는 랭킹으로 고르기 때문이다. 그래서 잔차가 원값과 거의 같은 것이
  이 표의 답이고, 열을 지우지 않는 것은 랭킹에 다이소몰 선케어가 오르는 날 이 기제가 되살아나기 때문이다.
- **제품 바스켓** (`BasketRow`) — **이것이 ydc 에서 실제 원인이었다.** 수집기가 그 주에 긁은 제품이 달랐다
  (기존 창 48제품, 다음 창 25제품, 교집합 14제품). 두 팔의 `product_key` 집합과 교집합을 싣고, **같은
  제품만으로 다시 세어** 바스켓 효과를 뺀 차를 낸다. 이것은 `수집 상한 10` 과 같은 계열이다 — **수집
  과정이 관측값을 만든다.**

### Verdict (`verdict`) — all four branches are exit code 0
ydc `holdout_commerce.report` 의 세 갈래를 그대로 들고 넷째를 우리 축이 더한다. 기준은 **언급률**이다.
- `재현` — 모든 게이트 통과 주제의 `|Δrate|` 가 `MATERIAL_PP` 이하. 수준도 순위도 재현된다.
- `순위 재현` — 상위 `RANK_TOP` 이 그대로인데 수준이 움직였다. **우리 결론은 순위를 쓰므로 유지된다** —
  왜 움직였는지는 위 세 갈래가 답한다.
- `순위 변동` — 상위 `RANK_TOP` 이 갈렸다. **결론을 다시 봐야 한다.**
- `순위 없음` — 게이트를 넘은 주제가 하나도 없다. **ydc 에 없는 갈래다**(다섯 주제가 전부 값을 가졌다).
  상위 비교가 공회전하는 것을 `재현` 이라고 부르면 없는 근거로 재현을 주장하게 된다.

**갈래를 묻는 순서는 ydc 의 것이다: 수준이 먼저다.** `|Δrate|` 가 전부 `MATERIAL_PP` 안이면 순위를
되묻지 않는다 — 1.5%p 안에서 순서가 바뀌는 것은 표본 흔들림이라는 것이 그 문턱의 뜻이고, 거기서 순위를
또 물으면 문턱이 두 번 서게 된다.

**재현 실패는 발견이지 실패가 아니다.** 넷 다 종료 코드 0 이고, 어느 갈래인지는 `note` 와 표가 싣는다 —
§민감도 의 "흔들린다는 1 이 아니다", §대조 의 "어긋난다는 1 이 아니다"와 **같은 자리, 같은 문장**이다.
ydc 도 그렇다(`report` 는 언제나 0 을 낸다).

**It does not overwrite an existing output.** This command writes to no table, and it does not recompute the
values §Crosscheck produced either — the seen arm is a value this command counted **afresh with the same
code**, so it is **not on the same footing** as the commerce column of §Composition (that side counts chunk
bodies and goes through `normalize_text`). So the two values are not subtracted. Every comparison inside this
command is between the two arms alone, which rode the same function.

### Holdout constants (gathered in `analysis/holdout` alone)
| 상수 | 값 | 무엇 위에서 나왔나 | 판단 |
|---|---|---|---|
| `MATERIAL_PP` | `1.5` | ydc `holdout_commerce.report` 의 `abs(d) <= 1.5` — "홀드아웃이 3분의 1 크기라 표본 흔들림이 있다. 1.5%p 를 넘으면 사람이 본다" | 채택. 적합값이 아니라 **사람이 볼지 정하는 문턱**이고, 우리 홀드아웃도 기존보다 작다 |
| `RANK_TOP` | `2` | 같은 함수의 `ra[:2] == rb[:2]` | 채택. **뒤에 붙은 `and ra[-1] == rb[-1]`(최하위 일치)는 옮기지 않는다** — ydc 의 다섯 주제는 전부 값을 가졌지만 우리 13주제 축의 꼬리는 0으로 묶여 최하위가 동률이라, 그 자리를 검사하면 정렬 순서를 검사하게 된다. **전량 실측(2026-08-27)이 그 자리를 실제로 밟았다** — 홀드 팔에서 `혼합자차`·`SPF_PA` 가 둘 다 0건이라 그 등수는 안정 정렬이 축 순서로 정한 것이고, ydc 규칙을 그대로 옮겼으면 거기서 동전 던지기가 판정을 갈랐다 |
| `MIN_MENTIONS` (the rank gate) | `5` | **the same number** as the sample gate in §Formulas — requiring 5 for the verdict while making an exception here alone is a double standard | Adopted. The same place as `MIN_PRODUCTS` in §Rating, but what it counts is documents rather than products, so `analysis.trend.MIN_MENTIONS` is taken as it is |

### Full measurement (2026-08-27, production DB read-only — `cosmai trend holdout`, run by the coordinator session)
```
seen=6,349 holdout=975 topics=13 ranked=13 reproduced=7/13 scale=1.42→1.34
verdict=순위 변동 window=새 기간이다 basket_shared=18   (종료 코드 0)
```
- **컷오프가 §대조 가 센 그 집합을 정확히 집는다**: 기존 팔 6,349 는 §대조 "전량 실측" 의 커머스 리뷰
  문서 수와 **같은 수**다. 두 값을 빼거나 나란히 놓는 것은 여전히 하지 않지만(축이 다르다 — 아래
  "기존 산출물을 덮어쓰지 않는다"), 컷오프가 임의의 날짜가 아니라 **우리가 실제로 본 집합**을 가른다는
  방증이다.
- 명부가 **단조 증가**라 이 컷오프가 뒤로 밀리지 않는다: `retrieval_chunk` 는 upsert 전용이고 사라진
  문서를 거두는 쪽은 전량 훑기에서만 돈다 — `--since` 증분이 기존 팔을 줄이지 않는다.
- **판정은 `순위 변동`이고 그것이 발견이다**(종료 코드 0). 갈린 이유는 세 갈래가 답한다 — 창이
  `새 기간이다`(홀드 팔이 기존 팔의 끝 이후에 시작한다), 플랫폼 구성 기제는 놀고 있고(위 §플랫폼 구성),
  제품 바스켓은 교집합 18제품이다.
- **`ra[-1]`(최하위 일치)을 안 옮긴 것이 여기서 값을 한다**: 홀드 팔에서 `혼합자차`·`SPF_PA` 가 **둘 다
  0건**이라 그 등수는 안정 정렬이 축 순서로 정한 것이다. ydc 규칙을 그대로 옮겼으면 그 자리에서
  동전 던지기가 판정을 갈랐다.
- 추출 규칙이 정말 DB 로 넘어갔는가도 같이 쟀다: `SUN_JOIN` 의 `SELECT DISTINCT` 로 팬아웃이 없다
  (모집단 7,324 = 조인 뒤 7,324) · 페이징 없음 · `review_pkey` 실재.

### Comparison against ydc (run 2026-08-27, 9 lines, **difference 0**)
`tool/compare-ydc-holdout` 이 태그(`v0.3.0`)에서 `holdout_commerce.py` 를 꺼내 **손대지 않고** 돌리고
(`--demo` 만 — 인자 없이 돌리면 그 머신의 PostgREST 로 나간다), 둘을 맞댄다:
- **상수** 셋. ydc 는 `report` **안에 리터럴로** 들고 있어 함수를 부를 수 없으므로 그 소스를 읽어
  맞댄다(`abs(d) <= 1.5` · `ra[:2] == rb[:2]` · 우리가 **안 옮긴** `ra[-1] == rb[-1]` 이 원본에 아직
  있는가 — 사라지면 위 상수 표의 근거 문장이 원본을 잘못 인용하고 있다는 뜻이다).
- **규칙** 여섯(`rates` 다섯 라벨 + 순위 축). 같은 텍스트에 (건수, 비율)을 맞댄다. **맞대는 것은 매처가
  아니라 세는 법이다** — 한 건에 여러 표현이 있어도 한 번인가, 분모가 그 팔의 리뷰 수인가. 매처 쪽
  1:1(`count_terms`)은 #7 이 이미 했고 그 결과를 여기서 입력을 만드는 데 쓴다. **맞대지 않는 것 넷(`CUTOFF`·`BASE`·`PLATFORMS`·`platform_of`)은 도구가 §선언 으로 찍고
넘어간다** — 넷 다 승격이 답을 바꾼 자리이고(컷오프는 청크 색인으로, 플랫폼은 본문 머리 파싱에서
`review.source` 열로), 조용히 빠지면 "차이 0" 이 그 넷을 덮는 것처럼 읽힌다.

**전량 대조는 CI 가 지킬 수 없다** — 원천이 운영 DB 다. CI 가 지는 것은 규칙과 상수이고
(`tests/test_holdout_rules.py`), 원천을 맞대는 일은 사람이 한 번 돌린다(`tests/test_holdout_pipeline.py`
는 모양·막힘·종료 코드와 "아무것도 안 썼는가" 를 진다 — §대조 와 같은 가름이다).

## Limitations of the population (how to read the numbers — `manifest.limitations`, fork #4)
These are the eight sentences the two runs that gathered the 2026-08-19 corpus (`needs.corpus_*`,
`formats.md` §Corpus snapshot) wrote down about themselves. If §Formulas above says **how** a value is made,
this section says what the made value **is not** — without these sentences in the contract only the numbers
are left later, and the same number gets read as something else. The eight lines stand as constants in
`db/corpus/contract.py` and the loader compares them against the manifest.

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
  - So the quarter is the parent video's rather than the comment's (the quarterly document population bullet of §Formulas, `parent_item_id`).
- 댓글은 계속 쌓이므로 최근 분기는 구조적으로 과소 집계된다.
  - **최근 분기의 하락은 트렌드가 아닐 수 있다.** YoY 판정이 최근 분기를 만질 때 이 한계가 먼저다.
- order=relevance는 유튜브 비공개 알고리즘이며 좋아요 순이 아니다.
  - 댓글 표본은 인기순도 무작위도 아니다. 댓글 기반 지표를 "상위 반응"으로 읽지 않는다.

이 한계들은 재수집(#38)으로 사라지지 않는다 — 같은 방법으로 다시 걷기 때문이다. 사라지는 것은
2026-08-19 이라는 시점뿐이고, 그래서 스냅샷을 덮지 않고 판본으로 나란히 둔다.

## Baselines the evaluation harness compares against (the rule implementation, measured 2026-08-23)
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

### Rule measurement (2026-08-24, #3's implementation) — the bar for replacing an implementation
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

## Retrieval measurements (#28 step 4 — 2026-08-25, every source · 381,950 chunks · the queries are the topic aliases)
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
- **이 여섯 줄은 주제 사전 v1 위의 값이다.** 포크 #56 이 v3 로 별칭 일곱을 더했으므로(`formats.md`
  §주제 사전 v3) 색인 토큰이 달라졌고 — `썬쿠션`·`썬스틱`·`선에센스`·`속건조`·`파데프리` 가 Kiwi 사용자
  단어이자 확장 목록에 들어온다 — 질의도 61/60 에서 **63/62** 로 는다. 그래서 v3 를 켠 뒤 이 표는 **다시
  재기 전까지 v1 판본의 기록**이고, 다음 재측정이 어느 델타 위의 값인지는 `tests/retrieval/test_topics.py`
  가 붙드는 "얼어붙은 v1 + #56 원장" 등식이 말한다. 표의 모양(mode×engine 여섯 줄·채택 조건)은 그대로다.
- 이 숫자를 만든 주제 사전은 **`needs.aspect_lexicon` 의 활성 버전**(`ruleset='retrieval-topic'`, v1 —
  그 **번호표는 DB 로 확인할 수 없다**, 아래 세 줄)이다.
  포크 #8 이 원천을 `analysis/retrieval/topics.py` 의 상수에서 그리로 옮겼고, 옮긴 사전이 상수판과 **같은
  15개 주제·같은 별칭·같은 `match_topics` 결과**라는 것을 `tests/retrieval/test_topics.py` 가 얼어붙은 사본
  (`tests/retrieval/frozen_topics.py`)과 맞대어 붙든다 — 그 동등성이 **선언된 델타 없이** 깨지면 이 표는
  조용히 낡은 표가 된다. #56 의 델타는 선언된 것이라 위 줄이 그것을 적는다.
- 원값은 `var/retrieval/score_{mode}_{engine}.csv` 여섯 벌(`var/` 는 레포에 들어가지 않는다). 잰 시점은 정답셋
  소스 좁힘(포크 #16)과 정답 키셋 페이징(포크 #17 S4) **이전**이다 — 둘 다 전 소스 실행이 훑는 행 집합을
  바꾸지 않으므로, 다시 재서 값이 움직이면 그것은 이 두 변경이 아니라 코퍼스가 자란 것이다.
- **질의 불용어(포크 #46)는 이 여섯 줄을 움직이지 않는다.** 재실행이 아니라 구성으로 그렇다: 질의는 전부
  주제 별칭이고, 그중 어느 것도 토큰화하면 그 목록과 겹치지 않는다 — **v1 의 별칭 73개(질의 61/60)와
  v3 의 80개(질의 63/62) 둘 다 겹침 0** 이다(실측 2026-08-27 · `tests/retrieval/test_query_stopwords.py`
  가 v3 판을 건다). 겹침이 0 이면 `tokenize_query` 는 `tokenize` 와 같은 토큰을
  내므로 bm25 세 줄이 그대로고, vector 는 질의를 토큰화하지 않으며(원문을 인코딩한다) heldout 의 정답 제외
  (`eval.docs_with_tokens`)와 색인은 둘 다 `tokenize` 축이라 애초에 이 목록을 보지 않는다. ydc 도 자기
  별칭 61개로 같은 것을 쟀고 같은 0 이었다.
- **이 여섯 줄이 선 벡터 저장소 판본**(포크 #49): `var/retrieval/vectors/e5base` —
  `model=intfloat/multilingual-e5-base · revision=d128750597153bb5987e10b1c3493a34e5a4502a · vectors=381950 ·
  chunked_at_max=키없음`. 마지막 칸은 그 키가 생기기 전에 구운 저장소라는 뜻이고, 그래서 이 판본의 대조는
  개수까지다(`tests/retrieval/test_vectors.py`). 판본을 다시 찍는 길은 `tool/show-vector-stamp <저장소>` 다 —
  매니페스트만 읽으므로 1.2GB 행렬도 GPU 도 필요 없다.
- **bm25 두 줄은 그 판본 위의 값이 아니다.** bm25 는 벡터 저장소를 열지 않는다 — literal `.864` 와 heldout
  `.000` 이 선 것은 코퍼스 381,950청크와 활성 주제 사전이고, 벡터 판본과는 축이 달라 나란히 읽으면 안 된다.
  채택 기준이 되는 heldout vector `.062` 만이 위 저장소 위의 값이다.
- 이 판본은 **행이 스스로 적은 것이 아니다** — 그때의 원값 CSV 여섯 벌에는 판본 열이 아예 없었고(포크 #49 가
  고친 자리가 그것이다), 여기 적은 것은 매니페스트의 `count` 가 표 머리의 청크 수와 같다는 대조로 이은
  것이다. 다시 재는 값부터는 `store` 열이 행마다 스스로 적으므로 이 대조가 필요 없다.
- **이 여섯 줄이 선 주제 사전 판본**(포크 #62). 레포가 그 사전을 얼려 두었다 —
  그때 DB v1 에 넣은 적재 원본(`git show de2ee06:analysis/retrieval/dict/topics_v1.csv`)으로 되짚은
  판본 문자열은 `ruleset=retrieval-topic · version=1 · topics=15 · aliases=73 ·
  fingerprint=5a0cae76311e1408` 이다. 평가 행이 오늘 CSV `dictionary` 열에 싣는 것과 **같은 모양**이다
  (`entrypoints.md` §검색). 이 판본도 **행이 스스로 적은 것이 아니다** — 그때의 원값 CSV 여섯 벌에는
  사전 열이 없었다. 다만 **이 다섯 칸이 다 같은 무게는 아니라** 아래 둘로 갈라 적는다.
- **되짚은 것 — 사전의 내용과 지문.** 옛 적재 원본을 `tool/show-lexicon-stamp --csv <옛 CSV>
  --against 2` 로 운영의 남아 있는 v2 와 맞대면 양쪽이 `fingerprint=5a0cae76311e1408` 이고 **차이
  없다**(운영 DB 읽기 전용 실측 2026-08-27). 레포의 `tests/retrieval/frozen_topics.py` 는 평가 이식
  직전의 **프록시 사본**이다. 그 사본은 `유기자차.mfds_inci` 의 순서 하나가 적재 원본과 달라
  `fingerprint=4afd3b25522a4d26` 을 내지만, 그 열은 매칭도 질의도 읽지 않는다. 두 사전이 그 순서
  하나에서만 갈린다는 것은 `tests/retrieval/test_topics.py` 가 기계로 되묻는다.

  내용은 그 뒤로도 이어진다. ① 옛 적재 원본 = 운영 v2 — 위 대조에서 차이 0. ② 현재 적재 원본 =
  운영 활성 v3 — 주제·별칭·순서까지 **차이 0**(`tool/show-lexicon-stamp --csv
  analysis/retrieval/dict/topics_v1.csv --against 3`; 행 단위로는 `cosmai lexicon diff --kind aspect
  --csv analysis/retrieval/dict/topics_v1.csv`). ③ 운영 v3 = 운영 v2 + 포크 #56 의 별칭 일곱,
  **그 밖의 차이 없음**(`tool/show-lexicon-stamp --version 3 --against 2`).
  그래서 "오늘 켜진 사전이 저 여섯 줄의 사전에서 무엇이 늘어난 것인가"는 세 명령으로 답이 나온다:
  `촉촉함_건조함` +`속건조` · `톤업_메이크업베이스` +`파데프리` · `선크림` +`썬쿠션`·`썬스틱`·`선에센스`·
  `선스프레이`·`sunscreen`. 그 델타가 질의를 61/60 → 63/62 로 늘린다.
- **되짚을 수 없는 것 — 번호표.** `needs.aspect_lexicon` 의 `ruleset='retrieval-topic'` 에 **v1 행이
  없다**: 남아 있는 것은 v2 94행(꺼짐)과 v3 101행(켜짐)뿐이다(운영 DB 읽기 전용 실측 2026-08-27 ·
  `tool/show-lexicon-stamp` · v2 `fingerprint=5a0cae76311e1408` · v3 `ae48f7cfb70a60f7`). 그러므로
  **`version=1` 은 그때의 계약과 포크 #8 의 초록이 남긴 기록이지 DB 가 대는 근거가 아니다.** 내용과
  지문은 남아 있는 v2 로 되짚었지만, 그 내용에 번호 1을 붙여 실제로 활성화했던 DB 행은 사라졌다.
- 다시 재는 값부터는 `dictionary` 열이 행마다 스스로 적으므로 이 되짚기가 필요 없다. **이 표를 다시 재는
  것은 이 이슈가 하지 않았다** — 여섯 줄은 그대로 v1 판본의 기록이고, 재측정은 그때 `dictionary`·`store`
  두 열을 함께 실은 원값 위에 선다.

## Vector floor (not added — the distributions do not part, fork #48)
`analysis/retrieval/vectors.py` 의 `search` 에는 유사도 하한선이 없다 — 코사인으로 정렬해 상위 k 를 그대로
낸다. 그렇다고 "있으면 좋겠다"로 넣으면 안 된다: **e5 코사인은 좁은 띠에 뭉쳐 있다.** 문서-문서가 아니라
질의-문서라 거의 다 한 구간에 들어오고, 분포가 실제로 갈리지 않으면 하한선은 **무관한 결과를 통과시키면서
맞는 결과를 자르는 쪽으로만** 작동한다. 그래서 넣기 전에 재고, **재기 전에 판정 기준을 정한다.**
우리 코퍼스에서 잰 판정은 **못 쓴다** 이고, 아래 두 표가 그 근거다.

| 판정 | 조건 | 우리 코퍼스에서 |
|---|---|---|
| 분리가 된다 | 가짜 질의의 최고 코사인 < 진짜 질의의 최저 코사인 | **아니다** — 가짜 최고 .8550 > 진짜 최저 .8071 |
| 쓸 수 있다 | 겹치더라도 정탐 90% 이상을 남기는 문턱이 오탐을 절반 이상 자른다 | **아니다** — 그 문턱 .8226 이 자르는 오탐 **0/12** |
| 못 쓴다 | 위 둘 다 아니면 하한선을 쓰지 않는다 | **이것이다** |

- **결과를 보고 기준을 만들지 않는다.** 이 표가 계약에 있는 이유가 그것이고, 세 갈래의 뜻은
  `tool/measure-vector-floor` 의 `verdict` 한 함수에만 있으며 `tests/retrieval/test_vector_floor.py` 가
  그 뜻을 붙든다. 90% 와 절반은 그 도구의 `KEEP`·`CUT` 두 상수다.
- **"쓸 수 있다"의 문턱은 정탐 90% 를 남기는 것 중 가장 높은 실측값이다.** 값 사이의 수를 고르면 그
  고르는 규칙이 또 하나의 손잡이가 되고, 더 낮은 문턱을 고르면 오탐을 덜 자르므로 이 기준은 "쓸 수 있다"
  쪽에 가장 후한 읽기다 — ydc 의 구현(`sorted(real)[int(n*0.10)-1]`)보다 한 칸 높다.
- 질의는 **고르지 않는다**: 진짜는 `retrieval eval --mode literal` 이 쓰는 그 주제 별칭(정본은
  `needs.aspect_lexicon` 의 활성 버전)이고, 가짜는 코퍼스에 없는 성분명처럼 생긴 말이다. 가짜가 **정말
  없는지는 잴 때마다 확인한다** — 하나라도 코퍼스에 있으면 그것은 가짜가 아니라서 그 표본 위의 수를
  믿을 수 없다(도구의 종료 코드 1).
- 재는 값은 질의마다 **최고 코사인**(top-1)이다. 상위 k 의 평균을 쓰면 k 가 또 하나의 손잡이가 된다.

실측 (2026-08-27 · 재는 길은 `tool/measure-vector-floor` · 질의마다 최고 코사인 · 저장소 판본은
`model=intfloat/multilingual-e5-base · revision=d128750597153bb5987e10b1c3493a34e5a4502a · vectors=381950 ·
chunked_at_max=키없음`, 활성 주제 사전 v2):

**이 분위수는 주제 사전 v2 위의 기록이다**(포크 #56) — v3 는 진짜 질의를 61 → **63** 으로 늘린다(새 별칭
`속건조`·`파데프리`). 다시 재기 전까지 아래 두 줄은 v2 판본의 값이고, 판정(분포가 갈리지 않는다)이 그 둘로
어떻게 되는지는 재 봐야 안다.

**Version record of this table vs. what the tool carries from now on (#68).** The two rows below were
measured on active lexicon **v2 · 61 queries**, and the tool of that day recorded the lexicon axis as a bare
number; that record stays as written. Since #68 `tool/measure-vector-floor` records the lexicon axis as the
full stamp (`ruleset · version · topics · aliases · fingerprint`), the same weight as the store axis, so the
next remeasurement replaces this line with the stamp it was measured on.


| 분포 | n | 최소 | 25% | 중앙 | 75% | 최대 |
|---|---|---|---|---|---|---|
| 진짜 질의 (주제 별칭 — §검색 실측 의 literal 과 같은 질의) | 61 | .8071 | .8359 | .8457 | .8705 | .9161 |
| 가짜 질의 (코퍼스에 없는 성분명) | 12 | .8301 | .8393 | .8401 | .8472 | .8550 |

- **가짜 분포가 진짜 분포 안에 들어앉아 있다.** "겹친다"만으로는 스치는 것과 뭉쳐 있는 것이 같아 보여서
  겹침의 크기를 함께 적는다: 가짜 12개 중 **10개**가 진짜의 사분위 구간(.8359~.8705) 안에 있고, 진짜 61개
  중 **37개**가 가짜 최고(.8550)보다 낮다. 두 중앙값의 차이는 **.0056** 이다.
- **ydc 임시값 .865 를 우리 코퍼스에 걸면 가짜 12/12 를 막고 진짜 45/61 = **73.8%** 를 함께 버린다.**
  "가짜를 다 막는다"만 보면 좋은 값이고, 그것이 표본 6개로 정한 값의 모습이다. 반대로 정탐을 90% 남기는
  문턱(.8226)은 가짜를 **하나도** 못 막는다 — 이 두 줄이 같은 분포의 앞뒤라 하한선에 쓸 자리가 없다.
- 이 표는 §검색 실측 과 **같은 저장소·같은 질의 목록** 위에 서 있다. 다만 **나란히 읽는 수는 아니다** —
  저쪽은 P@10 이고 이쪽은 코사인이라 축이 다르다. 사전도 같은 판본이 아니다: 저 표는 활성 v1 위의
  값이라 적혀 있고 이 표는 v2 위의 값인데, `needs.aspect_lexicon` 에 `ruleset='retrieval-topic'` 의 v1 행이
  더는 없어 두 판본을 **DB 안에서** 직접 맞댈 수 없다(2026-08-27 · 오늘 활성은 v2 도 아니라 v3 다).
  포크 #62 가 그 자리를 이었다: 되짚은 것(사전의 내용)과 되짚을 수 없는 것(번호표와 지문)을 갈라
  §검색 실측 이 적고, CSV 를 DB 버전과 맞대는 길이 둘 섰다 — 행 단위는 `cosmai lexicon diff --kind aspect
  --csv <path>`, 컴파일된 사전 단위는 `tool/show-lexicon-stamp --csv <path> --against <n>` 이다.
  다만 **이 표의 표본(61개)은 다시 확인해 주지 않는다**: 그때의 대조는 "활성 v2 의 literal 질의 61개가
  그 시점의 적재 원본 CSV 와 같다"였는데, 그 뒤 적재 원본이 v3 내용이 되고 활성도 v3 가 되어
  `tool/measure-vector-floor` 의 `csv_queries` 칸은 이제 **오늘의** 두 짝(63개)을 맞댄다.
- **그래서 `vectors.search` 에 하한선을 두지 않는다.** 넣는 날 `tests/retrieval/test_vector_floor.py` 의
  `test_search_fills_top_k_however_far_the_query_is` 가 빨개지고, 그때 이 절을 함께 고쳐야 한다.

### Then what stops a query with no grounding — chunk frequency (`analysis/retrieval/grounding.py`)
코사인은 안 갈리지만 **빈도는 갈린다.** 규칙 하나다: **길이 4 이상인 질의 토큰 중 청크빈도가 0 인 것이
있으면 막는다.** 코퍼스가 그 이름을 한 번도 말한 적이 없다는 뜻이라, 검색 결과가 나와도 그 이름과 무관한
문서다. 막힌 질의는 결과 0건 + stderr 한 줄이고 종료 코드는 이미 있는 `1`(결과 없음)이다.

**게이트는 `--engine vector`·`hybrid` 에만 건다**(이슈 #48 §범위 확장). `bm25` 의 동작은 **이 이슈 전과
같다**: 어휘 검색은 빈도 0 인 낱말을 idf 0 으로 무시하고 **남은 낱말로 답하므로**, 게이트를 걸면 "진짜
주제 + 코퍼스에 아직 없는 신제품 이름" 질의에서 예전에 나오던 부분 답이 0건이 된다. 그 손해는 아무도
재지 않았고(질의 로그가 없다), 재지 않은 손해를 감수할 이유가 없다.
`tests/retrieval/test_grounding.py` 가 그 자리를 지킨다 — bm25 가 그런 질의에 실제로 답한다는 것까지 본다.

**"df" 라 부르지만 세는 단위는 청크다.** `Index` 는 청크 단위로 서므로 `len(postings[term])` 은 문서 수가
아니라 청크 수다. 0 인지 아닌지만 보므로 아래 표는 안 흔들리지만, `eval.docs_with_tokens` 가 하는
일(문서로 접기)과는 다른 낱말이다.

실측 (2026-08-27 · 재는 길은 `tool/measure-vector-floor --part df` · 청크 381,950 · **활성 주제 사전 v2**
— v3(포크 #56)는 진짜 별칭을 61 → 63 으로 늘리므로 이 표도 그때 다시 재는 표다 ·
막힌 질의 수). **이 표는 스위트가 다시 재지 못한다** — `--part df` 는 인코더도 GPU 도 안 쓰지만 **공유DB
와 세워진 BM25 색인**을 요구하고, 캐시가 없으면 38만 청크를 형태소 분석하는 십수 분이다. 그래서 여기가
그 수의 거처이고, `tests/retrieval/test_grounding.py` 가 붙드는 것은 규칙과 표의 문장이다(§검색 실측 과
같은 자리):

| 규칙 | 진짜 별칭 61 | 진짜 문장 610 | 가짜 이름 12 | 가짜 문장 120 |
|---|---|---|---|---|
| 토큰의 빈도가 **전부** 0 (ydc 초판) | 2 | 0 | 10 | 0 |
| **하나라도** 0 · 길이 ≥ 3 | 2 | 20 | 11 | 110 |
| **하나라도** 0 · 길이 ≥ 4 (`ZERO_DF_MINLEN`) | **0** | **0** | **11** | **110** |

- 표본도 규칙이 만든다(§질의 라우팅 과 같은 방법): 진짜 별칭은 위 표의 그 61개, 문장은 별칭 × `#47` 의
  도구가 쓰는 고정 문형 10개, 가짜는 위 표의 그 12개와 그 문장 120개다.
- **길이 4 는 이득 0 · 손해 2 로 정했다.** 3 으로 내리면 `재도포` 와 `ZnO`(→ `zno`) 두 별칭이 막히고 그
  별칭이 든 문장 20개가 함께 막히는데, **가짜 차단은 11/110 그대로다.**
- **"전부 0" 갈래는 두지 않는다.** 우리 코퍼스에서 그 갈래가 더 막는 가짜는 **0개**이고(11 ≥ 10 —
  `젤라토프로틴추출물` 은 `추출물` 1,341 때문에 전부-0 이 아니다) 진짜 별칭 2개를 막는다. ydc 는 그
  갈래를 두고 `재도포` 가 막히는 것을 옳다고 봤지만, 우리는 같은 이득 0 · 손해 2 다.
- **ydc 출처는 셋 다 `v0.3.0:vector_threshold.py` 에서 확인했다**(2026-08-27, 읽기만):
  가짜 12개 목록은 그 파일의 `FAKE` 와 **글자 그대로 같고**(순서까지), 우리가 안 쓴 문턱 공식은
  `sorted(real)[max(0, int(len(real) * 0.10) - 1)]`(:74), `ZERO_DF_MINLEN = 4` 는 이름과 값이 같다(:33).
  다만 **"전부 0" 은 그 판본에서 버려진 갈래가 아니다** — `v0.3.0` 의 `df_gate` 는 두 갈래를 함께 쓰고
  (:149 과 :164), 우리가 첫 갈래를 뺀 것이다. 이 레포 안에서는 확인되지 않는다 — 핀 사본
  (`analysis/slices/ydc/`, `v0.1.0`)에도 이 파일이 없었고 그 사본은 #9 가 지웠다.
- **토큰이 0개인 질의는 빈도로 판정하지 않는다 — 통과시킨다.** `땀에`·`톤 업`·키릴 표기가 그 갈래이고,
  막으면 **벡터가 유일하게 답하는 자리**를 막는다: §검색 실측 을 만든 실행의 원값에서 `톤 업` 은 vector
  P@10 1.000 인데 bm25 는 검색 결과가 0건이고, `땀에` 는 vector .200 / bm25 0건이다.
- **못 막는 자리는 키릴 표기 하나다**(`трансдермалин` — 가짜 이름 12개 중 1개, 가짜 문장 120개 중 10개).
  `bm25.tokenize` 가 한글도 라틴도 아닌 문자에서 토큰을 하나도 내지 않아 바로 위 갈래로 빠진다. 고칠
  자리는 이 게이트가 아니라 토큰화이고, 이 게이트가 그것을 흉내 내면 축이 둘이 된다.
- **빈도는 색인 축에서 읽는다**(`bm25.tokenize` · `Index.postings`) — 질의 불용어 목록(포크 #46)을 타지
  않는다. `eval.docs_with_tokens` 가 같은 이유로 같은 축이다. 위 표를 잰 시점의 활성 불용어 목록은
  비어 있었으므로(`version=None`, 2026-08-27) 두 토큰화는 같은 토큰을 냈고, 표는 어느 축으로 읽어도 같다.
- **The gate is on `pipeline.search` alone.** `retrieval eval` does not ride it, so the six lines of
  §Retrieval measurements do not move under this gate — and that they would not move even if it did ride it
  is the **0** in the first column of the third row of the table above.

## Per-source allocation (not added — the global top k does not follow the composition, fork #54)
`ranked_chunks(..., sources=...)` 는 `sources` 로 후보를 **좁힐** 뿐, 남은 것 중 전역 상위 k 를 낸다 — 소스별
몫이 없다. ydc `rag/engine.py` 는 소스마다 따로 뽑아 합치는데, 그 이유는 자기 색인의 **92%가 짧은 유튜브
댓글**이라 전역 상위 k 가 `mfds` 를 **293위**로, `ingredient` 를 **300위 밖**으로 밀어냈기 때문이다. 같은
이유가 우리에게도 있는지를 재고 나서 결정한다 — 재기 전에 RRF 를 넣는 것이 이 절이 막는 일이다.

**판정 기준 넷은 재기 전에 정해졌다. 결과를 보고 기준을 만들지 않는다.** 상수는 `K`(10)와 `BURIED_RANK`
(100위 — ydc 의 293위와 같은 자리수이자 k 의 열 배) 둘뿐이고, 거처는 `tool/measure-source-mix` 의 `verdict`
한 함수다(`tests/retrieval/test_source_mix.py` 가 그 뜻을 붙든다).

| 판정 | 뜻 |
|---|---|
| 쏠리지 않는다 | 전역 상위 k 의 지배 소스 점유율 < 그 소스의 색인 구성비 — ydc 의 조건 자체가 없다 |
| 밀리지 않는다 | 쏠리되, 후보를 가진 소수 소스의 첫 등장 순위 **중앙값** < 100위 |
| 지배한다 | 쏠리고, 그 중앙값 ≥ 100위 — 여기서만 분배가 살 자리가 있다 |
| 측정 불가 | 후보를 가진 소수 소스가 하나도 없다 |

**중앙값이 판정을 진다.** 한 질의의 최악값(우리 실측 777위)으로 판정하면 어떤 코퍼스에서도 지배가 나온다 —
꼬리는 언제나 길다.

실측 (2026-08-27 · 운영 DB 읽기 전용 · 청크 **381,950** · 주제 사전 v3 · 질의는 `retrieval eval --mode
literal` 이 쓰는 그 주제 별칭 63개 중 후보가 있는 **59개**(`재도포`·`땀에`·`톤 업`·`ZnO` 는 df 0) ·
엔진은 bm25. 재는 길은 `tool/measure-source-mix`):

| 소스 | 색인 구성비(청크) | 색인 구성비(문서) | 전역 상위 10 점유율 |
|---|---|---|---|
| `youtube_comment` | 288,914 · **75.64%** | 285,735 · 89.34% | 416 · **71.11%** |
| `youtube_transcript` | 63,972 · 16.75% | 5,303 · 1.66% | 32 · 5.47% |
| `commerce_review` | 23,156 · **6.06%** | 22,889 · 7.16% | 123 · **21.03%** |
| `youtube_video` | 5,908 · 1.55% | 5,908 · 1.85% | 14 · 2.39% |

- 판정은 **쏠리지 않는다** 다 — 지배 소스는 색인의 75.64% 인데 상위 10 은 71.11% 만 가져간다(질의별 평균
  70.51%). 첫 관문에서 이미 갈리므로 두 번째 관문은 판정을 지지 않지만, 같이 적어 둔다: 후보를 가진 소수
  소스 (질의, 소스) **157쌍의 첫 등장 순위 중앙값은 19위**(p25 5 · p75 64 · 최대 777)로 100위와 자리수가
  다르다. 상위 10 이 지배 소스뿐인 질의는 59개 중 **11개**이고, 나머지 48개는 소수 소스가 이미 들어 있다.
- **거꾸로 서 있다는 것이 이 절의 답이다.** 색인의 6.06% 인 `commerce_review` 가 상위 10 의 **21.03%** —
  3.5배로 과대표된다. 짧은 리뷰가 질의어를 밀도 높게 담아 BM25 의 길이 보정(`bm25.B` 0.75)이 그쪽을 올리기
  때문이고, 그래서 "긴 문서가 많은 소수 소스가 밀린다"는 ydc 의 그림은 우리 코퍼스에서 성립하지 않는다.
- **`youtube_transcript` "sometimes wins, usually loses" — and the cause is chunk length (fork #65).**
  58 of the 59 queries have a transcript candidate; in **47** its first appearance is outside the top 10
  (median first rank 32) and in **11** it is inside — at rank 1 in 3 of the 58. The numbers behind it
  (`tool/measure-transcript-bimodal`, 2026-09-04, lexicon v3, 381,950 chunks, `avg_len` 24.76 tokens):
  a transcript chunk is median **87 tokens / 480 characters** (3.51× `avg_len`, 12.06 chunks per
  document — the only source packed to the 500-character target) against a comment's 7 tokens, so at
  equal tf BM25's length normalisation (`bm25.B` 0.75) hands the comment a **2.87×** score and a
  transcript needs tf ≥ 7 to tie one comment that says the word once. The rival causes are refuted by
  measurement: transcripts own **47.14%** of the df the query set reaches (2.8× their index share) yet
  5.47% of the top 10, so they lose at scoring, not at matching spoken language; and the 11 winning
  queries are the ones where transcripts hold the term almost alone (median own-df share 82.30% against
  42.20% for the buried 47) — a competition split, not a chunking or style split. Sweeping `B` from
  0.0 to 1.0 moves the transcript share of the top 10 from 62.56% to 3.42% and the median first rank
  from 1 to 190: the whole phenomenon is `B`. **`B` stays 0.75.** The adoption gate the contract names is
  the heldout bm25 row, and it is .000 at every `B`; the only row that moves is literal — a wash, P@10
  .868 → .871 and MRR@10 .897 → .894 at B=0.5 — and that row is a breakage detector biased on the same
  axis (a transcript chunk carries a topic label 46.03% of the time against a comment's 13.30%, yet per
  1,000 characters it is the lowest of the four sources), so tuning `B` on it would be circular. Nor
  does any `B` justify allocation: at B ≥ 0.9 the first gate flips to skew, but the minority median stays
  38–49, below the 100 the verdict asks. The
  structural repair, if the creator-side voice must be visible, is a shorter transcript window (about
  140 characters would sit at `avg_len`), which needs a re-chunk and a re-embed of production and a new
  retrieval-measurements table — its own issue. `youtube_video` has the same shape (38 of its 47 candidate queries
  outside the top 10 · median 35 · max 777) and the same reading applies.
- **So an allocation (drawing k/n per source and merging · RRF) is not added to `ranked_chunks`.** An
  allocation is not free — it hands out the top k slots by membership rather than by relevance, so with no
  skew to fix all that is left is the loss. The gold of §Retrieval measurements is a (document, topic) label
  independent of the source, so that loss comes straight out as a fall in P@10.
- **다시 재야 하는 때**: 이 판정은 위 구성비 위에 선다. 한 소스가 자라 지배 소스의 상위 10 점유율이 색인
  구성비를 넘으면 첫 관문이 뒤집히므로, 코퍼스가 크게 자라거나 소스가 늘면 `tool/measure-source-mix` 를
  다시 돌린다. `sources` 로 **좁히는** 기능은 그대로다 — 이 절이 없다고 말하는 것은 몫이지 좁힘이 아니다.

## Query routing (no router is attached — there is no canonical decision on an ingredient name, fork #47)
ydc `v0.3.0` 의 `rag/router.py` 는 질의마다 규칙으로 엔진을 고른다(LLM 을 안 쓴다 — 결정적이고 왜 그렇게
갔는지 항상 말할 수 있다). **승격하지 않는다.** 라우터가 보는 **신호 넷 중 둘의 원천이 없고**(성분명 ·
등록번호) 그에 딸려 **갈래 둘이 막히며**(`bm25` 의 성분명 쪽 · `multi_source`), 성분명 자리에 지금 있는
유일한 목록을 쓰면 ydc 가 밟은 함정을 그대로 밟기 때문이다. 남은 신호 둘만 보는 라우터를 실제로 재 보면
옳게 보낸 질의가 0개다(아래 §오라우팅 실측 의 여섯째 줄).

### The canonical decision on an ingredient name is not the tokeniser dictionary
`analysis/retrieval/dict/ingredient_dictionary.tsv`(1,877표기, `bm25.DICTIONARIES`)는 **Kiwi 토크나이저
사전**이다. 담론어가 일부러 들어 있다 — 사전 없이 색인하면 `백탁` 이 `백`+`탁` 으로 쪼개지기 때문이고
(`bm25.kiwi`), 그것이 이 파일이 하는 일이다. 그걸 "성분명 목록"으로 읽으면 그 말이 든 **자연어 질의가
정확 질의로 판정**된다. 이 문장을 `tests/retrieval/test_query_routing.py` 가 지킨다.

레포의 성분명 후보는 셋이고 **셋 다 담론어를 담는다** — 어느 것도 이 판정의 정본이 될 수 없다.

| 후보 | 규모 | 왜 정본이 아닌가 |
|---|---|---|
| `dict/ingredient_dictionary.tsv` | 1,877표기 | 토크나이저 사전. 활성 주제 사전의 `ko` 별칭 **11개**(`선크림`·`백탁`·`톤업`·`무기자차`…)가 여기 있다 |
| `needs.entity_lexicon` `kind='ingredient'` v1 | 43행 / 28키 / 고유 42표기 (원본 `eval/lexicon/ingredient_kr_colloquial_v1.csv`) | `source='paper_lexicon'` — 논문 검색어 어휘다. 원본 CSV 32키의 `category` 가 **10종**(uv_filter 5 · mechanism 5 · spec 4 · enzyme 2 · peptide 2 · formulation 2 · regulatory 2 · claim 1 · anatomy 1 · ingredient 8)으로 갈려 `category='ingredient'` 는 **32키 중 8키**뿐이며, `무기자차` 를 ZINC_OXIDE 와 TITANIUM_DIOXIDE **양쪽**의 표기로 담는다 |
| `needs.aspect_lexicon` `ruleset='retrieval-topic'` 의 `term_kind='mfds_inci'` | 24표기 | 성분 표기 축이지만 무기·유기자차 두 주제의 자외선차단 성분뿐이고, `자외선차단제`(제품 범주)가 섞였으며 `ko` 축과 3표기(`아보벤존`·`옥토크릴렌`·`자외선차단제`)가 겹친다 |

ydc 의 정본은 성분표(`data/external/product_ingredient_function_repaired.csv`, 31,246행/577제품)의
`ingredient` 컬럼에서 주제 별칭을 빼고 마커를 정리한 1,851종이다. **그 성분표는 우리에게 오지 않는다** —
upstream `slopindustries/cosmai#73` 이 2026-08-26 에 "전성분은 수집기로 걷는다, 외부 CSV 는 들이지
않는다"로 정했다. 그래서 이 축은 그 수집이 서기 전까지 열리지 않는다(포크 #10 이 보류로 든 자리).

### The four branches and their sources (what each is blocked by)
| 갈래 | 신호 | 우리 원천 | 상태 |
|---|---|---|---|
| `bm25` | 성분명 | 없다 (위 표) | **막힘** — upstream `slopindustries/cosmai#73` |
| `bm25` | 브랜드 | `needs.entity_lexicon` `kind='brand'` 968행 / 고유 표기 950 (원본 `eval/lexicon/brand_lexicon_v1.csv` 859행) | **목록은 있으나 오탐한다** — 950표기 중 **144개가 2자 이하**라 ydc 의 부분문자열 `_find` 아래에서 평범한 한국어에 걸린다(`밀려` ← 브랜드 `려`) |
| `bm25` | 등록번호(10자리 보고번호) | 없다 | **막힘** — 포크 #55 |
| `bm25` | SPF/PA 표기 | 정규식 하나 | 열려 있다 |
| `temporal_filter` | 시점 표현 + `report_date` | **등록 시점 축은 없다**(포크 #55 — `mfds_items.csv` 는 `COSMETIC_REPORT_SEQ`·`ITEM_NAME`·`ENTP_NAME`·`report_date` 넉 칸이라 시점과 등록번호만 열고 **성분 축은 열지 않는다**). `corpus_document.published_at`(023 DDL, NOT NULL)은 **있다** | **막힘** — 다만 원천이 없어서가 아니라 **축이 다른 물음**이라서다: ydc 의 `report_date` 는 *제품 등록 시점*이고 우리 칸은 *발화 시점*이라, "최신 제품"과 "최근 발화"는 같은 질의가 아니다. 시점 표현 사전은 상수라 원천이 필요 없다 |
| `multi_source` | 정확 신호 + 자연어 신호 | 성분명 축이 서야 갈린다 | **막힘** (아래) |
| `vector` | 그 외 | — | 열려 있다 |

**열려 있는 신호 둘만으로는 라우터가 서지 않는다 — 불완전해서가 아니라 해로워서다.** 기본값이 `vector` 면
부분 라우터는 아무것도 악화시키지 않는다는 반론이 가능하므로, 그 자리는 수사가 아니라 실측이 막는다. 브랜드
표기 950 + SPF/PA 정규식만 보는 라우터에 우리의 **유일한 측정된 질의 워크로드**(§검색 실측 의 literal 모드가
재는 그 주제 별칭 73개)를 통과시키면 `bm25` 로 가는 질의가 **2개, 옳게 보낸 것은 0개**다 — `밀려` 가 브랜드
`려` 에, `화이트닝` 이 브랜드 `화이트` 에 걸린다. **이득 0 · 손해 2 이므로 기본값보다 나쁘다.**

### There are places that do not split in two for us either (`multi_source`)
ydc 의 `콜라겐 들어간 제품 뭐가 좋아` — `콜라겐` 은 진짜 성분이면서 담론어다(담론/제품 비율 콜라겐 281 ·
판테놀 7). 우리 자리는 활성 주제 사전이 그대로 가리킨다: 성분 표기 축(`mfds_inci`)과 사람이 쓰는 말 축(`ko`)에
**둘 다** 오른 표기가 **3개** 있다 — `아보벤존`·`옥토크릴렌`·`자외선차단제`. 진짜 성분이면서 사람이 그 말로
묻는다는 뜻이고, 그것이 `콜라겐` 과 같은 자리다. (표 다섯째 줄의 `mfds_inci` 15 는 **다른 축**이다 —
그쪽은 토크나이저 사전과의 겹침이고, 그 15는 `에칠헥실트리아존` 같은 순수 INCI 화학명이라 담론어가 아니라
담론어의 정반대다. 이 문단에 그 수를 쓰면 안 된다.) 정확 신호와 자연어 신호가
같이 있으면 **고르지 말고 둘 다 낸다** — 라우터를 붙이는 날 이 규칙이 함께 온다. 하나를 고르면 반드시
한쪽을 잃고, 그것은 소스를 합산하지 않는 것과 같은 논리다.

### Misrouting measurement (2026-08-27 · our own dictionary · the way to measure it is `tool/measure-query-routing`)
ydc 초판의 이진 규칙(정확 신호가 있으면 `bm25`, 없으면 `vector`)에 성분명 목록으로 우리 토크나이저 사전을
넣고, 자연어 판정은 ydc 와 같이 Kiwi 의 서술어 태그(`VA`·`VV`·`EF`·`EC`·`VCP`·`VCN`)로 한다.
`tests/retrieval/test_query_routing.py` 가 아래 수와 도구의 산출을 매번 맞댄다.

| 무엇을 재는가 | 값 |
|---|---|
| 자연어 질의 10개 표본 — 주제 사전 순서 앞 10주제 × 주제의 첫 `ko` 별칭 × 고정 문형 10개 | 오라우팅 **4/10** |
| 같은 규칙을 15주제 전부에 | **7/15** |
| ydc 가 공표한 질의 3개를 우리 사전으로 | **3/3** |
| 활성 주제 사전의 `ko` 별칭 73개 중 토크나이저 사전에 있는 것 | **11** |
| 같은 사전의 `mfds_inci` 표기 24개 중 토크나이저 사전에 있는 것 | **15** |
| 성분 표기(`mfds_inci`)이면서 사람이 그 말로 묻는(`ko`) 표기 — `콜라겐` 자리 | **3** |
| 열려 있는 신호 둘(브랜드 950표기 + SPF/PA)만 보는 라우터가 주제 별칭 73개에서 `bm25` 로 보내는 질의 | **2**, 그중 옳은 것 **0** |

- **ydc 의 7/10 은 재현되지 않았다(4/10). 함정은 그대로 재현됐다.** 그 둘은 다른 말이다: 비율은 표본의
  주제 구성이 정하고 규칙이 정하지 않는다. 우리 표본의 앞 10주제에는 `선크림` 주제가 안 들어간다(사전에서
  15번째다) — 넣으면 오르고, 빼면 내린다. **그래서 이 절의 답은 첫 줄이 아니라 넷째 줄과 마지막 줄이다**:
  담론어 11개가 성분 신호로 서는 것은 표본과 무관한 사전의 성질이고(그 11개가 든 자연어 질의는 **전부**
  `bm25` 로 간다), 성분명 축을 뺀 라우터는 이득 0 · 손해 2 다.
- 문형 10개는 성분 신호를 하나도 내지 않는다(실측 · 같은 테스트). 걸린 표기는 언제나 그 주제의 별칭
  하나뿐이라, 비율이 문형에서 온 것이 아님을 도구가 매번 보인다.
- **표본을 규칙이 만들어도 규칙 선택의 자의성은 남는다.** 별칭 고르는 규칙을 바꾸면 앞 10주제의 오라우팅이
  **0~6** 사이에서 움직인다(실측 · 같은 도구의 `alias_rules`: 첫 `ko` 4 · 마지막 `ko` 0 · 아무 `ko` 6 ·
  첫 `mfds_inci` 1). 첫 줄의 4 는 그 범위 안의 한 점이고, 그래서 첫 줄은 이 절의 답이 아니다.
- ydc 가 공표한 셋은 걸린 표기까지 그쪽 문서와 글자 그대로 같다 — `선크림 루틴 알려줘` → `['선크림','루틴']`,
  `백탁 관련해서 소비자들이` → `['백탁']`, `끈적이지 않는 선크림 추천` → `['선크림']`. **`루틴` 까지 같다**:
  우리 사전은 ydc 것과 같은 성분표에서 나온 같은 파일이다.
- 이 표는 §검색 실측 과 **같은 자가 아니다**. 축이 다르다 — 저쪽은 질의 61/60개가 전부 주제 별칭이고 점수는
  P@10 이며, 이쪽은 자연어 문장 10개이고 값은 갈래 배정이다. 나란히 놓으면 안 된다.

### What is handed to fork #11
#11(검색 기본 엔진)은 **기본값 하나를 고르는** 이슈이고 라우터는 그 질문을 질의마다 규칙으로 답하는 안이다.
넘기는 것은 하나뿐이다: **라우터는 #11 을 대체하지 못한다** — 신호 넷 중 둘이 막혀 갈래 둘이 서지 않고,
남은 신호 둘만으로 세우면 실측으로 이득 0 · 손해 2 라 기본값보다 나쁘다. 성분표가 오기 전에는 그 상태가
바뀌지 않는다. 그러니 #11 은 기본값을 골라야 한다.
**Not one of the seven lines of the table above is an input to #11** — the input to the default-engine
judgment is the six lines of §Retrieval measurements, measured on the same footing over every source, and it
is the same place §Evidence pinned for its own two lines.

## Answer layer (`cosmai retrieval ask` — a summary of retrieval results, fork #73)
The LLM is the last layer and makes no conclusion: `search` finds the evidence, the code folds it to one item
per document, and the model only says what that evidence says. Since fork #77 the evidence can be an MFDS
filing (`source='mfds'`, BM25 only — `entrypoints.md`, the retrieval block): a report number or a registered product name
is answered from the ledger, and the chunk text ends with the snapshot label so the answer says which copy it
read; "recent" and formulation properties (inorganic, SPF) are not in the ledger and stay unanswered from it. The grounding gate is engine-independent here
(#76): `ask` checks it before searching whatever `--engine` says, because the call is paid and a df-0 name makes
the model refuse anyway (row 1 of the #74 table on #69); `search` itself keeps #48's rule and leaves bm25 ungated. The decision that this layer
exists and what it is for is fork #69 (decision A, 2026-09-03); the acceptance measurement — ydc's 17 listed queries
re-measured on our corpus — is fork #74 (done 2026-09-04, the table is on #69) and is **not** in this section. The origin is ydc `rag/generate.py`
(v0.4.0 `76db718`), with two corrections the promotion required: text blocks only out of a response that mixes
thinking blocks (the shape `analysis/polarity/llm.py` already uses), and "no key" as an option (`--dry-run`),
not a fallback path.

**Prompt rules** — principle sentences, no numbers; the numbers ydc carried (a heldout hit rate, a backtest
hit rate and base rate) were records of older lexicon and corpus versions and do not belong in a prompt that
outlives them. (1) Answer only from the evidence given; invent nothing. (2) Put `[Source: doc_id]` after
every factual claim. (3) When the evidence is not enough, say the fixed sentence and stop. (4) Keep sources
apart; BM25 scores and vector cosines are different scales, never cite a score as a ranking reason. (5) No
causation between an ingredient and a reaction — only "there was a mention that …". (6) Vector search misses
often and a chunk can sit close in cosine while unrelated (an e5 limit): read the query-token chunk
frequency first — frequency 0 means the corpus never says that word — then read the text and say so in the
answer. (7) Do not predict; backtesting did not show that a rise continues; say only "this asymmetry is here
now". (8) Do not use papers or research trends — that axis is held shut (`analysis.crosscheck.PAPER_HOLD`;
the sentence is emitted only while the constant holds). ydc's rules 5 (MFDS data) and 7 (temporal filter)
are deleted: they name sources this corpus does not have (MFDS is fork #55, still open).

**Format** — exactly three sections, fixed English markers, sentences in the language of the query:
`## Core` (2–4 sentences, each cited), `## Evidence summary` (at most three bullets; "N items say the same"
rather than quoting), `## Limits` (one or two lines: what this answer must not be used for, the one-source
warning, and that it stands on retrieved chunks — a different denominator from the quarterly verdict table).
Past four sentences the core is cut.

**Citation unit** — `search` emits `chunk_id` (`doc_id#ordinal`); the prompt cites the document. Chunks of
one document are folded into one piece of evidence with their texts concatenated in rank order, so a long
document is one citation, not several.

**Version note** — one stderr line per run (on the vector path `index` and `chunks` both stand on the encoded
sources the gate read, #77): `note: index=<pipeline.index_signature> · chunks=<count> ·
dictionary=<topics stamp>[ · store=<vectors stamp>][ · <coverage note>]`. The lexicon and the signature are
read **once, right after the index is opened and before the search**, so the row and the note stand on the
lexicon the evidence stood on (the same rule `eval.run` keeps; fork #62, #68). The index and the vector store
are each opened once per run and handed down to `search`.

**Cost** — `analysis/polarity/pricing.UsageLedger` as it is: `reserve` before the call (estimate = prompt
characters × the polarity rate + the output ceiling), `settle` after, `purpose='retrieval_ask'`, the $10
total hard stop shared with polarity. The output ceiling is 4096 tokens because adaptive thinking spends the
same budget; an answer the model cut off (`stop_reason == max_tokens`) or left empty is settled and logged —
the money moved — but refused to the caller (exit 1), never printed as if complete. The per-`purpose` cap for
`retrieval_ask` is **$0.20 per call on the reservation estimate and $1.00 per UTC day** (user decisions on fork
#78 and #80, 2026-09-05; #74 measured $0.025 mean · $0.042 max per settled call over 17 calls, and the reservation
carries the 4,096-token output ceiling, so the per-call value sits above it): `UsageLedger.reserve` checks both under
the same advisory lock as the hard stop and before the reservation row — today's spend counts the purpose's settled
and reserved rows since UTC midnight — and a miss is refused the way the hard stop is (exit 2, no call, no log row;
fork #80). The values sit next to `PURPOSE` in `analysis/retrieval/ask.py` until upstream #136 makes them a knob.
The value was set against the reservation, not the settled cost: the reservation is $0.0614 of output ceiling
plus the prompt at two tokens per character, so ordinary asks reserve $0.078–$0.097 and a `--top 10` fold of ten
chunks near the 500-character split about $0.109 — $0.10 refused that fold, $0.20 admits it with headroom and
still refuses a runaway prompt or a dearer model.
The same decision keeps the conditional answer for a question whose data axis the corpus lacks (sales, a price
comparison — rows 2 and 5 of the #74 table): no refusal rule is added to the prompt.

**Log** — `needs.retrieval_ask_log` (DDL 026, append-only by convention — the runtime code only inserts; the role's privileges are the schema defaults): `id` ·
`called_at` · `query` · `engine` · `gate_ok` · `token_df` (jsonb, per query token — on the query axis,
`bm25.tokenize_query`, which drops query stopwords; the gate reads the index axis, so `gate_ok` can be true
on a token absent from this map) · `doc_ids` (text[], folded, rank order) · `index_fingerprint` ·
`dictionary_stamp` · `store_stamp` (null on `bm25`) · `model` · `usd` · `answer_chars`. One row per real
call — not on `--dry-run`, not on the 0-evidence path, not on a gated query (no call was made; since #76 the gate precedes the call for
every engine, so `gate_ok` is true on every row — DDL 026's column comment, which says bm25 is never gated,
predates that) — inserted after `settle` in its own
short transaction, outside the LLM round trip (the 15-second idle-in-transaction rule). These columns are what
lets the next judgment be measured: the partial answers BM25 gives on the query axis (`pipeline.search`'s
"unmeasured loss") and the actual query distribution behind fork #11.

**Exit codes and streams** are in the retrieval section of `entrypoints.md` (the `ask` continuation of its
exit-code bullet); `tests/retrieval/test_ask.py` holds every sentence above that names a behaviour.

## Pass criteria (2026-08-23 decision: no stop through step 6 → the baseline in the second pass)
| 패스 | 유닛 완료 기준 | 검사 |
|---|---|---|
| 1차 | 계약 시그니처 구현 + `cosmai eval <task>`가 그 유닛의 평가셋에서 **점수를 산출**한다(기준선 미달 허용) + `analyze <stage>` 멱등 | eval 출력 행이 `needs.analysis_run.note`에 기록, 점수는 이슈 코멘트 |
| 2차 | 위 표의 기준선 이상 | 같은 평가셋, 블라인드 홀드아웃 |
기준선 표는 계약이고, 패스는 순서다. 1차 패스 점수가 기준선을 이미 넘으면 2차는 생략한다.
