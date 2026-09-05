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
        # The return order leans on the loader's promise that patterns already arrive ordered by
        # (priority, id).
        specific = tuple(p for p in self.patterns if p.scope == "category" and p.category == category)
        # A twin (a neutral noun) has the same name and has to survive separately, so is_neutral_noun goes
        # into the key as well.
        hidden = {(p.aspect, p.is_neutral_noun) for p in specific}
        generic = tuple(
            p for p in self.patterns if p.scope == "generic" and (p.aspect, p.is_neutral_noun) not in hidden
        )
        return specific + generic

    def complaint_marker_re(self, category: str | None) -> re.Pattern[str]:
        """Discourse markers | every pattern of that category."""
        # The union differs per category, so it cannot be built at load time -- the caller caches it per
        # category.
        parts = [self.discourse_marker_re.pattern]
        parts += [p.pattern.pattern for p in self.for_category(category)]
        return re.compile("|".join(parts))


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
    gap_pp: float | None = None  # 댓글 구성비 - 영상 구성비 (%p). (주제, 분기) 사실이라 두 행이 같은 값
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
