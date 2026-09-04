"""저장된 세 표 → 기회 카드 (포크 #6). **아무것도 쓰지 않는다.**

Why cards make no table is carried by the contract's §Opportunity cards -- every number is already stored
(ydc design principle 2) and a second copy fights over being canonical the moment it exists. It is not dropped
to a file either (the same convention as `retrieval terms`): a snapshot of a growing corpus goes stale in the
repo, so redirect it if you want to keep it.

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

# The judgement and the metrics in one. The share, velocity and mention counts that stand in a card's table
# are not in the judgement table but in the metrics table (024 holds not one counted column).
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
# The quarters that are in the grid. It is there so that asking for a quarter that is not says which quarters
# there are rather than "run judge".
QUARTERS: LiteralString = (
    "SELECT DISTINCT quarter FROM topic_quarter_judgement "
    "WHERE run_id = %(run_id)s AND scope = %(scope)s AND panel_version = %(panel_version)s "
    "AND panel_role = %(panel_role)s"
)
# From a cell to the evidence text in one line (db/views/topic_quarter_evidence_quote.sql).
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
    # The (topic, quarter) that matched the rules but could not stand as a card for want of evidence text.
    unquoted: tuple[tuple[str, str], ...] = ()

    @property
    def status(self) -> str:
        """**Zero cards is not a 1.** It is the normally computed answer after every rule has run (in this
        sample too, 8 of 11 quarters have none), and in the common convention at the top of this file a 1
        means "the output is not whole" -- the same place #41 pinned in `sensitivity`
        (`contracts/entrypoints.md` §Sensitivity).

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
        """Cards make no table, so there is no view to ask back. The truncated places are carried in the same
        vocabulary."""
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
    """The cards of that quarter. It commits as soon as it has read and does not look at the DB after that
    (the 15-second timeout)."""
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
        # It may also mean this quarter is not in the grid -- judge has already run and there are simply no
        # population videos in that quarter, which really happens in this sample (2025Q1). Saying the two
        # branches in one sentence sends people on a wasted trip.
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
