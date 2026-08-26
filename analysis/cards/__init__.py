"""R&D 기회 카드를 규칙으로 만든다 — `contracts/interfaces.md` §기회 카드 가 정본이다 (포크 #6).

규칙의 출처는 ydc `analysis/slices/ydc/cards.py` 이고, 슬라이스를 import 하지 않고 옮겨 적었다. 설계
원칙 넷을 그대로 받는다: ① 유형은 규칙이 배정한다 -- LLM 이 "이건 제품 공백이야"라고 판단하지 않는다.
② 모든 수치는 이미 저장된 표에서 그대로 온다. ③ 근거 원문이 없으면 카드로 만들지 않는다. ④ 한계를
카드 안에 넣는다.

이 모듈도 DB 를 모른다. 카드는 행을 만들지 않으므로(계약 §기회 카드) 여기서 나온 것은 저장되지 않고
`analysis/cards/pipeline.py` 가 stdout 으로 낸다 -- 같은 수가 두 곳에 살면 그 순간 정본을 다툰다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from analysis.judge import DIFFUSING, SPIKE, STICKY, SURGE, UNJUDGED
from analysis.types import TopicQuarterJudgementRow

# 보고서의 손잡이이지 적합된 값이 아니다 -- 근거가 팀 합의뿐이라는 것을 계약 §기회 카드 가 적는다.
GAP_PRODUCT_GAP = 2.0  # 제품 공백으로 볼 최소 갭(%p)
SATURATED_COMPOSITION = 15.0  # 포화로 볼 최소 댓글 구성비(%)

EXPRESSION_GAP = "표현 공백"
PRODUCT_GAP = "제품 공백 기회"
VERIFIED_GROWTH = "검증된 성장"
FAD_RISK = "단기 유행 위험"
SATURATED = "포화 시장"
UPSTREAM_RESEARCH = "선행 연구 기회"
# 규칙이 걸리는 순서다. 위에서 먼저 걸리면 끝난다 -- 순서 자체가 정의인 것은 §판정 과 같다.
CARD_TYPES = (EXPRESSION_GAP, PRODUCT_GAP, VERIFIED_GROWTH, FAD_RISK, SATURATED, UPSTREAM_RESEARCH)
# 어휘에서 지우지 않는 것은 그 입력이 오는 날 규칙이 그대로 서기 때문이다. 없는 입력을 0 으로 깔면
# 그 유형이 조용히 영원히 안 나온다 (`W_EVIDENCE` 의 넷째 항을 0 으로 깔지 않은 것과 같은 문장).
UNAVAILABLE: Mapping[str, str] = {
    EXPRESSION_GAP: "제품 전성분 축(ydc ingredient_axis.py)의 원천이 이 레포에 없다",
    UPSTREAM_RESEARCH: "논문 계열 데이터가 아직 없다 -- ydc 에서도 미도착으로 보류다",
}
IMPLEMENTED = tuple(kind for kind in CARD_TYPES if kind not in UNAVAILABLE)

RISING = (SURGE, SPIKE)
STEADY = (STICKY, DIFFUSING)

# 카드가 자기 근거를 의심하라고 다는 주석이다. 색인·추출 축의 불용어 목록이 아니고(포크 #37 이 처분한
# 것은 그 축이다), 제 자리는 주제 사전의 extra 다 -- 옮기는 것은 사전 판본을 올리는 일이다 (계약 §기회 카드).
GENERIC_ALIAS: Mapping[str, frozenset[str]] = {
    "발림성": frozenset({"제형", "텍스처"}),
    "성분_신제품": frozenset({"성분"}),
    "촉촉함_건조함": frozenset({"수분"}),
    "지속력_워터프루프": frozenset({"지속"}),
}
UNDERCOUNTED = "최근 분기는 댓글이 계속 쌓이므로 구조적으로 과소 집계된다"
SINGLE_SOURCE = "단일 소스 판정 -- 플랫폼 간 교차 확인 없음"


@dataclass(frozen=True)
class Quote:
    """카드에 실리는 발화 하나. 뷰 `topic_quarter_evidence_quote` 한 행이 그대로 이것이다."""

    rank: int
    like_count: int
    matched_term: str | None
    text: str
    parent_video_url: str | None


@dataclass(frozen=True)
class CellFacts:
    """한 (주제, 분기) 가 든 것. 판정 두 행(댓글·영상)과 그 셀의 지표 몇 칸이다."""

    topic_key: str
    quarter: str
    comment: TopicQuarterJudgementRow | None
    video: TopicQuarterJudgementRow | None
    comment_composition: float | None = None
    video_composition: float | None = None
    velocity_yoy: float | None = None
    mentions: int | None = None


@dataclass(frozen=True)
class Card:
    topic_key: str
    quarter: str
    card_type: str
    type_basis: str
    strength: float
    quotes: tuple[Quote, ...]
    limits: tuple[str, ...]
    opportunity_score: float | None = None
    comment_type: str = ""
    video_type: str = ""
    comment_composition_pct: float = 0.0
    video_composition_pct: float = 0.0
    gap_pp: float | None = None
    velocity_yoy: float | None = None
    evidence_strength: float | None = None
    mentions: int | None = None


def _type_of(row: TopicQuarterJudgementRow | None) -> str:
    return row.trend_type if row is not None else ""


def _gap(facts: CellFacts) -> float:
    """갭이 없는 셀은 0 으로 읽는다 -- 한쪽 source 에 행이 없다는 뜻이고, 그때 갭 규칙은 서지 않는다."""
    for row in (facts.comment, facts.video):
        if row is not None and row.gap_pp is not None:
            return float(row.gap_pp)
    return 0.0


def _percent(value: float | None) -> float:
    return round(100 * float(value or 0.0), 2)


def classify(facts: CellFacts) -> tuple[str, str] | None:
    """(카드 유형, 배정 근거). 어느 규칙에도 안 걸리면 None -- 카드로 만들지 않는다."""
    gap = _gap(facts)
    comment_type, video_type = _type_of(facts.comment), _type_of(facts.video)
    composition = _percent(facts.comment_composition)

    if gap >= GAP_PRODUCT_GAP and comment_type not in UNJUDGED and comment_type:
        return (
            PRODUCT_GAP,
            f"갭 +{gap:.2f}%p -- 댓글이 영상 설명보다 훨씬 많이 말한다 (댓글 판정 {comment_type})",
        )
    if comment_type in RISING or video_type in RISING:
        if abs(gap) < GAP_PRODUCT_GAP:
            return (
                VERIFIED_GROWTH,
                f"댓글 {comment_type or '—'} / 영상 {video_type or '—'}, 갭 {gap:+.2f}%p 로 작다",
            )
        return (FAD_RISK, f"단기 피크 관측 (댓글 {comment_type or '—'} / 영상 {video_type or '—'})")
    if comment_type in STEADY and video_type in STEADY and composition >= SATURATED_COMPOSITION:
        return (SATURATED, f"구성비 {composition:.2f}% 로 상위인데 양쪽 다 {comment_type}·{video_type}")
    return None


def strength(kind: str, facts: CellFacts) -> float:
    """유형을 정한 근거가 곧 그 카드의 세기다. 전부 `opportunity_score` 로 줄을 세우면 점수가 NULL 인
    셀의 카드가 언제나 밀리는데, 그 카드는 점수가 아니라 갭으로 서는 것이다."""
    if kind == PRODUCT_GAP:
        return round(abs(_gap(facts)), 2)
    for row in (facts.comment, facts.video):
        if row is not None and row.opportunity_score is not None:
            return round(float(row.opportunity_score), 2)
    return 0.0


def limits(facts: CellFacts, quotes: Sequence[Quote]) -> list[str]:
    """한계를 카드 안에 넣는다 (설계 원칙 4). 표본 부족·단일 소스·과소 집계를 숨기지 않는다."""
    made: list[str] = []
    for label, row in (("댓글", facts.comment), ("영상 설명", facts.video)):
        if row is None:
            continue
        if row.hold_reason:
            made.append(f"{label}: 판정 보류 -- {row.hold_reason}")
        if row.single_source:
            made.append(f"{label}: {SINGLE_SOURCE}")
    used = {q.matched_term for q in quotes if q.matched_term}
    generic = used & GENERIC_ALIAS.get(facts.topic_key, frozenset())
    if generic:
        made.append(
            f"근거가 일반어 별칭({', '.join(sorted(generic))})으로 걸렸다. 이 주제의 구체 표현이 담긴 "
            f"댓글이 부족하다는 뜻이므로 근거를 사람이 확인해야 한다"
        )
    made.append(UNDERCOUNTED)
    return made


def alias_rank(entries: Iterable[Mapping[str, object]]) -> dict[str, dict[str, int]]:
    """주제 -> {별칭: 순서}. 사전은 구체적인 것부터 적혀 있고 그 순서가 곧 구체성이다 (포크 #8)."""
    made: dict[str, dict[str, int]] = {}
    for entry in entries:
        terms = [*(entry.get("ko") or []), *(entry.get("latin") or [])]  # type: ignore[misc]
        made[str(entry["topic"])] = {str(term): i for i, term in enumerate(terms)}
    return made


def quote_order(topic_key: str, quote: Quote, ranks: Mapping[str, Mapping[str, int]]) -> tuple[int, int]:
    """구체적인 별칭이 먼저, 그다음 좋아요. 상한이 3 이라 이 정렬은 고르지 않고 줄만 다시 세운다 --
    일반어로 걸린 근거를 실제로 밀어내려면 `TOP_PER_CELL` 이 커져야 한다 (계약 §기회 카드)."""
    place = ranks.get(topic_key, {}).get(quote.matched_term or "", 99)
    return (place, -quote.like_count)


def build(
    cells: Iterable[CellFacts],
    *,
    quotes: Mapping[tuple[str, str], Sequence[Quote]],
    alias_rank: Mapping[str, Mapping[str, int]],
    top: int = 3,
) -> list[Card]:
    """규칙에 걸린 셀마다 카드 하나, 그다음 유형마다 가장 센 것 하나만 남긴다."""
    made: list[Card] = []
    for facts in cells:
        got = classify(facts)
        if not got:
            continue
        kind, basis = got
        picked = sorted(
            quotes.get((facts.topic_key, facts.quarter), ()),
            key=lambda quote: quote_order(facts.topic_key, quote, alias_rank),
        )[:top]
        if not picked:
            continue  # 근거 원문이 없으면 카드로 만들지 않는다 (설계 원칙 3)
        score = next(
            (
                float(row.opportunity_score)
                for row in (facts.comment, facts.video)
                if row is not None and row.opportunity_score is not None
            ),
            None,
        )
        made.append(
            Card(
                topic_key=facts.topic_key,
                quarter=facts.quarter,
                card_type=kind,
                type_basis=basis,
                strength=strength(kind, facts),
                quotes=tuple(picked),
                limits=tuple(limits(facts, picked)),
                opportunity_score=score,
                comment_type=_type_of(facts.comment),
                video_type=_type_of(facts.video),
                comment_composition_pct=_percent(facts.comment_composition),
                video_composition_pct=_percent(facts.video_composition),
                gap_pp=_gap(facts),
                velocity_yoy=facts.velocity_yoy,
                evidence_strength=facts.comment.evidence_strength if facts.comment else None,
                mentions=facts.mentions,
            )
        )

    # 유형이 겹치지 않게 고른다. 같은 유형 카드 셋은 데모에서 한 장과 같다.
    picked_cards: list[Card] = []
    seen: set[str] = set()
    for card in sorted(made, key=lambda card: (-card.strength, card.topic_key)):
        if card.card_type in seen:
            continue
        seen.add(card.card_type)
        picked_cards.append(card)
    return picked_cards


def render(made: Sequence[Card], quarter: str) -> str:
    """마크다운 한 장. 요약 문장 자리에 근거 원문을 그대로 싣는 것이 LLM 을 쓰지 않는다는 말이다."""
    out = [
        f"# R&D Opportunity Card — {quarter}",
        "",
        "유형은 규칙이 배정했고 모든 수치는 저장된 표에서 왔다. 요약 문장은 LLM 을 쓰지 않고 "
        "근거 원문을 그대로 실었다.",
        "",
    ]
    for i, card in enumerate(made, 1):
        mentions = card.mentions if card.mentions is not None else "—"
        out += [
            f"## {i}. {card.topic_key} — {card.card_type}",
            "",
            f"**유형 배정 근거** {card.type_basis}",
            "",
            "| | |",
            "|---|---|",
            f"| 기회 점수 | {card.opportunity_score if card.opportunity_score is not None else '—'} |",
            f"| 판정 | 댓글 {card.comment_type or '—'} / 영상 {card.video_type or '—'} |",
            f"| 구성비 | 댓글 {card.comment_composition_pct}% · 영상 {card.video_composition_pct}% |",
            f"| 갭(댓글−영상) | {card.gap_pp:+.2f}%p |",
            f"| velocity(YoY) | {card.velocity_yoy if card.velocity_yoy is not None else '—'} |",
            f"| 근거 강도 / 언급 | {card.evidence_strength or '—'} / {mentions} |",
            "",
            "**소비자 발화 (좋아요 상위)**",
            "",
        ]
        for quote in card.quotes:
            body = " ".join((quote.text or "").split())[:220]
            out += [
                f"> {body}",
                "",
                f"  좋아요 {quote.like_count} · 걸린 표현 `{quote.matched_term or '—'}` · "
                f"[부모 영상]({quote.parent_video_url or '—'})",
                "",
            ]
        out += ["**한계**", "", *(f"- {line}" for line in card.limits), ""]
        out += ["**검토** accept / watch / reject — 사유와 다음 작업을 여기에 적는다.", "", "---", ""]
    return "\n".join(out)
