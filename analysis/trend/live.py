"""The live analysis chain and its gate: `match:topic` -> `trend quarter` -> `judge` -> `evidence` (fork #96).

None of the three analysis stages takes a snapshot on the command line -- each reads the **active** snapshot,
and until the coordinator switches it the active snapshot is the archive. A cron line calling them directly
would re-open the archive's run every night and recompute it, which #93 D0 forbids. So one command owns the
gate and the cron line calls only it: it resolves the active snapshot once, refuses unless that snapshot is
`live` and its `instrument.text_parts` carries a description, and then hands the same snapshot id and one
cutoff to every stage, so none of them re-reads `active` mid-chain.

The text_parts half is upstream shk95-1/cosmai#264's gate: a title-only live text carries 915 of 3,534 of
the archive's topic hits (25.9%, fork #95), and confident verdicts on a quarter of the mentions are worse
than none.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, LiteralString, Protocol

import psycopg

from analysis.evidence.pipeline import run as evidence_run
from analysis.judge.pipeline import run as judge_run
from analysis.trend.pipeline import run as quarter_run
from db.corpus.match import match
from db.corpus.project import LIVE_LINEAGE

REQUIRED_TEXT_PART = "description"

GATE_SQL: LiteralString = "SELECT snapshot_id, lineage, instrument FROM corpus_snapshot WHERE active"


class LiveGateClosed(LookupError):
    """The active snapshot is not one the live chain may analyse. Blocked, not failed: nothing ran."""


class StageOutcome(Protocol):
    @property
    def note(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def violations(self) -> Any: ...


def gate(conn: psycopg.Connection[Any]) -> int:
    """The active snapshot's id when the chain may run on it; otherwise LiveGateClosed naming the reason."""
    with conn.cursor() as cur:
        cur.execute(GATE_SQL)
        found = cur.fetchone()
    conn.commit()
    if found is None:
        raise LiveGateClosed("blocked: no active corpus snapshot; the live chain has nothing to read")
    snapshot_id, lineage, instrument = int(found[0]), found[1], dict(found[2] or {})
    if lineage != LIVE_LINEAGE:
        raise LiveGateClosed(
            f"blocked: the active snapshot {snapshot_id} is lineage {lineage!r}, not {LIVE_LINEAGE!r}; the"
            " archive is read, never recomputed (#93 D0). Switching `active` is the coordinator's step"
        )
    parts = instrument.get("text_parts") or []
    if REQUIRED_TEXT_PART not in parts:
        raise LiveGateClosed(
            f"blocked: the active snapshot {snapshot_id} has text_parts={parts!r} without"
            f" {REQUIRED_TEXT_PART!r}; a title-only text carries 25.9% of the topic hits"
            " (upstream shk95-1/cosmai#264 is the fix)"
        )
    return snapshot_id


def run(
    conn: psycopg.Connection[Any],
    *,
    cutoff: datetime | None = None,
    report: Callable[[StageOutcome], None] | None = None,
) -> list[StageOutcome]:
    """The four stages in order on the gated snapshot. A stage that is blocked raises and stops the chain;
    the outcomes already reported stand."""
    snapshot_id = gate(conn)
    at = cutoff or datetime.now(UTC)
    steps: tuple[Callable[[], StageOutcome], ...] = (
        lambda: match(conn, snapshot_id=snapshot_id, cutoff=at),
        lambda: quarter_run(conn, snapshot_id=snapshot_id, cutoff=at),
        lambda: judge_run(conn, snapshot_id=snapshot_id),
        lambda: evidence_run(conn, snapshot_id=snapshot_id),
    )
    outcomes: list[StageOutcome] = []
    for step in steps:
        outcome = step()
        outcomes.append(outcome)
        if report:
            report(outcome)
    return outcomes
