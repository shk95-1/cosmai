"""저장된 세 표 → 기회 카드 (포크 #6). **아무것도 쓰지 않는다.**

카드가 표를 만들지 않는 이유는 계약 §기회 카드 가 든다 -- 모든 수치가 이미 저장돼 있고(ydc 설계 원칙 2)
한 벌 더 두면 그 순간 정본을 다툰다. 파일로도 떨구지 않는다(`retrieval terms` 와 같은 규약): 자라는
코퍼스의 스냅숏이라 레포에 두면 낡고, 남기려면 리다이렉트한다.

근거를 읽는 길은 뷰 `needs.topic_quarter_evidence_quote` 하나다. 이 파일이 판정·근거·코퍼스를 손으로
조인하지 않는 것이 이 이슈의 완료 기준이 실제로 서 있다는 증거다 -- 사람이 쓰는 길과 코드가 쓰는 길이
같아야 그 길이 낡지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, LiteralString

import psycopg

from analysis.cards import Card, CellFacts, Deck, Quote, alias_rank, build, render
from analysis.evidence.pipeline import FIND_RUN, NoEvidence
from analysis.retrieval import topics as topic_registry
from analysis.trend.pipeline import COMMENT, PANEL_ROLE, SCOPE, VIDEO, note_of
from analysis.types import TopicQuarterJudgementRow
from db.corpus import active_snapshot
from db.seed import panel as panel_seed

# 판정과 지표를 한 번에. 카드의 표에 서는 구성비·velocity·언급 수는 판정 표에 없고 지표 표에 있다
# (024 는 세는 칸을 하나도 들지 않는다).
CELLS: LiteralString = """
SELECT j.topic_key, j.source, j.trend_type, j.judged, j.evidence_strength, j.single_source,
       j.opportunity_score, j.gap_pp, j.hold_reason,
       m.composition, m.velocity_yoy, m.mentions
  FROM topic_quarter_judgement j
  JOIN metrics_topic_quarter m
    ON (m.run_id, m.scope, m.topic_key, m.quarter, m.source, m.content_type,
        m.panel_version, m.panel_role)
     = (j.run_id, j.scope, j.topic_key, j.quarter, j.source, j.content_type,
        j.panel_version, j.panel_role)
 WHERE j.run_id = %(run_id)s AND j.scope = %(scope)s AND j.panel_version = %(panel_version)s
   AND j.panel_role = %(panel_role)s AND j.quarter = %(quarter)s
"""
# 격자에 있는 분기. 없는 분기를 물었을 때 "judge 를 돌려라"가 아니라 있는 분기를 말해 주기 위한 것이다.
QUARTERS: LiteralString = (
    "SELECT DISTINCT quarter FROM topic_quarter_judgement "
    "WHERE run_id = %(run_id)s AND scope = %(scope)s AND panel_version = %(panel_version)s "
    "AND panel_role = %(panel_role)s"
)
# 셀에서 근거 원문까지 한 줄 (db/views/topic_quarter_evidence_quote.sql).
QUOTES: LiteralString = """
SELECT topic_key, rank, like_count, matched_term, text, parent_video_url
  FROM topic_quarter_evidence_quote
 WHERE run_id = %(run_id)s AND scope = %(scope)s AND panel_version = %(panel_version)s
   AND panel_role = %(panel_role)s AND quarter = %(quarter)s
 ORDER BY topic_key, rank
"""


@dataclass(frozen=True)
class CardOutcome:
    run_id: int
    quarter: str
    cards: list[Card] = field(default_factory=list)
    # 규칙에 걸렸는데 근거 원문이 없어 카드로 서지 못한 (주제, 분기).
    unquoted: tuple[tuple[str, str], ...] = ()

    @property
    def status(self) -> str:
        """**카드 0건은 1 이 아니다.** 그것은 규칙이 다 돌고 나온 정상적으로 계산된 답이고(이 표본에서도
        11분기 중 8분기가 0장이다), 이 파일 맨 위의 공통 규약에서 1 은 "산출이 온전하지 않다"는 뜻이다 --
        #41 이 `sensitivity` 에서 못 박은 그 자리와 같다 (`contracts/entrypoints.md` §민감도).

        잘린 산출은 하나뿐이다: 규칙에 걸렸는데 근거 원문이 없어 카드로 서지 못한 셀.
        """
        return "ok" if not self.unquoted else "partial"

    @property
    def note(self) -> str:
        kinds = " ".join(f"{card.topic_key}={card.card_type}" for card in self.cards)
        tail = f" unquoted={len(self.unquoted)}" if self.unquoted else ""
        return (
            f"trend cards run={self.run_id} quarter={self.quarter} "
            f"cards={len(self.cards)} {kinds}{tail}".rstrip()
        )

    @property
    def violations(self) -> list[str]:
        """카드는 표를 만들지 않으므로 되물을 뷰가 없다. 잘린 자리를 같은 어휘로 싣는다."""
        return [f"unquoted_cell {quarter} topic={topic}" for topic, quarter in self.unquoted]


def _judgement(row: tuple, topic: str, quarter: str, scope: str, run_id: int, version: int, role: str):
    (
        _topic, source, trend_type, judged, strength, single, score, gap, hold, *_rest
    ) = row  # fmt: skip
    return TopicQuarterJudgementRow(
        run_id=run_id,
        scope=scope,
        topic_key=topic,
        quarter=quarter,
        source=str(source),
        content_type="long_form",
        panel_version=version,
        panel_role=role,
        trend_type=str(trend_type),
        judged=bool(judged),
        evidence_strength=float(strength),
        single_source=bool(single),
        opportunity_score=None if score is None else float(score),
        gap_pp=None if gap is None else float(gap),
        hold_reason=str(hold or ""),
    )


def collect(
    conn: psycopg.Connection[Any],
    quarter: str,
    *,
    scope: str = SCOPE,
    panel_role: str = PANEL_ROLE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
) -> CardOutcome:
    """그 분기의 카드. 읽자마자 커밋하고 그 뒤로는 DB 를 보지 않는다 (15초 타임아웃)."""
    with conn.cursor() as cur:
        version = panel_version if panel_version is not None else panel_seed.active_version(cur)
        snapshot = snapshot_id if snapshot_id is not None else active_snapshot(cur)
        if version is None or snapshot is None:
            raise NoEvidence("no active panel roster or corpus snapshot; seed them first")
        cur.execute(FIND_RUN, (note_of(scope, snapshot, version),))
        found = cur.fetchone()
        if found is None:
            raise NoEvidence(
                f"no quarter run for {scope!r} on snapshot {snapshot}; run `cosmai trend quarter`"
            )
        run_id = int(found[0])
        where = {
            "run_id": run_id,
            "scope": scope,
            "panel_version": version,
            "panel_role": panel_role,
            "quarter": quarter,
        }
        cur.execute(CELLS, where)
        rows = cur.fetchall()
        cur.execute(QUOTES, where)
        quoted = cur.fetchall()
        cur.execute(QUARTERS, {k: v for k, v in where.items() if k != "quarter"})
        known_quarters = cur.fetchall()
        ranks = alias_rank(topic_registry.load(conn).entries)
    conn.commit()

    if not rows:
        # 이 분기가 격자에 없다는 뜻일 수도 있다 -- judge 는 이미 돌았는데 그 분기에 모집단 영상이 없는
        # 것이 이 표본에서 실제로 일어난다(2025Q1). 두 갈래를 한 문장으로 말하면 헛걸음을 시킨다.
        cur_quarters = sorted(quarter for (quarter,) in known_quarters)
        if cur_quarters:
            raise NoEvidence(
                f"run {run_id} has no judged cell for {quarter}; that quarter is not in this run's "
                f"grid (it has {', '.join(cur_quarters)})"
            )
        raise NoEvidence(f"run {run_id} has no topic_quarter_judgement row; run `cosmai trend judge`")

    by_topic: dict[str, dict[str, tuple]] = {}
    for row in rows:
        by_topic.setdefault(str(row[0]), {})[str(row[1])] = row
    facts: list[CellFacts] = []
    for topic, sides in sorted(by_topic.items()):
        comment, video = sides.get(COMMENT), sides.get(VIDEO)
        made = comment or video
        assert made is not None
        facts.append(
            CellFacts(
                topic_key=topic,
                quarter=quarter,
                comment=_judgement(comment, topic, quarter, scope, run_id, version, panel_role)
                if comment
                else None,
                video=_judgement(video, topic, quarter, scope, run_id, version, panel_role)
                if video
                else None,
                comment_composition=None if comment is None or comment[9] is None else float(comment[9]),
                video_composition=None if video is None or video[9] is None else float(video[9]),
                velocity_yoy=None if made[10] is None else float(made[10]),
                mentions=None if comment is None else int(comment[11]),
            )
        )

    quotes: dict[tuple[str, str], list[Quote]] = {}
    for topic, rank, likes, term, text, url in quoted:
        quotes.setdefault((str(topic), quarter), []).append(
            Quote(
                rank=int(rank),
                like_count=int(likes),
                matched_term=term,
                text=str(text or ""),
                parent_video_url=url,
            )
        )
    deck: Deck = build(facts, quotes=quotes, alias_rank=ranks)
    return CardOutcome(run_id, quarter, list(deck.cards), deck.unquoted)


def report(outcome: CardOutcome) -> str:
    return render(outcome.cards, outcome.quarter)
