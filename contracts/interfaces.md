# Analysis package interfaces (Python; the types are dataclass/Protocol)

`analysis/types.py` and this code block **must be the same** — `tests/test_contract_types.py` compares the dataclass fields.
The audit ids (B·A·T) are the item numbers of the 2026-08-23 contract audit (issue #17).

```python
# analysis/types.py -- has to match the code block of contracts/interfaces.md
# (tests/test_contract_types.py).
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
    category: str | None = None  # the site original. The dictionary choice is lexicon_category (formats.md)
    channel_id: str | None = None


# ---------- dictionary ----------
@dataclass(frozen=True)
class EntitySurface:  # one dictionary row = entity_lexicon
    # product_line is not here: a line is not a headword but composed of brand + line_tokens (A14).
    kind: str  # brand | format | attribute | ingredient | stopword | alias (vocabulary of the CHECK of 001)
    canonical: str
    surface: str
    tier: str | None  # brand: normal | cooc_required | stop
    source: str | None


@dataclass(frozen=True)
class Lexicon:
    """One version of entity_lexicon. Everything the linker and the wish extractor need."""

    version: int
    surfaces: tuple[EntitySurface, ...]
    surface_to_canonical: Mapping[str, str]  # lower-case keys included
    surface_re: re.Pattern[str]  # descending length + allowed particles
    stop: frozenset[str]
    cooc_required: frozenset[str]
    product_word_re: re.Pattern[str]  # for judging co-occurrence with a product word
    cooc_window: int = 25  # 25 characters either side
    format_patterns: tuple[tuple[str, re.Pattern[str]], ...] = ()
    attribute_patterns: tuple[tuple[str, re.Pattern[str]], ...] = ()


@dataclass(frozen=True)
class AspectPattern:
    aspect: str  # need_key
    scope: str  # generic | category
    category: str  # only when scope=category; generic is ''
    pattern: re.Pattern[str]
    is_neutral_noun: bool  # the neutral-noun twin
    priority: int  # B5: ascending match, ties by id
    ruleset: str  # B4: suncare-v2.2 | p1-v2.2 | shared


@dataclass(frozen=True)
class AspectLexicon:
    """Used by both polarity.classify and extractor.candidates. The loader is ruleset IN (requested,
    'shared')."""

    version: int
    ruleset: str
    patterns: tuple[AspectPattern, ...]
    discourse_marker_re: re.Pattern[str]
    wish_marker_re: re.Pattern[str]

    def for_category(self, category: str | None) -> tuple[AspectPattern, ...]:
        """Ascending priority, ties by id -- a category-only one hides the generic of the same name."""
        ...

    def complaint_marker_re(self, category: str | None) -> re.Pattern[str]:
        """Discourse markers | every pattern of that category."""
        ...


# ---------- product identification ----------
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
    match_score: float | None  # A13: the candidates.dice of the same pair


@dataclass(frozen=True)
class ProductVariantRow:  # -> needs.product_variant (B3: no output algorithm, so outside the scope of #2)
    source: str
    product_key: str
    variant_of: str
    variant_kind: str  # refill | size | scent | shade | option | set
    variant_label: str | None


@dataclass(frozen=True)
class ProductCandidateRow:  # -> needs.product_ref_candidate (A13: the human review queue)
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
class ProductMatch:  # B2: one union-find emits four things at once
    refs: tuple[ProductRefRow, ...]
    members: tuple[ProductMemberRow, ...]
    variants: tuple[ProductVariantRow, ...] = ()
    candidates: tuple[ProductCandidateRow, ...] = ()


# ---------- extraction ----------
@dataclass(frozen=True)
class EntityHit:  # the linker output
    kind: str  # brand | format | attribute | ingredient | product_line
    canonical: str
    surface: str
    start: int
    end: int
    cooc: bool  # whether it co-occurs with a product word


@dataclass(frozen=True)
class Candidate:  # the extractor output (per sentence)
    unit_ref: str
    sentence: str
    kind: str  # complaint | wish | low_rating
    marker: str
    subject: str | None = None  # A10: a product name or video title -- context when a person reads it


@dataclass(frozen=True)
class PolarityRequest:  # one item of classify_many. The arguments of classify tied together as they are
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
    format: str | None  # A12: at most 3 separated by ';', the first one is the main value (formats.md)
    attribute: str | None
    marker: str | None
    sentence: str = ""  # B1: which sentence matched -- wish_mention.sentence is NOT NULL


# ---------- mention rows ----------
@dataclass(frozen=True)
class NeedMentionRow:  # → needs.need_mention
    src: str
    site: str
    ref: str
    product_ref: str | None
    source_product_key: str | None
    category: str | None  # the site's original category
    lexicon_category: str | None  # B10: the category used to choose the dictionary
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


# ---------- aggregation ----------
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
class PanelRosterRow:  # -> needs.panel_roster (fork #3). One roster revision -- panel_version's parent
    version: int
    note: str | None = None  # what this revision is (seed:channels_v1 ...)


@dataclass(frozen=True)
class PanelChannelRow:  # -> needs.panel_channel (fork #3). The 43-channel roster; the seed fills it (#31)
    channel_id: str
    version: int  # the roster version. The same shape as a dictionary (formats.md §Panel roster CSV)
    panel_role: str  # product | expert -- a channel outside the roster is outside the denominator too
    handle: str | None = None
    channel_title: str | None = None
    role_basis: str | None = None  # the ground for that role (team_message | name_rule_verified ...)
    source_list: str | None = None
    active: bool = True


@dataclass(frozen=True)
class MetricsTopicQuarterRow:  # → needs.metrics_topic_quarter (canonical for the quarter, formats.md §Time)
    run_id: int
    scope: str  # a category name | 'all' (the same vocabulary as metrics_need.scope)
    # The registry of the topic axis is aspect_lexicon(ruleset='retrieval-topic').aspect, not needs.need_key
    topic_key: str  # 두 축은 `백탁` 하나만 겹친다 (tests/test_panel_quarter_contract.py)
    quarter: str  # 'YYYYQn'
    source: str  # youtube_video | youtube_comment -- descriptions and comments go side by side, not merged
    content_type: str  # long_form | short_form — the denominator is long-form only (§Formulas)
    panel_version: int  # the population of this ratio: panel_channel.version
    panel_role: str  # which population of that roster. product | expert
    mentions: int  # numerator: documents this topic matched
    documents: int  # documents of that population in that quarter
    quarter_mentions: int  # denominator of the share: the mentions of that quarter's trend_use topics
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
    # The first eight columns are the primary key of metrics_topic_quarter as it is. A judgement takes one row
    # of that table and emits one row, so those eight are the FK, and a judgement row cannot exist without the
    # metric row that grounds it.
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
    single_source: bool  # was this judged looking at one source only. v1 (YouTube alone) is always true
    opportunity_score: float | None = None  # 0-100 normalized in the family. NULL for an unscored cell
    gap_pp: float | None = None  # comment - video share (%p). A (topic, quarter) fact, so both rows equal
    hold_reason: str = ""  # `판정 보류` 의 사유 코드. 보류가 아니면 '' (§판정 의 닫힌 어휘)


@dataclass(frozen=True)
class TopicQuarterEvidenceRow:  # → needs.topic_quarter_evidence (the speech under a verdict cell — §Evidence)
    # The first eight columns are the primary key of topic_quarter_judgement as it is. That the evidence
    # points at the judged cell rather than the metric row is the point -- whoever asks for evidence is
    # whoever read the type.
    run_id: int
    scope: str
    topic_key: str
    quarter: str
    source: str
    content_type: str
    panel_version: int
    panel_role: str
    rank: int  # the like-count descending slot inside that cell. From 1 with no gaps (§Evidence)
    snapshot_id: int  # the observation revision the evidence lives in. doc_id alone cannot part a recollect
    doc_id: str  # the body is not here -- the corpus is canonical and topic_quarter_evidence_quote joins it
    like_count: int  # why it was picked. A snapshot as of collected_at, so counted later it is another number
    matched_term: str | None = None  # the expression corpus_mention already attached. Not matched again here


# ---------- sensitivity (a counterfactual output. Stored in no table — §Sensitivity) ----------
@dataclass(frozen=True)
class PanelSensitivityRow:  # does the panel composition change the conclusion (ydc panel_sensitivity.py)
    source: str
    topic_key: str
    quarters_ok_product: int  # quarters whose mentions clear the sample gate -- the product-only output
    quarters_ok_all: int  # the same measured over all 43 channels (product+expert)
    delta_product_pp: float  # share of the last 4 quarters - share of the 4 before that (%p)
    delta_all_pp: float
    difference_pp: float  # the difference of the two deltas. Subtracted before rounding
    sample_ok: bool  # do the qualifying quarters exceed half the observed ones. Otherwise it is not judged


@dataclass(frozen=True)
class BacktestRow:  # could it have been known then (ydc backtest.py)
    cutoff: str  # the quarter T judged. Metrics were recounted as if only up to the quarter after T was known
    source: str
    topic_key: str
    trend_type: str  # 방향이 있는 넷뿐이다 — 급상승·신규 등장·사라짐·단기 피크
    before_pp: float  # the average share of the previous 4 quarters (baseline A)
    before_excl_pp: float  # the previous 4 quarters minus T, averaged (baseline B -- "did the level hold")
    after_pp: float  # the average of the 4 quarters after C
    at_cutoff_pp: float  # T 분기의 구성비. `단기 피크` 의 비교 상대다
    expected: str  # 상승 유지 | 하락 유지 | 피크 소멸
    actual: str  # 상승 | 하락 (기준 A 의 비교 결과)
    hit: bool  # baseline A
    hit_level: bool  # baseline B


@dataclass(frozen=True)
class AdSensitivityRow:  # is the conclusion the same with ads and sponsorship removed (ydc spam_ad_flags.py)
    variant: str  # ad_video | creator_comment | promo_comment | all_flagged
    source: str
    topic_key: str
    composition_base_pp: float  # the share of the recent 4 quarters (the baseline)
    composition_kept_pp: float  # the same measured on that variant
    diff_pp: float
    judged_cells: int  # the cells the baseline judged in that (source, topic)
    flipped_cells: int  # of those, the cells whose type changed. Cells lost to sample shortfall are not here


# ---------- protocols ----------
class Linker(Protocol):
    version: str

    def link(self, unit: TextUnit, lexicon: Lexicon) -> list[EntityHit]: ...
    def match_products(self, products: Iterable[ProductRow]) -> ProductMatch: ...  # B2


class Extractor(Protocol):
    version: str

    def candidates(self, unit: TextUnit, aspects: AspectLexicon) -> list[Candidate]: ...
    def wishes(self, unit: TextUnit, lexicon: Lexicon) -> WishResult | None: ...


class Polarity(Protocol):  # <- the LLM insertion point. The rule and LLM implementations share a signature
    version: str

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult: ...  # category is the lexicon_category (not the site original)
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
class LabeledRow:  # one row of needs.labeled_set. The only input the eval harness hands an implementation
    task: str
    ref: str
    split: str
    gold: str
    text: str
    extra: Mapping[str, object]  # the set name (`set`), rating, in_final and the other columns of the CSV


class Predictor(Protocol):  # an eval implementation. Takes a batch and returns labels in the same order
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
  The population is inside the row: `panel_version` (which roster) · `panel_role` (which population of
  that roster) · `denom_channels` (how many channels actually entered that quarter's output) ·
  `documents` · `quarter_mentions`. The denominator is **long-form videos only**
  (`content_type='long_form'`): a short has an empty description box, so its match rate is 24% (long form
  64%), and its weight moves between 55% and 41% from quarter to quarter, so putting it in one denominator
  disguises a change of format choice as a topic trend. Video descriptions and comments are not merged
  either but emitted side by side under `source` — the two measure different things (a description
  measures specifications and formulae, a comment measures how it felt and what was wrong). Because
  `content_type` is inside the key, a `short_form` row is legal too — the two formats do not fight over one
  denominator but each have their own `quarter_mentions`·`denom_channels`. v1 (ydc) emits `long_form` rows
  only.
- **The quarterly document population** — what those five columns counted. Of the videos uploaded by the
  channels that are `panel_role` among the active rows of that `panel_version` roster: ① those that have a
  duration and exceed 60 seconds (a video with no duration — a live stream and the like — drops out along
  with the shorts) ② those where a dictionary word of the `scope` category catches **as a substring** in
  the normalised **title+description** (ydc: the alias list of the `선크림` topic) ③ those that have an
  observation month. Only a video that clears all three stays, and the same video is counted once even
  when it is in several runs. **`documents` is not the panel's total video count — it is the count after
  the cut by category.** Computed over all the panel's videos, every ratio of this table changes with no
  error.
  - `source='youtube_video'`: one document = one video (title+description), and `documents` is the count of
    those videos in that quarter.
  - `source='youtube_comment'`: one document = one comment attached to those videos. `documents` is the
    count of comments that are **not empty and, within one video, folded into one when they normalise to
    the same thing** (duplicates across videos are not folded — the same words under a different video are
    each a real reaction). The quarter is not the comment's time but **the parent video's quarter**: a
    comment lands yesterday on a three-year-old video, so making the quarter out of the comment's time
    leaves the denominator undefined.
  - `denom_channels` is **the same** on both `source` values — the count of channels that emitted a video
    clearing the three conditions above in that quarter (a comment attaches to a video, not to a channel).
- **The quarterly table's row set** — inside one (`run_id`, `scope`, `source`, `content_type`,
  `panel_version`, `panel_role`) this table is **a dense grid**: there is one row for each topic with
  `trend_use=true` (13 today) × every quarter existing in that computation, and a cell with 0 mentions
  becomes a row too (`mentions=0` · `composition=0` · `unique_ratio=1` ·
  `sample_ok=false`). A topic with `trend_use=false` (`추천_재구매`·`선크림` — they catch 76% and 93% of
  the videos respectively and so have no discriminating power) is used only as a filter and a genre marker
  and has no row in this table. Two invariants therefore hold, and the view
  `needs.metrics_topic_quarter_violation` (`db/views/`) asks them back against the stored rows — it holds
  when the view is empty.
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

The verdict **presupposes the dense grid** of §The quarterly table's row set: `신규 등장` takes the three
preceding quarters, `사라짐` takes the highest quarter of the whole period and `채널 확산` takes the same
quarter of the previous year out of that topic's history. If a 0-mention cell is not left as a row, that
lookup meets a blank, and a blank is not 0 but "unknown", so the verdict quietly differs.

- **evidence_strength** = `W_EVIDENCE.documents * min(1, 근거 수 백분위)` + `W_EVIDENCE.channels *
  min(1, channel_count / denom_channels)` + `W_EVIDENCE.unique * min(1, unique_ratio)`, 0~100.
  - `근거 수 백분위` is the position (0~1) at which `mentions` sits **within that source**. Where several
    values are equal it is given the middle of that stretch. One absolute threshold is not used because the
    scale differs per source (measured on this corpus: video median 16 · comment median 62), and so this
    term depends **not on one row but on the whole row set of that source** — the second reason the verdict
    is a run-level derivation.
  - **The channel term is `channel_count / denom_channels`.** That is a **third** ratio, different again
    from the two channel ratios `channel_diffusion` uses. Mixed up, the three give a different number with
    no error, so they are written apart here:
    | where | numerator | denominator | source-dependent |
    |---|---|---|---|
    | `evidence_strength`'s channel term | `channel_count` (the channels that emitted that topic in that row's source) | `denom_channels` | **yes** (a comment row and a video row hold different values) |
    | `channel_diffusion`'s first term (breadth) | the number of **video** channels that emitted that topic | `denom_channels` | no (the two rows hold the same value) |
    | `channel_diffusion`'s second term (evenness) | the Shannon entropy of the per-channel **video** mention distribution | `ln(the number of channels in that distribution)` | no (the two rows hold the same value) |
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
  1. If it is that source's **last quarter**, `미확정(진행 중)`. Being in progress, its document count is
     not yet full.
  2. If `evidence_strength < EVIDENCE_FLOOR` or `mentions < MIN_DOCUMENTS`, `근거 부족`.
  3. If the three preceding quarters **exist** and the `composition` of all three is below
     `NEW_TOPIC_MAX_SHARE` and `mentions >= MIN_DOCUMENTS` and `channel_count >= 2`, `신규 등장`.
  4. If `velocity_yoy` is NULL, `판정 보류` (there is nothing to compare against). **Standing after 3 is
     the point** — a topic that has just appeared normally has no sample in the same quarter of the
     previous year, so letting that cell fall through to held would stop `신규 등장` ever standing.
  5. If `velocity_yoy > TAU`, then `단기 피크` when `persist_quarters == 1`, otherwise `급상승`.
  6. If there is a row for the same quarter of the previous year and
     `channel_diffusion - the previous year's same-quarter channel_diffusion > DIFFUSION_TAU` and
     `velocity_yoy <= TAU`, `채널 확산`.
  7. If `abs(velocity_yoy) <= TAU` and `persist_quarters >= 3`, `지속 인기`.
  8. If `velocity_yoy < -TAU` and `composition < (that topic's highest composition over the whole period) / 2`,
     `사라짐`.
  9. If nothing catches it, `판정 보류`.
- **The type vocabulary is nine, and seven of them are "types".** The other two (`판정 보류` ·
  `미확정(진행 중)`) are not types but say that no verdict was made. `judged` = is it among the six left
  when `근거 부족` is taken out of the seven.
  Seven types: `급상승` `사라짐` `지속 인기` `단기 피크` `신규 등장` `채널 확산` `근거 부족`
- **hold_reason** — why `판정 보류` came out. Left blank, the hole in the rules cannot be seen. The closed
  vocabulary is four: `no_prior_year` (전년 동분기 표본 부족, order 4) · `above_half_peak`
  (`velocity < -TAU` but the composition is at least half the highest quarter's, so it cannot enter
  `사라짐`) · `within_tau_short_persistence` (the change is within TAU but `persist_quarters < 3`) ·
  `no_rule` (규칙 미해당). A row that is not held is `''`. ydc writes this reason as one sentence for a
  person to read and slips the highest quarter's composition into `above_half_peak`, but that number is a
  derivation that comes back out of the same run's `metrics_topic_quarter`, so it is not stored here.
  - This column has actually shown up one hole in the rules: the largest fall on this corpus
    (`톤업_메이크업베이스` comments, `velocity_yoy = -0.56`) falls into `above_half_peak`. That is because
    `사라짐` demands the two conditions **together**. Adding a type is a matter for the team to agree, so
    the rule is left as it is and only the reason is recorded.
- **opportunity_score** = the four terms brought onto 0~1, weighted and summed, then min-max normalised to
  0~100 **within that source**. 1 decimal place. The set that gets a score (`scored`) is those cells of
  that source whose `velocity_yoy` is not NULL, that are not the last quarter, and whose `trend_type` is
  neither `근거 부족` nor `판정 보류`. Every other cell is NULL — **not 0.** 0 means "the lowest
  opportunity" and NULL means "it was not scored".
  `raw = W_SCORE.velocity * (velocity_yoy - min) / (max - min) + W_SCORE.persistence * persistence
  + W_SCORE.channel_diffusion * channel_diffusion + W_SCORE.evidence_strength * evidence_strength / 100`,
  where `min`·`max` are the `velocity_yoy` range inside `scored` (a width of 0 is taken as 1.0). The stored
  value is that `raw` min-max normalised inside `scored` again. **So this score is comparable only within
  that output** — it is run-relative in the same sense as `persistence`, and comparing its size against
  another run's score is wrong. A cell can be `judged` and still have a NULL score (2 cells measured over
  everything): `신규 등장` stands before order 4, so it is judged with `velocity_yoy` still NULL.
- **gap_pp** = `100 * (youtube_comment 의 composition - youtube_video 의 composition)`, 2 decimal places.
  Being a (topic, quarter) fact, **the two source rows hold the same value.** When one source has no row
  for that (topic, quarter) it is NULL. This column is the reason the two series are not mixed by a
  weighted sum such as 0.6:0.4 — the gap itself is the signal (`백탁` is 0 of 13 quarters on video against
  12 of 13 on comments, and mixing them makes that emptiness disappear).
- **single_source** — was this verdict made looking at one source only. In v1 it is **always true**. That is
  because `source_count < 2`, one of TEAM_DECISIONS_v0.2 §3.2's three `근거 부족` conditions, is **not
  applied**, and because inside YouTube the videos and the comments are not mutually verifying sources but
  two series of different character (a description is specifications and formulae, a comment is how it felt
  and what was wrong). A column whose value is always the same is kept because **the fact that the gate is
  off** has to be readable from the row — the day NAVER and commerce join and this column becomes false,
  that condition turns on.

### Verdict constants (gathered in `analysis/judge` alone, and `tests/test_judge_constants.py` compares this table against it)
These are the five that #3's grade-A review handed over as "an artefact fitted on the stored values".
Below is the answer to **what each value came out of** and to **whether it is adopted as it stands or
fitted again**; the reproduction was done by fork #40 on 2026-08-26 over the original output
(`reports/trend_sunscreen_v0.2.csv`, 338 rows = this table's 338 rows, compared by #5 with a cell
difference of 0).

| constant | value | what it came out of (the reproduction) | verdict |
|---|---|---|---|
| `TAU` | `0.35` | the 75th percentile of the observed `abs(velocity_yoy)` distribution. **The grounds are that drawing it separately per source gave almost the same value** — the reproduction: video 76 cells, median 0.215 · 75th **0.366** · 90th 0.594 · max 0.887; comments 108 cells, median 0.218 · 75th **0.357** · 90th 0.526 · max 1.290. **Everywhere in this table a percentile uses the same `sorted(v)[int(q*n)]` definition as the `DIFFUSION_TAU` row below** — counted with `statistics.median` the medians are 0.207/0.216, and the two numbers are two answers under different definitions rather than a rounding difference (fork #41 corrected this). The comment 90th percentile is the number TEAM_DECISIONS_v0.2 §3.1.1 wrote as 0.525 (measured 0.5257 — the table truncates, this rounds, and it does not reach the cut). The rest are the same numbers as that table | **Adopted as it stands.** Fitted again it comes back at 0.357~0.366, and bringing the two down to one and fixing it was the team's decision. **Drawing it once and fixing it** is the point of this value — drawn again at every output, the top 25% would always be `급상승` even in a quiet quarter |
| `DIFFUSION_TAU` | `0.089` | the 75th percentile of `abs(Δchannel_diffusion)` over the **234 cells** that have a row for the same quarter of the previous year (13 topics × 9 quarters × 2 sources). The reproduction: n=**234** · median **0.042** · 75th **0.089** · 90th **0.496** — the same as the three numbers in `judge.py`'s comment down to the decimal place. A percentile is drawn with `sorted(v)[int(q*n)]` | **Adopted as it stands.** Left at 0 (= "rising at all is diffusion"), **52 of the 89 judged cells (58%)** pile into `채널 확산` alone and the classification loses its information. At this cut, 52 → **14 cells** (the reproduction agrees). When sources are added it has to be drawn again from that source's distribution |
| `EVIDENCE_FLOOR` | `50.0` | **not a fitted value.** It is the middle of the 0~100 scale that came from v1, and TEAM_DECISIONS records the value alone. Measured on this corpus: `evidence_strength` median 59.95 (`statistics.median`; under the `sorted(v)[int(q*n)]` definition of the `TAU` row above it is 60.1 — fork #41 corrected this), 111 of the 338 cells (33%) are `근거 부족`, and **51 of those are caught by this cut alone** (the cells caught by `mentions < 5` alone are **0**). Sensitivity: 40 → 73 cells · 50 → 111 cells · 60 → 156 cells | **Adopted without refitting — because there is no right answer to fit to.** `backtest.csv`'s 11 rows look only at the hits of cells that were **already judged** (their `trend_type` is only 급상승·신규 등장·사라짐·단기 피크) and say nothing at all about a `근거 부족` verdict. Writing down here that the only grounds are "the team agreed" is this row's job |
| `MIN_DOCUMENTS` | `5` | **the same number** as the gate of `metrics_topic_quarter.sample_ok` (022's `CHECK (sample_ok = (mentions >= 5))`, the `velocity_yoy` condition in §Formulas) | Adopted but **not defined separately** — `analysis.trend.MIN_MENTIONS` is taken as it is. Measured, this gate alone filters 0 cells, so it is entirely hidden behind `EVIDENCE_FLOOR` |
| `NEW_TOPIC_MAX_SHARE` | `0.01` | TEAM_DECISIONS §3.2's "composition of the three preceding quarters < 1%". Not a fitted value but a number the team agreed on because it reads well. Sensitivity: 0.005 → 1 cell · 0.01 → 5 cells · 0.02 → 10 cells | Adopted. What is written down is that the only grounds are the agreement |
| `W_EVIDENCE` | `documents 43.75` · `channels 31.25` · `unique 25.0` | **a renormalisation** of v1's four factors (evidence count 35 · channels 25 · non-duplication 20 · product and topic match confidence 20) that **drops the fourth, which cannot be computed without `entity_link`, and divides the remaining three by 0.8** (35/.8 · 25/.8 · 20/.8). The arithmetic is itself the grounds, and a test checks that division | Adopted. Laying the fourth term down as 0 would quietly cut 20 points off every topic and make `EVIDENCE_FLOOR` misfire. When `entity_link` exists it goes back to the original four-factor plan |
| `W_SCORE` | `velocity .35` · `persistence .25` · `channel_diffusion .20` · `evidence_strength .20` | **not a value fitted on this corpus.** It is the v1 agreed value, on TEAM_DECISIONS_v0.2 §1's list of "what is adopted from v1 as it stands" | Adopted. What is written down is that the only grounds are "the v1 agreement" — beyond the four summing to 1.0 there is no measurement supporting this value |

- Carrying **the values alone** out of the table above is a failure of this contract. Without each row's
  third column there is nowhere to answer "why 0.35".
- When a verdict constant changes, the definition has changed, so `analysis_run.versions.judgement` is
  raised (`versioning.md`). ydc records the same fact per row in a `tau`·`diffusion_tau` column, but this
  repository, following A19, puts no `*_version` column on an aggregate or derived table, so the run is
  that place.
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
- **A flip** = a cell among the verdict targets whose two deltas differ in sign and where at least one side
  moved by `MATERIAL_PP = 0.5`%p. Without demanding a width, every cell that crosses back and forth near 0
  is caught as a flip — over everything one cell really is caught that way (`youtube_comment` / `백탁`,
  −0.03 → +0.03). 0.5 is the smallest width that is visible within the observed three-year range of change
  (−5.5 ~ +2.6%p).

### Backtest (`BacktestRow` — ydc `backtest.py`)
- What it asks: whether the verdict is **"it could have been known then"** rather than "so it turned out,
  looking back". The metrics are recounted as if only up to a past quarter C were known, the preceding
  quarter T is judged, and whether that direction held over the `HORIZON` quarters after C is looked at.
- **The cut is at C, not at T.** The verdict leaves the last quarter as `미확정(진행 중)`
  (§Verdict order 1), so judging T needs data up to C = the quarter after T. Operations run that way too.
- **The cut is the point.** `persistence`'s baseline is the whole-period median (§Formulas), so judging the
  past without cutting amounts to fixing the baseline by looking at quarters that have not arrived.
  `velocity_yoy` uses the same quarter of the previous year alone, so it leaks nothing.
- Only the types that have a direction are verified: `급상승`·`신규 등장` (상승 유지) · `사라짐`
  (하락 유지) · `단기 피크` (피크 소멸). `지속 인기` and `채널 확산` are left out because they describe a
  state rather than predict a direction — put in, they inflate the hit rate.
- **Two baselines are emitted.** Baseline A's preceding window contains the risen quarter T itself, so a hit
  means "it has to rise further than T" and regression to the mean alone produces a miss. Baseline B
  (`before_excl_pp`) compares against the preceding window with T taken out and asks **"did the level it
  rose to hold"**. The two are different questions, and emitting only one of them would be choosing the
  result. For `단기 피크` the two baselines become the same question (did it fall below quarter T).
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
- **The rules that were dropped**: measured again, the phone-number regular expression (6 hits) and the
  gambling-and-loan dictionary (4 hits) caught almost nothing but false positives (`토토톡` · `40대출산맘` ·
  `무향`). False positives are not left in to catch 0.01%. The common spam kinds (gambling, stock-tip rooms)
  are not on this panel.
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
| `MATERIAL_PP` | `0.5` | the smallest width visible within the observed three-year range of change (−5.5 ~ +2.6%p). Not a fitted value but a reading threshold | Adopted. Left at 0, the `백탁` comment cell (−0.03 → +0.03) is caught as a flip over everything and turns the answer over |
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

**Engine choice**: this use takes **none** of the three. `corpus_mention` already holds the answer, so
there is no ranking to choose. What S6 gives #11 is not grounds for a default engine but the opposite:
**a concrete use, gathering evidence, does not need search's ranking.** So
**the two rows above are not used as an input to #11's default-engine decision** — the input to that
decision is the six rows of §Retrieval measurements, measured with the same yardstick over every source.
Search has a place of its own when the corpus is searched with words the dictionary does not have
(`허옇게 떠요` rather than `백탁`), and that place is heldout mode, the one place where vector cleared 0
(§Retrieval measurements).

### Opportunity cards (the rules decide the type — `cosmai trend cards`, ydc `cards.py`)
- **Zero cards is not a failure.** It is the normally computed answer after every rule has run, and in
  this sample too 9 of 13 quarters have none. Why the exit code does not say so, and the one case that
  becomes `partial(1)` instead (**a cell the rules caught but with no evidence text to stand a card on**),
  are in the evidence-and-cards section of `entrypoints.md` — the same seat #41 pinned in the sensitivity
  section with "shaking is not a 1".
- **A card makes no rows.** It is a render of the three tables already stored (`metrics_topic_quarter` ·
  `topic_quarter_judgement` · `topic_quarter_evidence`), and one more table would make the same number live
  in two places and fight over which is canonical — design principle 2 of ydc `cards.py` ("every number is
  taken as it stands from an output already made") is this sentence on the storage side. It is not dropped
  as a file either (the same seat as `retrieval terms`): being a snapshot of a growing corpus it goes stale
  in the repository, and whoever wants to keep one redirects it. That it has a column a person writes in
  (`accept / watch / reject`) is another reason this output is not a table — no table in this repository
  owns that decision yet.
- **The rules assign the type. An LLM does not judge "this is an opportunity"** (design principle 1). Where
  a summary sentence would go, the evidence's original text is carried as it stands. **With no evidence
  text, no card is made** (design principle 3).
- The type vocabulary is six and **only four can stand.** In the same sense as the verdict order, caught
  higher up it stops there.

  | type | rule | in this repository |
  |---|---|---|
  | 표현 공백 | the product's full-ingredient share / comment composition >= 5× | **cannot stand** — there is no source for the full-ingredient axis (ydc `ingredient_axis.py`) |
  | 제품 공백 기회 | `gap_pp >= GAP_PRODUCT_GAP` and the comment cell is a judged cell | stands |
  | 검증된 성장 | one of the two sides is `급상승`·`단기 피크` and `abs(gap_pp) < GAP_PRODUCT_GAP` | stands |
  | 단기 유행 위험 | the same condition with the gap at or above that | stands |
  | 포화 시장 | both sides are `지속 인기`·`채널 확산` and the comment composition >= `SATURATED_COMPOSITION` | stands |
  | 선행 연구 기회 | the paper series runs ahead and consumer mentions are low | **cannot stand** — held in ydc too, for want of the data |

  The two that cannot stand are not struck from the vocabulary because the rule stands as it is the day
  their input arrives. Laying a missing input down as 0 makes that type quietly never come out again (the
  same sentence as not laying `W_EVIDENCE`'s fourth term down as 0).
- Two constants — `GAP_PRODUCT_GAP = 2.0` (%p) · `SATURATED_COMPOSITION = 15.0` (%). Both are ydc
  `cards.py`'s values and **not fitted values**: being the handles of a report, they are numbers the team
  chose because they read well. The same seat as `EVIDENCE_FLOOR`, and writing down that the only grounds
  are the agreement is this row's job.
- **The quotation order is not the stored `rank` (likes) but (alias specificity, likes).** Aliases are
  written in the topic dictionary from the specific one first (`발림성`'s `발림성` comes before `제형` and
  `텍스처`), so a comment caught by a general word goes to the back. But the ceiling is 3, so this secondary
  sort **does not choose, it only lines them up again** — what ydc's comment worried about, "a comment
  caught by a general word being picked because of its likes", is stopped only by raising `TOP_PER_CELL`.
  What is done now is to carry that fact on the card as a limitation sentence.
  The general-word list that limitation sentence looks at (`발림성`↔`제형`·`텍스처` and the like, the four
  rows ydc's measurement produced) lives as a constant in `analysis/cards`. **It is not a stopword list for
  the index and extraction axis** (what fork #37 disposed of is that axis) — it is an annotation a card
  attaches telling itself to doubt its own evidence, and its proper place is the topic dictionary's `extra`
  (fork #8). Moving it there means raising the dictionary version, which this step does not do.
- One type is one card (three cards of the same type are as good as one in a demo). The strength that lines
  them up differs per type — `제품 공백 기회` uses `abs(gap_pp)`, the rest use `opportunity_score`. Lined up
  by score alone, the card of a cell whose score is NULL is always pushed back, and that card stands on the
  gap rather than on the score.
- The limitations go inside the card (design principle 4): `hold_reason` · `single_source` · the recent
  quarter's undercounting · general-word aliases. The source of the crosscheck set ydc carried alongside
  (commerce composition, survey positive rate, ingredient composition) was stood up afterwards by
  §Crosscheck — only, the card does not carry that answer yet (that would change the card body, so #7 did
  not do it). The transcript gain is still absent for want of a source. The fact that it is absent stays on
  this line, and does not stay on the card as an empty section.

## Crosscheck (the sources put side by side — three answers that are not stored, fork #7)

The three (ydc `source_composition.py` · `commerce_crosscheck.py` · the ingredient axis of
`cross_source.py`) are **not summed.** The denominator differs per source — a composition is against that
source's sum of mentions over the 13 topics, a platform attribute rating is the response share inside a
`topic_group`, and an ingredient discourse is a document count. Added or averaged, they lose their meaning
on the spot. So what is looked at is **the rank and the direction, not the size**. **Where they disagree is
the R&D gap** — being an asymmetry of the present state rather than a prediction, it is the kind that
survives the backtest of §Sensitivity.

**Nothing is written to any table. This promotion's DDL is 0 files.** The same seat as §Sensitivity, but
the column that does not fit is a different one: a row of these three answers is keyed by one (topic) or one
(ingredient), while 022's quarterly grain is keyed by eight columns. The commerce side has **neither the
quarter nor the roster** among those eight — review collection is biased to the recent (2026 is **86.0%** =
25,818/30,021, measured 2026-08-27), so splitting by quarter produces a "2026 explosion" that is an artefact
of the collection method, and the platform attribute rating's observation window is only a few days. The
ingredient axis has no `topic_key` axis at all (`INGREDIENT_KEYS` is not a registry of
`aspect_lexicon(ruleset='retrieval-topic')`).

Making a new table is itself within additive-only scope — #6 made 025 that way. What carries the weight is
not that but **the grain and the recency bias**: to store it, the row would have to carry what point in time
and what the ratio is about, and commerce review collection is biased to the recent (2026 is **86.0%** =
25,805/30,008, measured 2026-08-27) while the attribute rating's observation window is a few days, so
whatever time column is attached, what that column means differs from `metrics_topic_quarter`'s quarter.
Putting a value of a different meaning into a column of the same vocabulary is what is stopped here. So the
output is not a table but **an answer** (the stdout of `cosmai trend crosscheck`), and being read-only it is
run against the production DB as it is.

**Limitation**: of the three answers, §Composition alone does have a real seat, `(snapshot, source, topic)`
— it has no time column, and its source is `retrieval_chunk.source` vocabulary, so it does not collide with
022. It was not made a table because it is the answer of one command together with the other two, and this
is written here so the next person can ask this judgment again.

**Where the four sources stand** (the YouTube comments, transcripts, commerce reviews and ranking of issue
#7's completion criterion):
| source | from where | what it does in this command |
|---|---|---|
| YouTube comments | `retrieval_chunk(source='youtube_comment')` | composition — the consumer reaction side |
| YouTube transcripts | `retrieval_chunk(source='youtube_transcript')` | composition — **the creator side** |
| commerce reviews | `retrieval_chunk(source='commerce_review')` | composition — the real-use side · ingredient discourse |
| commerce ranking | `trend_radar.rank_snapshot` | **it fixes the commerce side's suncare population** |

That the ranking fixes the population is the point. ydc chose by whether a sunscreen alias was in the
product name, but fixing a population by a name substring is **the same mistake** as the `시카` incident of
§Ingredients (a short alias catches something else). Here the population is the products the platform
actually put on its suncare boards and categories, and the predicate is `SUN_BOARD='suncare'` and
`SUN_CATEGORY` (a `category_name` holding `선케어`·`선크림`·`선블록`·`선스틱`·`선쿠션`).

### Composition (`SourceShare` — ydc `source_composition.py`)
- What it asks: **how much each source speaks of the same topic.** YouTube alone cannot answer "is this
  topic really important" — verifying a value made from mention counts with mention counts is circular, and
  inside YouTube the transcripts and the comments share a bias, being the same platform. A different source
  has a different bias, so several sources saying the same direction is itself grounds.
- **All that differs per source is the character of the documents; the computation is fixed as one.** The
  dictionary is the one active topic dictionary (`analysis.retrieval.topics.match_topics`, `trend_use`
  topics only) · the unit is **one document** (once, even where the same topic catches in several chunks of
  one document) · the denominator is that source's sum of documents mentioning a `trend_use` topic.
  Document counts are not summed across sources.
- **`metrics_topic_quarter` is not read.** A stored composition is a value that has passed through the panel
  roster, the sunscreen filter and the quarter, so its computation differs from the commerce side's, and
  putting two values from different code paths side by side leaves it undecidable whether the difference
  belongs to the source or to the path. That the four sources ride **one and the same function** is the
  whole of this block.
- **The creator side is `youtube_transcript`.** ydc's `youtube_video` was the video description, but our
  `youtube_video` chunk is **one line of title** (`VIDEOS` in `analysis/retrieval/corpus.py` draws `title`),
  so it is no vessel for creator language — 5,908 documents carry only 1,123 topic mentions. So the two
  rules ydc put in the `video` seat run over the transcripts. The title column stays in the table but bites
  no interpretation rule.
- The two interpretations are ydc's sentences as they stand: `commerce >= 5 and creator < 2` → "not
  observable from the video description · present in real-use speech alone" · `|commerce − creator| >= 5`%p
  → which side speaks of it far more. `cross_source`'s "영상은 안 다루는데 댓글·리뷰에는 있음"
  (`commerce > 0 and creator < 0.5 and comment > creator * 3`) comes along too.

### Rating (`RatingRow` — ydc `commerce_crosscheck.py`)
- What it asks: **a verification independent of mention counts.** Every verdict of ours came out of mention
  counts. `trend_radar.review_topic` is the attribute rating oliveyoung and daiso aggregate from their own
  review surveys, and so is the only verification material independent of mention counts.
- **The direction is looked at, not the value.** The two indicators have different denominators — our
  `composition` is a composition across topics, while the commerce `share_pct` is the response distribution
  inside a `topic_group`. So our side is turned into a rank and `gap_pp`, and the commerce side into a
  positive rate, before they are interpreted.
- **A row whose `share_pct` is NULL is not taken.** That source carries a weight (`score`) instead of a
  share, and a weight and a percentage are different units, so mixing and averaging them shows nothing and
  is wrong (the DDL comment on `review_topic.score` already carries that sentence). Over everything that
  source is **10,920 rows · 35 products** (measured 2026-08-27 — the collector keeps writing, so this number
  grows. What carries the judgment is not the row count but the fact that `share_pct` is NULL).
- **Being a per-point-in-time snapshot, only the row with the latest `captured_at` per (product, option) is
  used.** The same option piles up one row per collection time, and an attribute rating changes only as
  reviews pile up, so the values across times are nearly the same. Counted whole, the product count is
  inflated by the number of times.
- **The canonical form of polarity is the table a person checked
  (`analysis/crosscheck/audit/polarity_v1.csv`), and the hint is a last resort.** Using the hint alone, as
  ydc does, catches **the same disease** as the ingredient keys — both are substring lists over vendor
  strings. Feeding the hint alone to the 23 group vocabulary items of the production `review_topic`'s
  `GROUP_MAP` (measured 2026-08-27), **five came out inverted**:
  | group/option | the hint's answer | the right answer |
  |---|---|---|
  | `자극도/자극이 있어요` · `보습력/약간 건조해요` · `지속력/예상보다 짧아요` · `커버력/예상보다 짧아요` | positive | negative |
  | `가루날림/날림이 없어요` | negative (the `없어요` hint) | positive |
  Five of `GROUP_MAP`'s seven groups have at least one inverted label. **Today's values are right** — only
  the three options that classify correctly come into the suncare set, and laying the checked table on top
  leaves 71.7 / 74.5 as they were. But the reason `GROUP_MAP` exists is the day the remaining groups arrive,
  and on that day the positive rate quietly inverts (with `보습력` in, the hint alone gives 80% against a
  real 40%). Putting `수분감/매트해요` on negative is a judgment — **the axis is moisture, so this is the
  low end of that axis**, which is a different question from mattness being a virtue as a product
  preference.
- A phrase the table does not know is answered by the hint (answer nothing and that product disappears
  whole), but the fact that such a phrase arrived is said by `tool/measure-crosscheck-keys` — **the same
  tool and the same convention** as the ingredient keys. Give it no group and the hint alone runs, and
  because that is the same answer as ydc's, the 1:1 of `tool/compare-ydc-crosscheck` stands as it is.
- The positive rate = the share the positive options take inside one product and one `topic_group`, and it
  is 0 where there are only neutral ones.
- The quarter it crosschecks is **the second from last of that run's grid**. The last quarter is the one in
  progress, which the verdict leaves as `미확정(진행 중)`, so it is undercounted. It is not taken as an
  argument for the same reason as in `quarter`·`judge` — two ways of choosing make two denominators.
- **No interpretation is written for a topic under `MIN_PRODUCTS = 5`.** Demanding `document_count >= 5` of
  our own verdict and then making an exception for this crosscheck alone is a double standard. That fact is
  carried by the table and the `note`, not by the exit code.

### Ingredients (`IngredientRow` — the ingredient axis of ydc `cross_source.py`)
- What it asks: **is the place that speaks of an ingredient the same as the place that uses it.** ydc laid
  down four — NAVER search, papers, suncare formulations and discourse. Here **only the three discourse
  axes** stand (YouTube as a whole · the sunscreen context within YouTube · commerce reviews). The other
  three do not stand in this repository, for the reasons below.
- **There is no NAVER axis.** `needs.naver_datalab_point` has 0 rows — the collector (#9) has not run yet.
  Filled with 0 a missing value looks like a present one, so the column itself is not put there (the same
  rule as ydc leaving an ingredient with no search term blank).
- **The paper axis is off, as `PAPER_HOLD`.** ydc's corrected grounds are carried over as they stand: **the
  numerator is an all-field search term (a survival rate of 20~48%) and the denominator `cosmetic` is a
  cosmetics search term — a value was divided by one of a different population.** The "`cosmetic` survival
  rate 100.1%" the first edition recorded as grounds is **not used** (the filter is
  `AND (skin OR cosmetic OR dermatology)` and the search term is `cosmetic`, so it is an identity that
  filters nothing at all). The source itself is not in this repository either.
- **The formulation axis is locked by `FORMULA_HOLD`.** There are 180 products with
  `trend_radar.product.ingredients`, and **2 of them are suncare** (measured over everything 2026-08-27).
  Making an adoption rate out of the 180 stands a ratio of a different population under the name "suncare
  formulation adoption rate", which is exactly the error `PAPER_HOLD` just above corrected. When an
  ingredient dataset is settled (#10) it is turned on by the same command. **The audit path stands now, for
  that day.**
- **A two-character alias is not used as a substring of an ingredient name.** This is ydc `v0.3.0 e5a1b00`'s
  correction, and **we audited our own table again and confirmed the same mismatches** (2026-08-27, 180
  products · 22,705 ingredient rows · 2,051 distinct names):
  | alias | what it catches in our table | ydc | disposition |
  |---|---|---|---|
  | `시카` | **all 216 rows are 트라이에톡시카프릴릴실레인 (209) and 트리에톡시카프릴릴실란 (7)** — a silicone dispersant | all 263 rows the same substance | dropped |
  | `레티놀` | 7 rows. `레티날` has 0 rows and the two are different substances | 8 rows | dropped |
  | `센텔라` | **0 rows.** The ingredient list writes 병풀·마데카소사이드·아시아티코사이드 | 0 rows | kept in the key — it is merely 0 rows, and that is why `병풀` was needed |
  The corrected keys are `"레티날": ("레티날",)` and
  `"시카센텔라": ("병풀","센텔라","마데카","아시아티코","아시아틱")`.
- **ydc's `[의심]` rule does not catch this accident — it was not carried over.** That rule is "does the
  caught ingredient name contain none of the key", but the matcher folds case and whitespace while the rule
  looks at the original text, so all it can really catch is a folding artefact (`pdrn` against `PDRN`).
  `시카` in fact **satisfies** that rule — 트라이에톡시카프릴릴실레인 really does contain `시카` inside it.
  What caught it was not a rule but **a person reading the names it printed**.
- **So the machine gate is a list a person read once and forbade.** Catching on that list is `key_mismatch`
  and exit code 1 (§entrypoints). Catching 0 rows is an absence rather than a mismatch, so it passes —
  `레티날`·`PDRN`·`엑소좀`·`트라넥삼산` are in that seat in our table. The forbidding has two layers:
  | layer | what | why |
  |---|---|---|
  | `DENIED_NAMES` | 트라이에톡시카프릴릴실레인 · 트리에톡시카프릴릴실란 | substances no key may catch |
  | `DENIED_FOR` | (`레티날`, 레티놀) | **the unit of forbidding is not a substance but a (key, substance).** Made global, one measured line turns an innocent key red — in an ingredient list separated by whitespace alone, `벼에스에이치-올리고펩타이드-1   * 레티놀 함량 509 IU/g` is one name whole, so the `펩타이드` key catching it is not a mismatch |
  The gate is **as wide as the matcher** (a substring with whitespace and case folded). Asked as an exact
  match, the gate would not see the `레티놀(0.04 ppm)` the matcher caught, and since **4 of the production
  table's 7 `레티놀` rows are already that suffixed form**, the gate would fall silent the day the 3 bare
  `레티놀` rows disappear.
- **The place that list cannot see — a mismatch not yet known — is carried by
  `tool/measure-crosscheck-keys`.** The canonical form of what a key catches is
  `analysis/crosscheck/audit/known_names_v1.csv` (the **190 names** a person read and confirmed off the
  2026-08-27 production table), and that tool measures the table as it is now and holds it against the list:
  when a name that is forbidden, or not on the list, comes into some key, it exits **1** and prints that
  name and its product count (a name that was there and has gone is not red — a product dropping out is not
  a mismatch). **CI cannot do this job**: it cannot reach the production table, and a few strings pinned in
  a fixture do not break as the corpus grows. What CI carries is only the list's **self-consistency** (does
  every name really catch on that key · is anything on the forbidden list · is a key missing).
  It was confirmed by measurement: put `"세라마이드": ("세라",)` into `INGREDIENT_KEYS` and that tool prints
  **카프릴릭/카프릭 트라이글리세라이드 (59 products, an emollient)** and exits 1 — a reproduction of the
  `시카` accident.
- **Two rules for splitting an ingredient list into ingredient names** (a trap only our source has, so ydc
  has no counterpart): a bracketed section marker (`[마데카소사이드] 정제수` · `[시카에센스]`) is dropped,
  being the name of a component of a gift set rather than an ingredient name (`콜라겐` 72→64 rows ·
  `시카센텔라` 179→174 rows are filtered by this) · a comma inside parentheses is not cut on
  (`나이아신아마이드(20,000 ppm)` would split into two ingredients). An ingredient list separated by
  whitespace alone with no commas stays as one lump, and the `note` counts that fact — split quietly, the
  blending order would stand as a wrong value.
- **A discourse count must not be read as "sunscreen discourse".** Counted over the whole index, it holds
  every ampoule and skin booster the same channel introduced. So the ones that have a `SUN_WORDS`
  (`선크림`·`썬크림`·`선스크린`·`자차`·`선세럼`·`선쿠션`·`자외선차단`) **in the same chunk** are counted
  separately. It is the same chunk rather than the same document because one transcript is about 12 chunks
  of 500 characters, so at document grain a whole video becomes sunscreen context. Over everything, PDRN is
  only **149 documents** of YouTube's 933 (16.0%) (ydc had 187 of 1,522). Unlike ingredient-name matching,
  discourse looks at the original text as it stands (ydc `count_terms`) — folding whitespace in free
  sentences joins across word boundaries and creates mentions that are not there.

### Crosscheck constants (gathered in `analysis/crosscheck` alone)
| constant | value | what it came out of | judgment |
|---|---|---|---|
| `MIN_PRODUCTS` | `5` | **the same number** as the sample gate in §Formulas — requiring 5 for the verdict while making an exception for the crosscheck alone is a double standard | Adopted. The same number as `analysis.trend.MIN_MENTIONS`, but it carries its own name because what it counts is products rather than documents |
| `LEAD_PP` | `5.0` | all three branches of ydc `source_composition.reading` use this width | Adopted. In our measurement `백탁` (commerce 9.80 against transcripts 3.32) and `지속력_워터프루프` (1.10 against 6.36) are parted by this width |
| `THIN_PP` | `2.0` | the same rule's threshold for "not observable from the video description" | Adopted |
| `SPARSE_PP` · `TALK_RATIO` | `0.5` · `3` | ydc `cross_source.topic_table`'s "영상은 안 다루는데 댓글·리뷰에는 있음" | Adopted. Not one topic is caught in our table — because the transcripts are thicker than the descriptions, and deleting the rule would leave no grounds to revive it the day the source changes |
| `SUN_SHARE_LOW` | `25` | below this sunscreen-context ratio it says not to read that number as "sunscreen discourse". Our measured PDRN 149/933 (16.0%, **documents**) is below it. ydc's 187/1,522 (12.3%) counts **chunks**, so it does not stand on the same yardstick — it says only that the direction agrees | Adopted. Not a fitted value but a reading threshold — a quarter is the lowest line at which one can say "this is mostly talk about sunscreen", and over everything **all** ten ingredients are below it, so this column today means one warning |
| `POSITIVE_RATE_HIGH` | `80` | the satisfaction threshold of ydc `commerce_crosscheck.reading` | Adopted. Our two measured cells (`발림성` 71.7% · `자극_눈시림` 74.5%) are both below it |
| `GAP_PP_MATERIAL` | `1.0` | the same rule's threshold for the comments saying much more (`gap_pp` = comment composition − video composition, §Verdict) | Adopted |
| `FORMULA_HOLD` · `PAPER_HOLD` | `True` · `True` | the population grounds of the two rows above | Adopted. The conditions for turning them on are #10's ingredient dataset and verification protocol respectively |

### Full measurement (2026-08-27, production DB read-only)
- **These values are a record on topic dictionary v2** (fork #56). v3's delta is `파데프리` alone —
  `톤업_메이크업베이스` becomes +636 (+14.6%), while `선크림`'s five surface forms are `trend_use=false`
  and `속건조` has `new` 0, so neither touches this section. So what has to be measured again after v3 is
  turned on is the two composition rows that use that topic as their denominator (`백탁` commerce 9.80%
  against comments 1.55%) and the `LEAD_PP`·`SPARSE_PP` examples; the rest of the rows stand.
- One run of the command is **12.9 seconds · 150MB peak resident** (2026-08-27
  `cosmai trend crosscheck`, exit code 0). Of that, one walk of the chunks is 381,950 chunks, 48MB,
  **11.3 seconds** (keyset pages of 20,000 rows with a commit per page — the same method as
  `gold_from_chunks` in `analysis/retrieval/eval.py`, and for the same reason). The three commerce-side
  queries are each under 0.4 seconds.
- Composition: commerce reviews 6,349 documents (those of the suncare ranking products' 7,324 reviews that
  have a chunk) · comments 285,735 · transcripts 5,303 · titles 5,908. `백탁` parts sixfold, commerce
  **9.80%** against comments 1.55%.
- Rating: 19 of the suncare ranking products have an attribute rating · 468 rows. The `topic_group` values
  that reach our topics are two, `자극도` and `발림성`, and `피부타입` is outside `GROUP_MAP`.
- Polarity: all 23 option vocabulary items of the `GROUP_MAP` groups are in the confirmed table
  (0 unconfirmed). On hints alone five invert — the table above.
- Ingredients: **0** audit suspicions (on the corrected keys, no key catches the forbidden list). The values
  for the three aliases are in the table above, and the ingredient lists separated by whitespace alone with
  no commas are **60** rows of the 22,705 ingredient rows (59 distinct names).

### Comparison against ydc (run 2026-08-27, 38 lines, **difference 0**)
**The promoted source is not the import pin** (`v0.4.0` `76db718`, `versioning.md`) — `cross_source.py` had its
ingredient keys and its sunscreen context corrected in `v0.3.0` (`e5a1b00`), and that revision is what was
promoted. The pinned copy (`analysis/slices/ydc/`, `v0.1.0`) never held that file and #9 deleted the copy — the
procedure that takes the file out of the tag and runs it **untouched**, and the comparison code, sit in one
place, `tool/compare-ydc-crosscheck`. Three things are put side by side:
- **Ten constants** (the key table · `SUN_WORDS` · `PAPER_HOLD` · `GROUP_MAP` · the two polarity hints ·
  `MIN_PRODUCTS` · the three interpretation phrases) are read straight out of the ydc module and compared —
  one character that went astray while being copied over is caught here.
- **Eighteen rules** (`ranks` · `positive_rate` · five `polarity` · four rating interpretations · four
  `count_terms`) are fed the same input and their answers compared. `count_terms` is among them because
  discourse matching parted from ingredient-name matching (§Ingredients), and that parting is invisible to a
  comparison of constants.
- The three things `cross_source.topic_table` holds **as literals inside the function** (`SPARSE_PP` 0.5 ·
  `TALK_RATIO` 3 · the `READ_COMMENT_ONLY` phrase) cannot be got at by calling the function, so its source
  is read and held against ours. Otherwise "difference 0" would read as covering these three when it does
  not.
- **`polarity` is compared without being handed a group** — our canonical form is the table a person
  confirmed (§Rating) and ydc has no such table. An answer with no group is an answer riding on hints alone,
  so at that place the two are the same.
- **The audit**: ydc's ingredient-list CSV (31,246 rows · 577 products) is fed as it stands to both audits
  and the (rows, products) per key compared. Being the one place where the source is the same, it is **the
  only 1:1 possible in this issue**, and all ten keys agree (`시카센텔라` 429 rows · 202 products =
  **35.0%** · `레티날` 0 rows — not the 41.1% of `시카` before the correction).

**CI cannot hold the full comparison**: both ydc's ingredient-list CSV and the panel run are in that
repository and are not to be put in this one. So what CI carries is the catch sets of the rules and the keys
(`tests/test_crosscheck_rules.py` · `tests/test_crosscheck_keys.py`), and holding the sources against each
other is run once by a person.

### `commerce_ranking.py` is not promoted (issue #7 §확인할 것 — the disposition is **held**)
It overlaps `analysis/aggregate/ranking.py`. `rank_daily` already writes into `needs`, per (source, board,
category, product, date), `n_present`·`present_share` (= the material for ydc's observation density,
entries and exits) and `rank_mean`/`rank_min`/`rank_max` (= `best_rank`/`worst_rank`/`swing`), and all ydc
adds is three window-level roll-ups on top (`swing`·`moved`·`entered`/`left`). Making a second output path
for a value that comes out of the stored rows in one line of SQL is fighting over the canonical form on the
spot. If a screen wants those three values, a view is the answer, not a promotion.

## Holdout (asking again with a new sample — an answer that is not stored, fork #51)

The promotion of ydc `holdout_commerce.py`. **It verifies the existing conclusion with newly piled-up
commerce reviews — it does not replace the numbers.** With the same code it counts **only the reviews never
seen** and looks at whether the existing ratios reproduce. Reproducing means the conclusion is not resting
on the sample; not reproducing is the more important finding. Where §Sensitivity shakes by the panel, the
window and the ad marking and §Crosscheck puts the sources side by side, this one shakes by the **sample** —
the three have to stand in the same place for "the grounds for trusting a verdict" to be one set.

**Nothing is written to any table. This promotion's DDL is 0 files.** On top of the reason §Crosscheck gave
(the grain and the recency bias) there is one more: a row of this answer is keyed by (arm, topic), and
**the boundary of the `arm` is our chunk index.** That boundary moves with every run of `cosmai retrieval
chunk` (today's holdout is tomorrow's seen), and the value means something only beside the seen arm it was
read with. Stored, it would freeze a moving boundary at one point in time and that fact would not be carried
on the row. So the output is not a table but **an answer** (the stdout of `cosmai trend holdout`), and being
read-only it is run against the production DB as it is.

### What splits the two arms is not a date but the chunk index
ydc split on a **hand-picked cutoff**, `captured_at < 2026-08-24`. That is because "what we saw" is not
left as rows in that repository (the analysis input was a single CSV). We have those rows — the `doc_id` of
`needs.retrieval_chunk(source='commerce_review')` is itself **the roster of the reviews the analysis really
saw**. So our cutoff is not a date but that roster:

| arm | what | why |
|---|---|---|
| `seen` (existing) | reviews whose `doc_id` is in the chunk index | they are the very reviews our outputs standing on the chunk index (§Crosscheck's composition and ingredients, the BM25 index) really counted |
| `holdout` | reviews of the same population that are not in the chunk index | **reviews never seen.** The collector wrote them afterwards |

There are two places where this is better than a date cutoff. **There is no argument** — two ways of choosing
makes two denominators (the same convention as §Rating not taking a quarter as an argument). And `captured_at`
is **the collection time, not the time we saw it**, so a review collected before the cutoff whose chunk was
baked after it falls quietly into the seen arm under a date rule.
`captured_at` is instead **carried as part of the answer** — §Window below.

**The population is the same predicate as §Crosscheck's**: the reviews of the suncare ranking products
`SUN_BOARD`·`SUN_CATEGORY` fix. The two arms have to stand on the same predicate for the difference to
belong to the sample rather than to the filter. **A review with an empty body is taken out of both arms** —
an empty body makes no chunk, so leaving it in fills the holdout not with "reviews not seen" but with
"reviews with nothing to see". The `note` counts how many were taken out.

### The extraction rules — where the stop, the total order and the row-count comparison land
On 2026-08-23 ydc **made a result that was not there, "37% duplicates", with unordered paging**, and then
laid three things on by hand (`count=exact` → paging under a total order → a row-count and uniqueness
comparison). **We do not do those three by hand — because the seat is different.** So as not to write down a
defence that is not there, where each one lands is written here:

| what ydc did by hand | our seat | so |
|---|---|---|
| a row-count comparison after `Prefer: count=exact` | **one transaction snapshot** (`REPEATABLE READ`) | the four reads see the same point in time. Counting and then counting again is an identity in this seat, so it is no check |
| `order=captured_at.asc,review_key.asc` | there is no paging (one query) | ordering is the rule that fixes page boundaries, and there are no boundaries |
| `review_key` uniqueness | `review_pkey (source, review_key)` | it is a DB constraint. Asking it again is an identity |

**In exchange it carries one trap that only this seat has.** A chunk is a derivative of the source and has
no foreign key (020's comment). The chunk stays even when the source review row disappears, so if there is a
**commerce chunk with no source** (`chunk_orphan`) the seen arm is not the arm the analysis really saw — this
output is then not to be trusted and the exit code is **1** (§entrypoints). The collector does not delete
reviews, so the normal state is 0.

**That the four reads (the chunk roster · the review-key roster · the empty-body count · the population) see
one point in time is this section's only machine defence.** The collector and the chunker keep running, so
outside it the arm sizes, the empty bodies taken out and the orphan chunks become counts of different
populations — and then `seen + holdout + empty` is **the size of no population at all**.
`tests/test_holdout_pipeline.py` checks it by having a review another connection committed between two reads
enter neither arm — lower the isolation level and that test goes red.

**There is one read the snapshot does not cover**: the active topic dictionary (`use_active`) is read
separately, before it (§Crosscheck has the same shape). The dictionary changes only by
`cosmai lexicon activate` and, unlike the collector and the chunker, does not run by itself, so it was not
bound in with them; but the fact that "the four reads see one point in time" **does not stretch to a fifth
read** is written down here — if the active version changes during this command, it does not mean the two
arms were matched with different dictionaries (the dictionary is read once and used for both) but that the
`note` does not say which version the table belongs to.

### Metrics (`TopicRow`) — there are two denominators and they are not mixed
The two arms are counted with the same dictionary (`analysis.retrieval.topics.match_topics`, `trend_use`
topics) and the same unit (**one review**). The same topic appearing in one review under several surface
forms is once (ydc `rates`'s rule). Both denominators are **carried, but a difference is not taken across
them**:

| column | denominator | what it answers |
|---|---|---|
| `rate` (mention rate) | **the review count of that arm** | ydc's axis. Did the level move as a whole |
| `share` (composition) | **that arm's sum of `trend_use` topic mentions** | the axis of §Composition. Did the place among the topics move |

The two can part, and that parting is part of the answer — when every topic's mention rate rises together
the composition stays as it was (the collection changed), and when one topic alone rises both move (that
word really did grow). **The verdict is made on ydc's axis (the mention rate)** — for the claim that the
promotion did not change the answer to stand, it has to be the same axis.

**The reproduction count on the composition axis is not carried — that number measures the scale rather than
the stability.** `share == rate / scale` (`scale` = the topic mentions per review of that arm). With a
coefficient above 1 the same `MATERIAL_PP` becomes **systematically looser** on the composition axis (below
1, tighter), and since that value differs per population, which way it goes is not asserted; the coefficient
is printed instead. In the full measurement (2026-08-27) the coefficient was seen **1.4218** (9,027/6,349) ·
holdout **1.3405** (1,307/975), and the reproduction counts on the two axes parted at 7/13 against 10/13.
The three that parted are exactly the three the scale divided — the direction and the size are as they were,
and being divided by 1.4 they failed to clear the threshold:

| topic | Δmention rate (%p) | Δcomposition (%p) |
|---|---|---|
| `발림성` | −2.20 | −0.78 |
| `톤업_메이크업베이스` | −2.01 | −0.95 |
| `무기자차` | −1.61 | −1.01 |

So **10/13 must not be read as independent grounds for 7/13.** The `note` carries the reproduction count
for the verdict axis alone (`reproduced=`) and carries that scale itself instead (`scale=1.42→1.34`). The
difference between the axes is shown by the table putting `Δ률%p` and `Δ비%p` side by side on each row —
**the two numbers are neither subtracted nor are the two axes' reproduction counts compared.** Choosing a
separate threshold for the composition axis would mean fitting again on that axis, and that is not done here.

**The rank has a gate.** A topic whose document count in the seen arm is below `MIN_MENTIONS` (5) gets no
rank (`None`). That the gate bites on **the seen arm** is the point — bitten on the holdout, an existing rank
would disappear because the new sample is thin and the question would stand backwards. In the full
measurement (2026-08-27) all 13 topics passed (`topics=13 ranked=13`), so this gate **never bit once**; it is
kept all the same because the population is fixed by the ranking rather than by the corpus, so the day the
suncare boards narrow, the tail of the seen arm thins too. **That the tail is tied at 0 is a fact of the
holdout arm**, and that seat is carried not by the gate but by `RANK_TOP` below.

### Why they part when they part — the three branches are each measured
This is the order ydc actually walked when the ratios moved as a whole. **The cause is split into three,
each is measured, and what is left as an answer is looked at.**

- **Window** (`window_reading`) — is the holdout **a new period** or **the same window grown longer**. The
  minimum and maximum `captured_at` of both arms are carried, and if the holdout's start is after the seen
  arm's end it is a new period, and if they overlap it is the same window grown longer. This is the question
  ydc wrote as "even the growth is not a new period but only the same window a day and a half longer", and
  on our side it is not declared but **read and answered**.
- **Platform composition** (`PlatformRow` · `StandardRow`) — the second cause of a level rising as a whole is the
  platform composition having changed. Counting is split per platform and **the holdout is reweighted by the
  seen arm's composition** — what is left as the real change is the difference (`residual_pp`) between the
  value with the composition effect taken out (`standardized`) and the seen value. The weights are those of
  **the platforms in the seen arm**, so a platform present only in the holdout does not enter this weighted
  sum (`PlatformRow` carries that fact as a 0).
  **On this population today that mechanism is idle** (measured 2026-08-27): the platforms of both arms are
  only `oliveyoung` and `glowpick`, and `daisomall`, with 6,769 reviews, has **0 suncare ranking products**
  and so does not stand in the table at all. The reason ydc wrote this section (the daisomall weight
  wobbling and dragging the whole with it) **did not reproduce on our source** — that side chose the
  population by name and we choose it by the ranking. So the residual being nearly the same as the raw value
  is this table's answer, and the columns are not deleted because this mechanism comes alive again the day
  daisomall suncare enters the ranking.
- **Product basket** (`BasketRow`) — **this was the real cause in ydc.** The products the collector scraped that
  week were different (48 products in the seen window, 25 in the next, 14 in the intersection). The two arms'
  `product_key` sets and their intersection are carried, and the difference with the basket effect taken out
  is produced **by counting again on the shared products alone**. This is of the same family as the
  `수집 상한 10` — **the collection process makes the observed value.**

### Verdict (`verdict`) — all four branches are exit code 0
The three branches of ydc `holdout_commerce.report` are taken as they stand and our axis adds a fourth. The
criterion is the **mention rate**.
- `재현` — the `|Δrate|` of every topic that clears the gate is at or below `MATERIAL_PP`. Both the level and
  the rank reproduce.
- `순위 재현` — the top `RANK_TOP` is as it was but the level moved. **Our conclusion uses the rank, so it
  holds** — why it moved is answered by the three branches above.
- `순위 변동` — the top `RANK_TOP` parted. **The conclusion has to be looked at again.**
- `순위 없음` — not one topic cleared the gate. **A branch ydc does not have** (all five of its topics had a
  value). Calling a top comparison that spins idle `재현` would claim reproduction on grounds that are not
  there.

**The order the branches are asked in is ydc's: the level comes first.** If every `|Δrate|` is inside
`MATERIAL_PP` the rank is not asked back — the meaning of that threshold is that an order changing within
1.5%p is sample wobble, and asking the rank again there would stand the threshold twice.

**A failure to reproduce is a finding, not a failure.** All four are exit code 0, and which branch it is is
carried by the `note` and the table — **the same place and the same sentence** as "shaking is not a 1" in
§Sensitivity and "disagreeing is not a 1" in §Crosscheck. ydc is the same (`report` always emits 0).

**It does not overwrite an existing output.** This command writes to no table, and it does not recompute the
values §Crosscheck produced either — the seen arm is a value this command counted **afresh with the same
code**, so it is **not on the same footing** as the commerce column of §Composition (that side counts chunk
bodies and goes through `normalize_text`). So the two values are not subtracted. Every comparison inside this
command is between the two arms alone, which rode the same function.

### Holdout constants (gathered in `analysis/holdout` alone)
| constant | value | what it came out of | judgment |
|---|---|---|---|
| `MATERIAL_PP` | `1.5` | `abs(d) <= 1.5` in ydc `holdout_commerce.report` — "the holdout is a third of the size, so there is sample wobble. Above 1.5%p a person looks" | Adopted. Not a fitted value but **a threshold that decides whether a person looks**, and our holdout is smaller than the seen arm too |
| `RANK_TOP` | `2` | `ra[:2] == rb[:2]` in the same function | Adopted. **The `and ra[-1] == rb[-1]` (the lowest matching) attached after it is not carried over** — ydc's five topics all had a value, but the tail of our 13-topic axis is tied at 0 so the lowest place is a draw, and checking that seat would be checking the sort order. **The full measurement (2026-08-27) actually stepped on that seat** — in the holdout arm `혼합자차` and `SPF_PA` are both 0, so their places were fixed by the stable sort following the axis order, and had ydc's rule been carried over as it stands a coin toss would have parted the verdict there |
| `MIN_MENTIONS` (the rank gate) | `5` | **the same number** as the sample gate in §Formulas — requiring 5 for the verdict while making an exception here alone is a double standard | Adopted. The same place as `MIN_PRODUCTS` in §Rating, but what it counts is documents rather than products, so `analysis.trend.MIN_MENTIONS` is taken as it is |

### Full measurement (2026-08-27, production DB read-only — `cosmai trend holdout`, run by the coordinator session)
```
seen=6,349 holdout=975 topics=13 ranked=13 reproduced=7/13 scale=1.42→1.34
verdict=순위 변동 window=새 기간이다 basket_shared=18   (종료 코드 0)
```
- **The cutoff picks out exactly the set §Crosscheck counted**: the seen arm's 6,349 is **the same number**
  as the commerce review document count in §Crosscheck's "full measurement". The two values are still
  neither subtracted nor put side by side (the axes differ — "it does not overwrite an existing output"
  below), but it is evidence that the cutoff parts **the set we really saw** rather than an arbitrary date.
- The roster is **monotonically increasing**, so this cutoff does not slide backwards: `retrieval_chunk` is
  upsert-only and the side that reaps vanished documents runs only on a full walk — a `--since` increment
  does not shrink the seen arm.
- **The verdict is `순위 변동`, and that is the finding** (exit code 0). Why it parted is answered by the
  three branches — the window is `새 기간이다` (the holdout arm starts after the seen arm's end), the
  platform composition mechanism is idle (§Platform composition above), and the product basket's intersection is 18
  products.
- **Not carrying `ra[-1]` (the lowest matching) over earns its keep here**: in the holdout arm `혼합자차` and
  `SPF_PA` are **both 0**, so their places were fixed by the stable sort following the axis order. Had ydc's
  rule been carried over as it stands, a coin toss would have parted the verdict there.
- Whether the extraction rules really moved into the DB was measured alongside: `SUN_JOIN`'s
  `SELECT DISTINCT` means no fan-out (population 7,324 = 7,324 after the join) · no paging ·
  `review_pkey` exists.

### Comparison against ydc (run 2026-08-27, 9 lines, **difference 0**)
`tool/compare-ydc-holdout` takes `holdout_commerce.py` out of the tag (`v0.3.0`), runs it **untouched**
(with `--demo` alone — run with no argument it goes out to that machine's PostgREST) and holds the two
against each other:
- **Three constants.** ydc holds them **as literals inside `report`**, so the function cannot be called and
  its source is read and held against ours (`abs(d) <= 1.5` · `ra[:2] == rb[:2]` · whether the
  `ra[-1] == rb[-1]` we **did not carry over** is still in the original — if it disappears, the grounds
  sentence of the constants table above is misquoting the original).
- **Six rules** (`rates`'s five labels + the rank axis). The (count, ratio) on the same text are held
  against each other. **What is held against what is the way of counting, not the matcher** — is it once
  even where one item has several expressions, is the denominator that arm's review count. The matcher-side
  1:1 (`count_terms`) was already done by #7 and its result is used here to make the input. **The four that
  are not held against each other (`CUTOFF`·`BASE`·`PLATFORMS`·`platform_of`) are printed by the tool under
  §선언 and passed over** — all four are places where the promotion changed the answer (the cutoff to the
  chunk index, the platform from parsing the head of the body to the `review.source` column), and dropped
  quietly they would make "difference 0" read as covering those four.

**CI cannot hold the full comparison** — the source is the production DB. What CI carries is the rules and
the constants (`tests/test_holdout_rules.py`), and holding the sources against each other is run once by a
person (`tests/test_holdout_pipeline.py` carries the shape, the blocks, the exit codes and "did it write
nothing" — the same division as in §Crosscheck).

## Limitations of the population (how to read the numbers — `manifest.limitations`, fork #4)
These are the eight sentences the two runs that gathered the 2026-08-19 corpus (`needs.corpus_*`,
`formats.md` §Corpus snapshot) wrote down about themselves. If §Formulas above says **how** a value is made,
this section says what the made value **is not** — without these sentences in the contract only the numbers
are left later, and the same number gets read as something else. The eight lines stand as constants in
`db/corpus/contract.py` and the loader compares them against the manifest.

- 모집단은 시드 채널 집합이며 전체 YouTube가 아니다(고정 패널).
  - Being a fixed panel, this ratio is not "on Korean YouTube" but "on these 43 channels". That is why the denominator is inside the row (`panel_version`·`panel_role`).
- 패널 밖 신규 채널·신규 브랜드의 등장은 관측되지 않는다.
  - So **the absence of a new brand is not a signal.** Before reading a topic with a low diffusion (`channel_diffusion`) as "it has not spread yet", the possibility that it is outside the panel is looked at first.
- 조회수·좋아요는 collected_at 시점 스냅샷이다.
  - `source_metadata.view_count`·`like_count` are not a time series. `corpus_document.collected_at`, raised to a column, is that point in time, and it is normal for the same video to have a different view count in two snapshots.
- 업로드 플레이리스트 최신순 가정에 기반해 cutoff에서 조기 종료한다.
  - It is no guarantee that the collection walked everything below `published_after`. The oldest quarter may be a truncated sample.
- 댓글은 주제 사전에 걸린 영상만 받는다. 전체 영상의 댓글 분모는 존재하지 않는다.
  - **The denominator of a comment-side ratio is always "the comments on videos a topic caught".** A comment denominator over all videos cannot be made, so reading a comment composition as "the share of consumer attention" is wrong.
- 댓글 published_at은 댓글 자체 시각이다. 분기 귀속은 video_id로 부모 영상에 붙인다.
  - So the quarter is the parent video's rather than the comment's (the quarterly document population bullet of §Formulas, `parent_item_id`).
- 댓글은 계속 쌓이므로 최근 분기는 구조적으로 과소 집계된다.
  - **A fall in the recent quarter may not be a trend.** When a YoY verdict touches the recent quarter, this limitation comes first.
- order=relevance는 유튜브 비공개 알고리즘이며 좋아요 순이 아니다.
  - The comment sample is neither by popularity nor random. A comment-based metric is not read as "the top reactions".

These limitations do not disappear with a recollection (#38) — because it walks the same way again. What
disappears is only the point in time, 2026-08-19, and that is why a snapshot is not overwritten but put
alongside as a version.

## Baselines the evaluation harness compares against (the rule implementation, measured 2026-08-23)
| task | evaluation set | rule baseline | adoption condition (a single threshold) |
|---|---|---|---|
| polarity (the suncare holdout) | sun holdout 100 | acc .77 · 불만 P .89 / R .70 | acc ≥ .77 and P:불만 ≥ .89 |
| polarity (across categories) | P1 blind40 (holdout) | acc .47 · 불만 P .67 | acc ≥ .47 and P:불만 ≥ .67 |
| wish_class | P9 blind60_v2 (holdout, labelled 2026-08-23) | a: P .94 / R .94 (holdout60, not blind) | P:a ≥ .90 on blind60_v2 |
| brand_link | P3 120 | precision 119/120 | P:OK ≥ .97 |
| product_match | P2 blind 40 (holdout, `match_check40_v2_blind`) | strict .77 / 변형허용 .95 (39 adopted pairs) | strict ≥ .769 on the adopted pairs |
- T10/T11: an adoption condition is **a single number**. A baseline written as a range cannot be compared by
  machine.
- **The numbers in this table are a rounded notation of the raw values the rules produced, so the comparison
  is made at the decimal places written.** The harness rounds a metric to the same decimal place as the
  threshold before comparing (`analysis.baselines.meets`, the same rounding as the `.3f` the harness prints
  scores with). The decimal places are the resolution of this gate: p1's `.67` has two places, so the
  `2/3 = .6667` the rules produced passes, and product_match's `.769` (= `30/39`, the source
  `slice-p2/README.md`'s `.77` restored to three places) has three and is that much tighter. Adding a
  decimal place while copying it over tightens the criterion and dropping one loosens it, so the decimal
  places are contract along with the number — `tests/test_baselines.py` compares the table letter for
  letter. Compared straight against the raw value, the very rules that made the baseline lose to
  `--check-baseline` (measured in #2); that property is held by the same file with the rules' raw values
  (`43/47`, `2/3` …) and a real run of the implementation.
- The names in an adoption condition are the metric keys the harness produces, as they stand — `acc` ·
  `P:<라벨>` · `R:<라벨>` · `strict` · `변형허용`. `tests/test_baselines.py` parses this table and compares
  it against the (name, number) pairs in `analysis/baselines.py`.
- **product_match's strict / 변형허용 are not accuracy but precision over the adopted set.** The
  implementation emits adopted (`Y`) or not adopted (`N`) per row, and the denominator is **the number of
  adopted pairs**: `strict = |adopted ∧ gold='Y'| / |adopted|`,
  `변형허용 = |adopted ∧ gold ∈ {'Y','V'}| / |adopted|`. Measured as accuracy over 40 rows, the same
  implementation gives a different number. The baselines .77/.95 are the 30/39 = .769 · 37/39 = .949 that
  came out of the 39 pairs the v2 rules adopted (`in_final=1` in `match_check40_v2_blind.csv`), and
  `tests/test_cli_eval.py` checks the reproduction on that adopted set.
- Replacing an implementation (rules→LLM, a dictionary version bump) comes in only through a PR that updates
  this table.

### Rule measurement (2026-08-24, #3's implementation) — the bar for replacing an implementation
| evaluation set | rule measurement |
|---|---|
| sun holdout 100 | acc .870 · P:불만 .915 |
| p1 blind40 | acc .475 · P:불만 .667 |
- The `adoption condition` of the table above is the **floor** the contract demands. Replacing an
  implementation (rules→LLM) has to **beat** the numbers here — even with `--check-baseline` green, losing
  on this table means `polarity_version` is not swapped (issue #6).
- `RULE_MEASURED` in `analysis/baselines.py` is a copy of this table and `tests/test_baselines.py` compares
  them.
- The comparison is on the same yardstick as the baseline table above — rounded to the decimal places
  written, then compared. `.915` is the raw `43/47 = .914893…` and `.667` is the three-place notation of
  `2/3`.

### LLM measurement (2026-08-24, a blind holdout — issue #6 §산출물 6)
| evaluation set | rule measurement | Sonnet 5 (llm-claude-sonnet-5-20260824) | Opus 5 (llm-claude-opus-5-20260824) | gemma4 (llm-ollama-gemma4:latest-fs2-20260824) |
|---|---|---|---|---|
| sun holdout 100 | acc .870 · P:불만 .915 / R .915 | acc .910 · P:불만 .979 / R .979 | acc .940 · P:불만 .979 / R 1.000 | acc .900 · P:불만 .978 / R .936 (만족 P .974 / R .864) |
| p1 blind40 | acc .475 · P:불만 .667 / R .455 | acc .950 · P:불만 .955 / R .955 | acc .950 · P:불만 .917 / R 1.000 | acc .850 · P:불만 1.000 / R .773 (만족 P 1.000 / R .933) |
- Prompt version `PROMPT_DATE=20260824` (no tuning edits, the same version as tune), the holdout run once
  per model (the blind kept).
- Cost: Sonnet $0.289 / Opus $0.398 (both on the Batches API).
- Adoption recommendation (the coordinator, 2026-08-24): both models cleared the contract floor and the rule
  measurement entirely — **Sonnet 5** is recommended (p1 P:불만 .955 against .917, 60% of the price). Running
  the `polarity_version` swap (a full pass) waits on #21's budget decision.
- **gemma4** (ollama, gemma4:latest 8B Q4_K_M, RTX 4060, think:false + few-shot fs2, the blind holdout run
  once at the end, cost $0 locally): it cleared both the contract floor and the rule measurement — sun acc
  .900 > the rules' .870, P .978 > .915 / p1 acc .850 > .475, P 1.000 > .667.
  Against thinking OFF with no few-shot (sun acc .850 · P .976 / p1 acc .850 · P 1.000), the few-shot raised
  sun acc by 5pt and kept the precision.
  This prompt version is **ollama-only**, so it has nothing to do with the Claude path's
  `PROMPT_DATE=20260824` — the Sonnet/Opus numbers above stand as they are.
- This table is **a record** — the baselines the harness compares against (the contract floor table and the
  rule measurement table above) are not changed by this table.

## Retrieval measurements (#28 step 4 — 2026-08-25, every source · 381,950 chunks · the queries are the topic aliases)
| mode | engine | queries | P@10 | MRR@10 | Hit@10 |
|---|---|---|---|---|---|
| literal | bm25 | 61 | .864 | .893 | 91.8% |
| literal | vector | 61 | .618 | .785 | 91.8% |
| literal | hybrid | 61 | .839 | .911 | 95.1% |
| heldout | bm25 | 60 | .000 | .000 | 0.0% |
| heldout | vector | 60 | .062 | .114 | 25.0% |
| heldout | hybrid | 60 | .025 | .029 | 8.3% |
- **The adoption condition is heldout's bm25 row: P@10 > .000.** The answer key in heldout is a document of
  the same topic that carries not one query token, so lexical search is structurally 0, and that 0 is the
  line the vector side has to clear (the two modes are defined in `analysis/retrieval/eval.py`). vector
  cleared it at .062 · Hit 25.0% and hybrid at .025 · 8.3% — **the grounds for adopting the vector side are
  this one row, and this table is where that number lives.**
- literal is not performance but **fault detection**. The answer key itself was made by string matching, so
  bm25 winning is normal (.864), and if this collapses the tokenisation is broken (the dictionary not
  applied, the normalisation out of step).
- The query count differs per mode because a topic with a single alias (`혼합자차`) drops out of heldout,
  and a query with an empty answer key is not scored.
- This table is **a record** — the baselines the harness compares against (the two tables above) are not
  changed by this table, and `--check-baseline` does not look at these numbers either.
  `tests/retrieval/test_contract.py` holds only the table's shape (the six mode×engine rows and the adoption
  condition). Being scores made with automatic labels (the topic dictionary), they are used to choose
  handles, while the human-made golden set is used once, in the final report.
- **These six rows are values on topic dictionary v1.** Fork #56 added seven aliases in v3 (`formats.md`
  §Topic lexicon v3), so the index tokens changed — `썬쿠션`·`썬스틱`·`선에센스`·`속건조`·`파데프리` come in
  as Kiwi user words and expansion-list entries — and the queries grow from 61/60 to **63/62**. So after v3
  is turned on this table is **a record of the v1 version until it is measured again**, and which delta the
  next measurement stands on is said by the "frozen v1 + #56 ledger" equation `tests/retrieval/test_topics.py`
  holds. The table's shape (the six mode×engine rows and the adoption condition) is unchanged.
- The topic dictionary that made these numbers is **the active version of `needs.aspect_lexicon`**
  (`ruleset='retrieval-topic'`, v1 — **that number tag cannot be confirmed from the DB**, the three lines
  below). Fork #8 moved the source there from a constant in `analysis/retrieval/topics.py`, and that the
  moved dictionary has **the same 15 topics, the same aliases and the same `match_topics` results** as the
  constant edition is held by `tests/retrieval/test_topics.py` against a frozen copy
  (`tests/retrieval/frozen_topics.py`) — if that equality breaks **without a declared delta**, this table
  quietly becomes a stale table. #56's delta is declared, and the row above records it.
- The raw values are the six `var/retrieval/score_{mode}_{engine}.csv` (`var/` does not go into the
  repository). The measurement was taken **before** the narrowing of the answer key's sources (fork #16) and
  the keyset paging of the answer key (fork #17 S4) — neither changes the row set an all-source run walks,
  so if the values move when measured again it is the corpus that grew, not these two changes.
- **The query stopwords (fork #46) do not move these six rows.** That holds by construction rather than by
  a rerun: the queries are all topic aliases, and not one of them overlaps that list when tokenised — **both
  v1's 73 aliases (61/60 queries) and v3's 80 (63/62 queries) overlap by 0** (measured 2026-08-27 ·
  `tests/retrieval/test_query_stopwords.py` bets on the v3 edition). With an overlap of 0, `tokenize_query`
  produces the same tokens as `tokenize`, so the three bm25 rows stand; vector does not tokenise the query
  (it encodes the original text); and heldout's answer-key exclusion (`eval.docs_with_tokens`) and the index
  are both on the `tokenize` axis and never look at this list in the first place. ydc measured the same
  thing with its own 61 aliases and got the same 0.
- **The vector store version these six rows stand on** (fork #49): `var/retrieval/vectors/e5base` —
  `model=intfloat/multilingual-e5-base · revision=d128750597153bb5987e10b1c3493a34e5a4502a · vectors=381950 ·
  chunked_at_max=키없음`. The last column means the store was baked before that key existed, so the
  comparison for this version goes as far as the count (`tests/retrieval/test_vectors.py`). The way to print
  the version again is `tool/show-vector-stamp <저장소>` — it reads the manifest alone, so it needs neither
  the 1.2GB matrix nor a GPU.
- **The two bm25 rows are not values on that version.** bm25 does not open the vector store — what literal
  `.864` and heldout `.000` stand on is the corpus of 381,950 chunks and the active topic dictionary, and
  being on a different axis from the vector version they must not be read side by side. Only the heldout
  vector `.062` that becomes the adoption criterion is a value on the store above.
- This version **was not written by the rows themselves** — the six raw-value CSVs of the time had no
  version column at all (that is the place fork #49 fixed), and what is recorded here was tied by the
  comparison that the manifest's `count` equals the chunk count in the table's header. From the next
  measurement on, the `store` column writes it per row and this comparison is not needed.
- **The topic dictionary version these six rows stand on** (fork #62). The repository had frozen that
  dictionary — the version string traced back through the loaded source that went into DB v1 at the time
  (`git show de2ee06:analysis/retrieval/dict/topics_v1.csv`) is `ruleset=retrieval-topic · version=1 ·
  topics=15 · aliases=73 · fingerprint=5a0cae76311e1408`. It is **the same shape** as what an evaluation row
  carries in the CSV `dictionary` column today (`entrypoints.md` §Search). This version too **was not
  written by the rows themselves** — the six raw-value CSVs of the time had no dictionary column. But
  **these five fields do not all carry the same weight**, so they are written apart, below.
- **What could be traced back — the dictionary's content and its fingerprint.** Holding the old loaded source against the v2 still in production with
  `tool/show-lexicon-stamp --csv <옛 CSV> --against 2` gives `fingerprint=5a0cae76311e1408` on both sides
  and **no difference** (measured read-only on the production DB, 2026-08-27). The repository's
  `tests/retrieval/frozen_topics.py` is a **proxy copy** from just before the evaluation was ported. That
  copy differs from the loaded source in one ordering, `유기자차.mfds_inci`, and so gives
  `fingerprint=4afd3b25522a4d26`, but neither the matching nor the queries read that column. That the two
  dictionaries part in that one ordering alone is asked back by machine in
  `tests/retrieval/test_topics.py`.

  The content carries on from there. ① the old loaded source = production v2 — difference 0 in the
  comparison above. ② the current loaded source = the active production v3 — **difference 0** down to the
  topics, the aliases and the ordering (`tool/show-lexicon-stamp --csv
  analysis/retrieval/dict/topics_v1.csv --against 3`; row by row, `cosmai lexicon diff --kind aspect
  --csv analysis/retrieval/dict/topics_v1.csv`). ③ production v3 = production v2 + fork #56's seven
  aliases, **and no other difference** (`tool/show-lexicon-stamp --version 3 --against 2`).
  So "what has grown in the dictionary turned on today over the dictionary of those six rows" is answered by
  three commands: `촉촉함_건조함` +`속건조` · `톤업_메이크업베이스` +`파데프리` · `선크림` +`썬쿠션`·
  `썬스틱`·`선에센스`·`선스프레이`·`sunscreen`. That delta grows the queries from 61/60 to 63/62.
- **What could not be traced back — the number tag.** `ruleset='retrieval-topic'` in `needs.aspect_lexicon`
  has **no v1 row**: what is left is only v2's 94 rows (off) and v3's 101 rows (on) (measured read-only on the
  production DB, 2026-08-27 · `tool/show-lexicon-stamp` · v2 `fingerprint=5a0cae76311e1408` · v3
  `ae48f7cfb70a60f7`). So **`version=1` is a record left by the contract of the time and by fork #8's green,
  not something the DB attests.** The content and the fingerprint were traced back through the v2 that
  remains, but the DB rows that actually carried number 1 on that content and were activated are gone.
- From the next measurement on, the `dictionary` column writes it per row and this tracing back is not
  needed. **Measuring this table again is not something this issue did** — the six rows stay a record of the
  v1 version, and a remeasurement stands on raw values that carry the `dictionary` and `store` columns
  together.

## Vector floor (not added — the distributions do not part, fork #48)
`search` in `analysis/retrieval/vectors.py` has no similarity floor — it sorts by cosine and emits the top
k as they stand. That does not mean one should be put in on "it would be nice to have": **e5 cosines are
bunched into a narrow band.** They are query-to-document rather than document-to-document, so nearly all of
them fall in one stretch, and if the distributions do not really part, a floor works **only in the direction
of letting irrelevant results through while cutting correct ones**. So it is measured before being put in,
and **the criteria for the verdict are fixed before the measurement.** The verdict measured on our corpus is
**못 쓴다**, and the two tables below are the grounds.

| verdict | condition | on our corpus |
|---|---|---|
| 분리가 된다 | the highest cosine of the fake queries < the lowest cosine of the real queries | **no** — the fake maximum .8550 > the real minimum .8071 |
| 쓸 수 있다 | even overlapping, the threshold that keeps 90% or more of the true positives cuts at least half of the false positives | **no** — that threshold, .8226, cuts **0/12** of the false positives |
| 못 쓴다 | if neither of the two above, no floor is used | **this one** |

- **The criteria are not made after looking at the results.** That is why this table is in the contract, and
  the meaning of the three branches lives in the single function `verdict` of
  `tool/measure-vector-floor` alone, with `tests/retrieval/test_vector_floor.py` holding that meaning. The
  90% and the half are that tool's two constants `KEEP`·`CUT`.
- **The threshold for "쓸 수 있다" is the highest measured value among those that keep 90% of the true
  positives.** Choosing a number between two values would make the choosing rule one more handle, and
  choosing a lower threshold cuts fewer false positives, so this criterion is the most generous reading on
  the "쓸 수 있다" side — one notch above ydc's implementation
  (`sorted(real)[int(n*0.10)-1]`).
- The queries are **not chosen**: the real ones are the topic aliases `retrieval eval --mode literal` uses
  (the canonical form being the active version of `needs.aspect_lexicon`), and the fake ones are words that
  look like ingredient names absent from the corpus. That the fakes **really are absent is confirmed at
  every measurement** — if even one is in the corpus it is not fake, and the numbers on that sample cannot
  be trusted (the tool's exit code 1).
- The value measured is the **highest cosine** (top-1) per query. Using the mean of the top k would make k
  one more handle.

The measurement (2026-08-27 · the way to measure it is `tool/measure-vector-floor` · the highest cosine per
query · the store version is `model=intfloat/multilingual-e5-base ·
revision=d128750597153bb5987e10b1c3493a34e5a4502a · vectors=381950 · chunked_at_max=키없음`, active topic
dictionary v2):

**These quantiles are a record on topic dictionary v2** (fork #56) — v3 grows the real queries from 61 to
**63** (the new aliases `속건조`·`파데프리`). Until they are measured again the two rows below are values on
the v2 version, and what the verdict (the distributions do not part) becomes with those two can only be
known by measuring.

**Version record of this table vs. what the tool carries from now on (#68).** The two rows below were
measured on active lexicon **v2 · 61 queries**, and the tool of that day recorded the lexicon axis as a bare
number; that record stays as written. Since #68 `tool/measure-vector-floor` records the lexicon axis as the
full stamp (`ruleset · version · topics · aliases · fingerprint`), the same weight as the store axis, so the
next remeasurement replaces this line with the stamp it was measured on.


| distribution | n | min | 25% | median | 75% | max |
|---|---|---|---|---|---|---|
| real queries (topic aliases — the same queries as literal in §Retrieval measurements) | 61 | .8071 | .8359 | .8457 | .8705 | .9161 |
| fake queries (ingredient-like names absent from the corpus) | 12 | .8301 | .8393 | .8401 | .8472 | .8550 |

- **The fake distribution sits inside the real one.** "They overlap" alone makes grazing look the same as
  bunching, so the size of the overlap is written alongside: **10** of the 12 fakes are inside the reals'
  interquartile range (.8359~.8705), and **37** of the 61 reals are below the fake maximum (.8550). The
  difference between the two medians is **.0056**.
- **Putting ydc's provisional value .865 on our corpus stops 12/12 of the fakes and throws away
  45/61 = **73.8%** of the reals with them.** Looking only at "it stops every fake" it is a good value, and
  that is what a value fixed on 6 samples looks like. In the other direction, the threshold that keeps 90%
  of the true positives (.8226) stops **not one** fake — the two rows being the two ends of the same
  distribution, there is no seat for a floor.
- This table stands on the **same store and the same query list** as §Retrieval measurements. But **the
  numbers are not to be read side by side** — that side is P@10 and this side is cosine, so the axes differ.
  The dictionary is not the same version either: that table is recorded as a value on active v1 and this one
  is a value on v2, and since `needs.aspect_lexicon` no longer has a v1 row for
  `ruleset='retrieval-topic'`, the two versions cannot be held against each other **inside the DB**
  (2026-08-27 · what is active today is not even v2 but v3). Fork #62 took that seat over: §Retrieval
  measurements records what could be traced back (the dictionary's content) apart from what could not (the
  number tag and the fingerprint), and two ways of holding a CSV against a DB version now stand — row by
  row, `cosmai lexicon diff --kind aspect --csv <path>`, and at compiled-dictionary grain,
  `tool/show-lexicon-stamp --csv <path> --against <n>`. But **this table's sample (61) is not confirmed
  again by them**: the comparison of the time was "the 61 literal queries of active v2 are the same as the
  loaded source CSV at that point", and since then the loaded source has become the v3 content and the
  active version is v3 too, so the `csv_queries` field of `tool/measure-vector-floor` now holds **today's**
  two counterparts (63) against each other.
- **So no floor is put on `vectors.search`.** The day one is put in,
  `test_search_fills_top_k_however_far_the_query_is` in `tests/retrieval/test_vector_floor.py` goes red, and
  this section has to be fixed with it.

### Then what stops a query with no grounding — chunk frequency (`analysis/retrieval/grounding.py`)
The cosines do not part but **the frequencies do.** There is one rule: **when any query token of length 4
or more has a chunk frequency of 0, it is stopped.** It means the corpus has never once said that name, so
even a search result that came out would be a document unrelated to it. A stopped query gives 0 results plus
one stderr line, and the exit code is the `1` (no results) that already exists.

**The gate is put on `--engine vector`·`hybrid` alone** (issue #48 §범위 확장). `bm25` behaves **as it did
before this issue**: lexical search ignores a word of frequency 0 as idf 0 and **answers with the words that
are left**, so putting the gate on it would turn the partial answer that used to come out of a "a real topic
+ a new product name not yet in the corpus" query into 0 results. Nobody measured that loss (there is no
query log), and there is no reason to accept an unmeasured loss.
`tests/retrieval/test_grounding.py` holds that seat — down to bm25 really answering such a query.

**It is called "df" but the unit counted is the chunk.** `Index` stands at chunk grain, so
`len(postings[term])` is a chunk count rather than a document count. Only whether it is 0 is looked at, so
the table below does not wobble, but it is a different word from what `eval.docs_with_tokens` does (folding
to documents).

The measurement (2026-08-27 · the way to measure it is `tool/measure-vector-floor --part df` · 381,950
chunks · **active topic dictionary v2** — v3 (fork #56) grows the real aliases from 61 to 63, so this table
is one to be measured again then · the count of stopped queries). **The suite cannot measure this table
again** — `--part df` uses neither the encoder nor a GPU but it does demand **a shared DB and a standing
BM25 index**, and with no cache it is the ten-odd minutes of morphologically analysing 380,000 chunks. So
this is where those numbers live, and what `tests/retrieval/test_grounding.py` holds is the rule and the
table's sentences (the same seat as §Retrieval measurements):

| rule | 61 real aliases | 610 real sentences | 12 fake names | 120 fake sentences |
|---|---|---|---|---|
| **every** token's frequency is 0 (ydc's first edition) | 2 | 0 | 10 | 0 |
| **any** is 0 · length ≥ 3 | 2 | 20 | 11 | 110 |
| **any** is 0 · length ≥ 4 (`ZERO_DF_MINLEN`) | **0** | **0** | **11** | **110** |

- The sample is made by rules too (the same method as §Query routing): the real aliases are those 61 of the
  table above, the sentences are the aliases × the 10 fixed sentence patterns `#47`'s tool uses, and the
  fakes are those 12 of the table above and their 120 sentences.
- **Length 4 was fixed at a gain of 0 and a loss of 2.** Lowered to 3, the two aliases `재도포` and
  `ZnO` (→ `zno`) are stopped and the 20 sentences holding those aliases are stopped with them, while
  **the fake blocking stays at 11/110.**
- **The "every 0" branch is not kept.** On our corpus that branch stops **0** more fakes (11 ≥ 10 —
  `젤라토프로틴추출물` is not every-0 because of `추출물` at 1,341) and stops 2 real aliases. ydc kept that
  branch and held `재도포` being stopped to be right, but for us it is the same gain of 0 and loss of 2.
- **All three ydc sources were confirmed in `v0.3.0:vector_threshold.py`** (2026-08-27, read only): the list
  of 12 fakes is **letter for letter the same** as that file's `FAKE` (down to the order), the threshold
  formula we did not use is `sorted(real)[max(0, int(len(real) * 0.10) - 1)]` (:74), and `ZERO_DF_MINLEN = 4`
  has the same name and value (:33). But **"every 0" is not a branch dropped in that version** — `v0.3.0`'s
  `df_gate` uses both branches together (:149 and :164), and it is we who took the first branch out. It
  cannot be confirmed inside this repository — the pinned copy (`analysis/slices/ydc/`, `v0.1.0`) did not
  hold this file either, and #9 deleted that copy.
- **A query with 0 tokens is not judged by frequency — it is let through.** `땀에`·`톤 업` and a Cyrillic
  notation are that branch, and stopping them would stop **the one place where the vector side is the only
  one that answers**: in the raw values of the run that made §Retrieval measurements, `톤 업` is vector P@10
  1.000 while bm25 returns 0 results, and `땀에` is vector .200 / bm25 0.
- **The one place it cannot stop is a Cyrillic notation** (`трансдермалин` — 1 of the 12 fake names, 10 of
  the 120 fake sentences). `bm25.tokenize` produces no token at all from characters that are neither Hangul
  nor Latin, so it falls into the branch just above. The place to fix that is the tokenisation rather than
  this gate, and this gate imitating it would make two axes.
- **The frequency is read on the index axis** (`bm25.tokenize` · `Index.postings`) — it does not ride the
  query stopword list (fork #46). `eval.docs_with_tokens` is on the same axis for the same reason. The
  active stopword list at the time this table was measured was empty (`version=None`, 2026-08-27), so the
  two tokenisations produced the same tokens and the table reads the same on either axis.
- **The gate is on `pipeline.search` alone.** `retrieval eval` does not ride it, so the six lines of
  §Retrieval measurements do not move under this gate — and that they would not move even if it did ride it
  is the **0** in the first column of the third row of the table above.

## Per-source allocation (not added — the global top k does not follow the composition, fork #54)
`ranked_chunks(..., sources=...)` **narrows** the candidates by `sources` and no more, emitting the global
top k of what is left — there is no per-source allocation. ydc `rag/engine.py` draws separately per source
and merges, and the reason is that **92% of its index is short YouTube comments**, so the global top k
pushed `mfds` down to **293위** and `ingredient` **outside 300**. Whether the same reason exists for us is
measured, and then decided — putting RRF in before measuring is what this section stops.

**The four verdict criteria were fixed before the measurement.
The criteria are not made after looking at the results.** The constants are only `K` (10) and
`BURIED_RANK` (rank 100 — the same order of magnitude as ydc's 293 and ten times k), and they live in the
single function `verdict` of `tool/measure-source-mix` (`tests/retrieval/test_source_mix.py` holds that
meaning).

| verdict | meaning |
|---|---|
| 쏠리지 않는다 | the dominant source's share of the global top k < that source's index composition — ydc's condition itself is absent |
| 밀리지 않는다 | it is skewed, but the **median** first-appearance rank of the minority sources that have a candidate is < 100 |
| 지배한다 | it is skewed and that median is ≥ 100 — only here is there a seat for an allocation |
| 측정 불가 | not one minority source has a candidate |

**The median carries the verdict.** Judging by one query's worst value (777 in our measurement) would give
domination on any corpus — the tail is always long.

The measurement (2026-08-27 · production DB, read-only · **381,950** chunks · topic dictionary v3 · the
queries are the **59** of the 63 topic aliases `retrieval eval --mode literal` uses that have a candidate
(`재도포`·`땀에`·`톤 업`·`ZnO` are df 0) · the engine is bm25. The way to measure it is
`tool/measure-source-mix`):

| source | index composition (chunks) | index composition (documents) | share of the global top 10 |
|---|---|---|---|
| `youtube_comment` | 288,914 · **75.64%** | 285,735 · 89.34% | 416 · **71.11%** |
| `youtube_transcript` | 63,972 · 16.75% | 5,303 · 1.66% | 32 · 5.47% |
| `commerce_review` | 23,156 · **6.06%** | 22,889 · 7.16% | 123 · **21.03%** |
| `youtube_video` | 5,908 · 1.55% | 5,908 · 1.85% | 14 · 2.39% |

- The verdict is **쏠리지 않는다** — the dominant source is 75.64% of the index while the top 10 takes only
  71.11% of it (a per-query mean of 70.51%). It is already decided at the first gate, so the second gate
  does not carry the verdict, but it is recorded alongside: over the **157 (query, source) pairs** of
  minority sources that have a candidate, **the median first-appearance rank is 19** (p25 5 · p75 64 · max
  777), an order of magnitude away from 100. The queries whose top 10 is the dominant source alone are
  **11** of the 59, and the remaining 48 already have a minority source in them.
- **That it stands the other way round is this section's answer.** `commerce_review`, 6.06% of the index,
  takes **21.03%** of the top 10 — over-represented 3.5×. That is because a short review holds the query
  words densely and BM25's length normalisation (`bm25.B` 0.75) lifts that side, so ydc's picture, "a
  minority source with many long documents is pushed down", does not hold on our corpus.
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
- **When it has to be measured again**: this verdict stands on the composition above. If one source grows
  and the dominant source's share of the top 10 passes its index composition, the first gate flips, so
  `tool/measure-source-mix` is run again whenever the corpus grows greatly or a source is added. The ability
  to **narrow** by `sources` is unchanged — what this section says is absent is the allocation, not the
  narrowing.

## Query routing (no router is attached — there is no canonical decision on an ingredient name, fork #47)
ydc `v0.3.0`'s `rag/router.py` chooses the engine per query by rules (it uses no LLM — it is deterministic
and can always say why it went that way). **It is not promoted.** Two of the **four signals** the router
looks at have no source (the ingredient name and the registration number), **two branches are blocked** with
them (the ingredient-name side of `bm25` · `multi_source`), and using the one list that exists today in the
ingredient-name seat walks straight into the trap ydc walked into. Measured for real, a router that looks at
the remaining two signals sends 0 queries correctly (the sixth row of §Misrouting measurement below).

### The canonical decision on an ingredient name is not the tokeniser dictionary
`analysis/retrieval/dict/ingredient_dictionary.tsv` (1,877 surface forms, `bm25.DICTIONARIES`) is a **Kiwi
tokeniser dictionary**. Discourse words are in it on purpose — indexed without the dictionary, `백탁` splits
into `백`+`탁` (`bm25.kiwi`), and stopping that is what this file does. Read as "a list of ingredient names",
**a natural-language query holding such a word is judged an exact query**. This sentence is held by
`tests/retrieval/test_query_routing.py`.

The repository has three ingredient-name candidates and **all three hold discourse words** — none of them
can be the canonical form of this decision.

| candidate | size | why it is not canonical |
|---|---|---|
| `dict/ingredient_dictionary.tsv` | 1,877 surface forms | a tokeniser dictionary. **11** `ko` aliases of the active topic dictionary (`선크림`·`백탁`·`톤업`·`무기자차`…) are in it |
| `needs.entity_lexicon` `kind='ingredient'` v1 | 43 rows / 28 keys / 42 distinct surface forms (the original is `eval/lexicon/ingredient_kr_colloquial_v1.csv`) | `source='paper_lexicon'` — it is a paper search-term vocabulary. The `category` of the original CSV's 32 keys is split into **10 kinds** (uv_filter 5 · mechanism 5 · spec 4 · enzyme 2 · peptide 2 · formulation 2 · regulatory 2 · claim 1 · anatomy 1 · ingredient 8), so `category='ingredient'` is only **8 of the 32 keys**, and it holds `무기자차` as a surface form of **both** ZINC_OXIDE and TITANIUM_DIOXIDE |
| `term_kind='mfds_inci'` in `needs.aspect_lexicon` `ruleset='retrieval-topic'` | 24 surface forms | an ingredient-notation axis, but only the UV filters of the two topics 무기자차 and 유기자차; `자외선차단제` (a product category) is mixed in, and 3 surface forms (`아보벤존`·`옥토크릴렌`·`자외선차단제`) overlap the `ko` axis |

ydc's canonical form is the 1,851 kinds left after taking the topic aliases out of the `ingredient` column
of its ingredient table (`data/external/product_ingredient_function_repaired.csv`, 31,246 rows / 577
products) and tidying the markers. **That ingredient table does not come to us** — upstream
`slopindustries/cosmai#73` decided on 2026-08-26 that "full ingredient lists are walked by a collector,
external CSVs are not taken in". So this axis does not open until that collection stands (the seat fork #10
recorded as held).

### The four branches and their sources (what each is blocked by)
| branch | signal | our source | state |
|---|---|---|---|
| `bm25` | the ingredient name | none (the table above) | **blocked** — upstream `slopindustries/cosmai#73` |
| `bm25` | the brand | `needs.entity_lexicon` `kind='brand'` 968 rows / 950 distinct surface forms (the original `eval/lexicon/brand_lexicon_v1.csv` has 859 rows) | **the list exists but it misfires** — **144 of the 950 surface forms are two characters or shorter**, so under ydc's substring `_find` they catch on ordinary Korean (`밀려` ← the brand `려`) |
| `bm25` | the registration number (a 10-digit report number) | none | **blocked** — fork #55 |
| `bm25` | an SPF/PA notation | one regular expression | open |
| `temporal_filter` | a time expression + `report_date` | **there is no registration-time axis** (fork #55 — `mfds_items.csv` has the four fields `COSMETIC_REPORT_SEQ`·`ITEM_NAME`·`ENTP_NAME`·`report_date`, so it opens the time and the registration number alone and **does not open the ingredient axis**). `corpus_document.published_at` (023 DDL, NOT NULL) **is there** | **blocked** — but not for want of a source: **it is a question on a different axis**. ydc's `report_date` is *when the product was registered* and our column is *when it was said*, so "the newest product" and "the most recent utterance" are not the same query. The time-expression dictionary is a constant and needs no source |
| `multi_source` | an exact signal + a natural-language signal | it parts only once the ingredient-name axis stands | **blocked** (below) |
| `vector` | everything else | — | open |

**A router cannot stand on the two open signals alone — not because it is incomplete but because it is
harmful.** Since one can argue that with `vector` as the default a partial router makes nothing worse, that
seat is held not by rhetoric but by measurement. Put our **only measured query workload** (the 73 topic
aliases the literal mode of §Retrieval measurements measures) through a router that looks only at the 950
brand surface forms + the SPF/PA regular expression, and the queries that go to `bm25` are **2, of which 0
were sent correctly** — `밀려` catches on the brand `려` and `화이트닝` on the brand `화이트`.
**A gain of 0 and a loss of 2, so it is worse than the default.**

### There are places that do not split in two for us either (`multi_source`)
ydc's `콜라겐 들어간 제품 뭐가 좋아` — `콜라겐` is a real ingredient and a discourse word at once (the
discourse/product ratio is 281 for 콜라겐 and 7 for 판테놀). Our own seat is pointed at by the active topic
dictionary as it stands: there are **3** surface forms that are on **both** the ingredient-notation axis
(`mfds_inci`) and the axis of the words people use (`ko`) — **3 of them**, `아보벤존`·`옥토크릴렌`·
`자외선차단제`. It means they are real ingredients and people ask with those words, and that is the same seat
as `콜라겐`. (The `mfds_inci` 15 in the table's fifth row is **a different axis** — that one is the overlap
with the tokeniser dictionary, and those 15 are pure INCI chemical names such as `에칠헥실트리아존`, so they
are not discourse words but their exact opposite. That number must not be used in this paragraph.) When an
exact signal and a
natural-language signal are there together, **do not choose, emit both** — the day a router is attached this
rule comes with it. Choosing one always loses the other side, and that is the same logic as not summing the
sources.

### Misrouting measurement (2026-08-27 · our own dictionary · the way to measure it is `tool/measure-query-routing`)
Our tokeniser dictionary is put into the ingredient-name slot of ydc's first-edition binary rule (an exact
signal means `bm25`, none means `vector`), and the natural-language judgment is made as ydc makes it, by
Kiwi's predicate tags (`VA`·`VV`·`EF`·`EC`·`VCP`·`VCN`). `tests/retrieval/test_query_routing.py` holds the
numbers below against the tool's output every time.

| what is measured | value |
|---|---|
| a sample of 10 natural-language queries — the first 10 topics in the topic dictionary's order × each topic's first `ko` alias × 10 fixed sentence patterns | 오라우팅 **4/10** |
| the same rule over all 15 topics | **7/15** |
| ydc's three published queries, on our dictionary | **3/3** |
| of the active topic dictionary's 73 `ko` aliases, those in the tokeniser dictionary | **11** |
| of the same dictionary's 24 `mfds_inci` surface forms, those in the tokeniser dictionary | **15** |
| surface forms that are an ingredient notation (`mfds_inci`) and are also what people ask with (`ko`) — the `콜라겐` seat | **3** |
| queries the router that looks only at the two open signals (950 brand surface forms + SPF/PA) sends to `bm25` out of the 73 topic aliases | **2**, of which correct **0** |

- **ydc's 7/10 did not reproduce (4/10). The trap reproduced exactly.** Those are two different statements:
  the ratio is fixed by the topic composition of the sample, not by the rule. The first 10 topics of our
  sample do not include the `선크림` topic (it is 15th in the dictionary) — put it in and the number rises,
  take it out and it falls.
  **So this section's answer is not the first row but the fourth row and the last row**: that 11 discourse
  words stand as ingredient signals is a property of the dictionary independent of
  the sample (**every** natural-language query holding those 11 goes to `bm25`), and a router without the
  ingredient-name axis is a gain of 0 and a loss of 2.
- The 10 sentence patterns emit no ingredient signal at all (measured · the same test). The surface form
  caught is always the one alias of that topic, so the tool shows every time that the ratio did not come
  from the sentence patterns.
- **Even with the sample made by rules, the arbitrariness of the rule choice remains.** Change the rule for
  choosing the alias and the misrouting over the first 10 topics moves **between 0 and 6** (measured · the
  same tool's `alias_rules`: first `ko` 4 · last `ko` 0 · any `ko` 6 · first `mfds_inci` 1). The 4 of the
  first row is one point inside that range, and that is why the first row is not this section's answer.
- ydc's three published queries are letter for letter the same as that side's document, down to the surface
  forms caught — `선크림 루틴 알려줘` → `['선크림','루틴']`, `백탁 관련해서 소비자들이` → `['백탁']`,
  `끈적이지 않는 선크림 추천` → `['선크림']`. **Down to `루틴`**: our dictionary is the same file, out of the
  same ingredient table as ydc's.
- This table is **not on the same footing as §Retrieval measurements**. The axes differ — there the 61/60
  queries are all topic aliases and the score is P@10, while here there are 10 natural-language sentences and
  the value is a branch assignment. They must not be put side by side.

### What is handed to fork #11
#11 (the default search engine) is an issue about **choosing one default**, and a router is a proposal that
answers that question per query by rules. Only one thing is handed over: **a router cannot replace #11** —
two of the four signals are blocked so two branches do not stand, and stood up on the two remaining signals
it measures as a gain of 0 and a loss of 2, which is worse than the default. That state does not change
before the ingredient table arrives. So #11 has to choose a default.
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
the model refuse anyway (row 1 of the #74 table on #69); `search` itself keeps #48's rule and leaves bm25 ungated. It precedes the dry run as well: `ask --dry-run` on such a name previews the refusal — an empty evidence block, the gate's note on stderr, no row — while `retrieval search` keeps the ungated view (#48); fork #85 pins the order. The decision that this layer
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
| pass | what completes a unit | the check |
|---|---|---|
| first | the contract signature is implemented + `cosmai eval <task>` **produces a score** on that unit's evaluation set (falling short of the baseline is allowed) + `analyze <stage>` is idempotent | the eval output row is recorded in `needs.analysis_run.note`, the score in an issue comment |
| second | at or above the baseline of the table above | the same evaluation set, a blind holdout |
The baseline table is contract and the passes are an order. If the first pass's score already clears the
baseline, the second is skipped.
