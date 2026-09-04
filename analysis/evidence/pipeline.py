"""`needs.corpus_*` + `needs.topic_quarter_judgement` → `needs.topic_quarter_evidence` (포크 #6).

Unlike the judgement, evidence is **a stage that scans the corpus**. So it runs into the trap #40 never met:
`needs_runtime`'s `idle_in_transaction_session_timeout` is 15 seconds, so holding the candidates on a cursor
and starting to fold them cuts the connection. Committing with `conn.commit()` as soon as it has read and not
looking at the DB after that is the shape of this file, and `analysis/trend/pipeline.py` has the same shape
for the same reason. What is pulled in is pointers and like counts, not bodies -- the body is joined by the
view when it needs one. Over the whole set (261,317 documents) 15,602 candidate rows · 178ms of query ·
명령 전체 0.52s · 최대 상주 73MB 로 **재 봤다**(2026-08-26, 계약 §근거 "전량 실측"); 재지 않은 채
"가볍다"고 적어 두면 그것은 다음 사람이 밟을 단언이다.

**The population is not written again.** The `POPULATION` CTE that built the metrics is imported and used as
it is. Choosing the evidence from a different population would stand the speech a card quotes and the numbers
written on that card on different denominators, and both would look plausible enough to hide it
보이지 않는다 (계약 §근거).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, LiteralString

import psycopg

from analysis.evidence import EVIDENCE_VERSION, TOP_PER_CELL, Candidate, select
from analysis.trend.pipeline import (
    COMMENT,
    CONTENT_TYPE,
    CORPUS_COMMENT,
    PANEL_ROLE,
    POPULATION,
    SCOPE,
    TOPIC_FILTER,
    note_of,
)
from analysis.types import TopicQuarterEvidenceRow
from db.corpus import active_snapshot
from db.seed import panel as panel_seed

FIND_RUN: LiteralString = "SELECT run_id FROM analysis_run WHERE note = %s ORDER BY run_id LIMIT 1"
# Where the evidence attaches. Reading only judged cells is how no row the FK of 025 would refuse is made.
CELLS: LiteralString = (
    "SELECT DISTINCT topic_key, quarter, source FROM topic_quarter_judgement "
    "WHERE run_id = %s AND scope = %s AND panel_version = %s AND panel_role = %s"
)
# The two predicates sit side by side because the contract does not guarantee they are equivalent, and the
# partial index of 023 is chosen by `content_type` so the plan is unchanged -- with `source` alone it scans
# 260k rows (the same place in `analysis/trend/pipeline.py`, measured in #5).
#
# quality_flags is not filtered here because a gate in two places drifts. The four rules are carried by
# `analysis/evidence` alone, and this query only brings the candidates over.
CANDIDATES: LiteralString = (
    POPULATION
    + f"""
SELECT c.doc_id, v.quarter, m.topic_id, c.source, c.channel_id,
       c.source_metadata->>'like_count'          AS like_count,
       c.source_metadata->>'author_channel_hash' AS author_channel_hash,
       c.quality_flags, m.matched_term
  FROM corpus_document c
  JOIN video v ON v.source_item_id = c.parent_item_id
  JOIN corpus_mention m ON m.snapshot_id = %(snapshot)s AND m.doc_id = c.doc_id AND m.trend_use
 WHERE c.snapshot_id = %(snapshot)s AND c.content_type = '{CORPUS_COMMENT}' AND c.source = '{COMMENT}'
"""
)  # noqa: S608
STAMP_VERSION: LiteralString = (
    "UPDATE analysis_run SET versions = coalesce(versions, '{}'::jsonb) || %s::jsonb WHERE run_id = %s"
)
# TODO(#200): `content_type` is in neither this predicate nor note_of(), so a short_form run
# deletes the same run's long_form evidence -- the same four columns as
# `analysis/trend/pipeline.py`·`analysis/judge/pipeline.py`.
CLEAR: LiteralString = (
    "DELETE FROM topic_quarter_evidence "
    "WHERE run_id = %s AND scope = %s AND panel_version = %s AND panel_role = %s"
)
INSERT: LiteralString = """
INSERT INTO topic_quarter_evidence
  (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role,
   rank, snapshot_id, doc_id, like_count, matched_term)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""
VIOLATIONS: LiteralString = (
    "SELECT violation, quarter, detail FROM topic_quarter_evidence_violation WHERE run_id = %s"
)


class NoEvidence(LookupError):
    """There is nothing to take as evidence. It has not been counted yet, so this is blocked rather than a
    failure -- writing 0 rows quietly makes the invariants true over an empty table too, and the card cannot
    tell "no cell matched the rule" from "there is no evidence"."""


def _int(value: Any) -> int:
    """A like count lives as a string inside jsonb. A value that cannot be read is 0 -- ydc read it that way
    too."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class Built:
    """One evidence set before loading. The golden set and the tests write nothing to the DB and look only at
    this."""

    run_id: int
    snapshot_id: int
    panel_version: int
    rows: list[TopicQuarterEvidenceRow]
    candidates: list[Candidate]


@dataclass(frozen=True)
class EvidenceOutcome:
    run_id: int
    snapshot_id: int
    panel_version: int
    written: int
    candidates: int
    cells: int
    violations: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ok" if not self.violations else "partial"

    @property
    def note(self) -> str:
        tail = f" partial:{len(self.violations)} violations" if self.violations else ""
        return (
            f"trend evidence run={self.run_id} snapshot={self.snapshot_id} "
            f"panel=v{self.panel_version} candidates={self.candidates} cells={self.cells} "
            f"rows={self.written}{tail}"
        )


def build(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    panel_role: str = PANEL_ROLE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
    top: int = TOP_PER_CELL,
) -> Built:
    """Reads the judged cells and the candidates, closes the transaction, and picks. The run is found the
    same way as `quarter` and `judge`."""
    with conn.cursor() as cur:
        version = panel_version if panel_version is not None else panel_seed.active_version(cur)
        snapshot = snapshot_id if snapshot_id is not None else active_snapshot(cur)
        if version is None:
            raise NoEvidence("no active panel roster; run `python -m db.seed --only panel` first")
        if snapshot is None:
            raise NoEvidence("no active corpus snapshot; run `python -m db.corpus load <dir>` first")
        cur.execute(FIND_RUN, (note_of(scope, snapshot, version),))
        found = cur.fetchone()
        if found is None:
            raise NoEvidence(
                f"no quarter run for {scope!r} on snapshot {snapshot}; run `cosmai trend quarter`"
            )
        run_id = int(found[0])
        cur.execute(CELLS, (run_id, scope, version, panel_role))
        cells = {(str(topic), str(quarter), str(source)) for topic, quarter, source in cur.fetchall()}
        if not cells:
            raise NoEvidence(f"run {run_id} has no topic_quarter_judgement row; run `cosmai trend judge`")
        cur.execute(
            CANDIDATES,
            {
                "snapshot": snapshot,
                "panel_version": version,
                "panel_role": panel_role,
                "topic_filter": TOPIC_FILTER,
            },
        )
        candidates = [
            Candidate(
                doc_id=str(doc_id),
                quarter=str(quarter),
                topic_key=str(topic_id),
                source=str(source),
                channel_id=str(channel_id),
                like_count=_int(like_count),
                author_channel_hash=str(author_hash or ""),
                quality_flags=str(flags or ""),
                matched_term=term,
            )
            for doc_id, quarter, topic_id, source, channel_id, like_count, author_hash, flags, term in (
                cur.fetchall()
            )
        ]
    conn.commit()

    if not candidates:
        raise NoEvidence(f"run {run_id} has no comment in the judged population to quote")
    rows = select(
        candidates,
        run_id=run_id,
        scope=scope,
        content_type=CONTENT_TYPE,
        panel_version=version,
        panel_role=panel_role,
        snapshot_id=snapshot,
        cells=cells,
        top=top,
    )
    if not rows:
        raise NoEvidence(
            f"run {run_id} has {len(candidates)} candidates but none survived the gates "
            "(creator's own comment, quality_flags, or a topic outside the judged grid)"
        )
    return Built(run_id, snapshot, version, rows, candidates)


def _values(row: TopicQuarterEvidenceRow) -> tuple[Any, ...]:
    return (
        row.run_id, row.scope, row.topic_key, row.quarter, row.source, row.content_type,
        row.panel_version, row.panel_role, row.rank, row.snapshot_id, row.doc_id,
        row.like_count, row.matched_term,
    )  # fmt: skip


def run(
    conn: psycopg.Connection[Any],
    *,
    scope: str = SCOPE,
    panel_role: str = PANEL_ROLE,
    snapshot_id: int | None = None,
    panel_version: int | None = None,
    top: int = TOP_PER_CELL,
) -> EvidenceOutcome:
    """Rewrites the evidence rows of that (run, scope, roster) wholesale -- a partial update puts a hole in
    the ladder of ranks."""
    made = build(
        conn,
        scope=scope,
        panel_role=panel_role,
        snapshot_id=snapshot_id,
        panel_version=panel_version,
        top=top,
    )
    payload = json.dumps({"evidence": EVIDENCE_VERSION}, ensure_ascii=False)
    with conn.cursor() as cur:
        cur.execute(CLEAR, (made.run_id, scope, made.panel_version, panel_role))
        cur.executemany(INSERT, [_values(row) for row in made.rows])
        cur.execute(STAMP_VERSION, (payload, made.run_id))
        # The stored rows answer, not a sentence of the contract -- do the ranks run on from 1, does the
        # evidence belong to that cell.
        cur.execute(VIOLATIONS, (made.run_id,))
        violations = [f"{name} {quarter or '-'} {detail}" for name, quarter, detail in cur.fetchall()]
    conn.commit()
    return EvidenceOutcome(
        run_id=made.run_id,
        snapshot_id=made.snapshot_id,
        panel_version=made.panel_version,
        written=len(made.rows),
        candidates=len(made.candidates),
        cells=len({(row.topic_key, row.quarter, row.source) for row in made.rows}),
        violations=violations,
    )
