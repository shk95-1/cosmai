"""One place for the wiring of `cosmai analyze <stage>`: link -> polarity -> aggregate tied into one run (#5).

The `pipeline.run()` of the three units is joined only here. The batch commit boundaries of each stage are
left as they are -- wrapping the whole thing in one transaction cannot finish inside needs_runtime's
transaction_timeout of 60s (db/bootstrap.sql). The run row is opened by polarity and aggregate writes metrics
under that run_id: the three share one run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, LiteralString

import psycopg

from analysis.aggregate import AGGREGATE_VERSION
from analysis.aggregate import pipeline as aggregate_stage
from analysis.extractor import VERSION as EXTRACTOR_VERSION
from analysis.lexicon import load_aspects, load_lexicon
from analysis.linker import LINKER_VERSION
from analysis.linker import pipeline as link_stage
from analysis.locks import ANALYZE, analyze_lock
from analysis.polarity import GENERIC_RULESET, SUNCARE_RULESET
from analysis.polarity import VERSION as POLARITY_VERSION
from analysis.polarity import pipeline as polarity_stage
from analysis.polarity.ownership import OWNERS, Owner
from analysis.polarity.pipeline import MARKER
from analysis.types import Polarity

__all__ = ["POPULATION", "StageOutcome", "run_stage"]

COMMERCE_SCHEMA = "trend_radar"
YOUTUBE_SCHEMA = "tubedepth"
# aggregate counts only the version family this run has just written -- mixing the seed (slice-*) into the
# same scope counts one sentence twice and leaves no way to trace which rules made the number. The population
# picked stays in analysis_run.versions.extractor.
POPULATION = (EXTRACTOR_VERSION,)
RULESETS = (SUNCARE_RULESET, GENERIC_RULESET)
LINK_COUNTS = ("product_ref", "brand_mention")
OK = "ok"  # entrypoints.md §공통 운영 뷰의 어휘: ok | partial | blocked | failed | running
FAILED = "failed"
PARTIAL = "partial"
# 수집기가 소스를 양보할 때와 같은 어휘·같은 종료 코드다 (entrypoints.md §수집기): 사이트가 거절한 게
# 아니라 우리가 양보한 것이고, 건너뛴 일은 다음 실행이 그대로 가져간다(모든 단계가 자연키 upsert).
SKIPPED = (
    f"skipped: another analyze run holds the {ANALYZE} lock, and running both would let one read a "
    "month the other has half-rewritten"
)
STALE = "half-written month(s) left behind by a run that died: {}"
# #38: polarity scopes need_mention by lexicon_category, aggregate scopes metrics by the source
# category (need_mention.category). `scopes_for` bridges the two axes, but it can only expand a label
# into source categories its own rows carry — a name_keyword label carries none — so a scoped pass can
# still write labels and aggregate none of them. metrics_wish ignores --scope altogether
# (analysis/aggregate/pipeline.py sums the whole wish population over WISH_SCOPES every time, scoped
# or not), so it carries no signal about this scope and never enters the predicate — only a scoped
# run's metrics_need hitting 0 after aggregate ran means anything (review round 1).
SILENT_SCOPE = (
    "--scope {scope!r} wrote 0 metrics_need rows: nothing in this run's population sits in that scope "
    "on either axis — no mention carries it as its source category, and no mention labelled with it "
    "carries a source category to expand into. Source categories seen for that lexicon_category: "
    "{categories}"
)
SCOPE_CATEGORIES: LiteralString = (
    "SELECT DISTINCT category FROM need_mention WHERE lexicon_category = %s ORDER BY 1"
)
SILENT_CLOSE: LiteralString = (
    "UPDATE analysis_run SET status = 'partial', note = note || %s WHERE run_id = %s"
)
# What is needed to reproduce it is the first line -- psycopg attaches the whole query here and swallows the
# note.
DETAIL_CHARS = 160

FAILURES = (psycopg.Error, LookupError, ValueError)
NOTE: LiteralString = "analyze:{stage}"
RUN_FAILED: LiteralString = (
    "INSERT INTO analysis_run (status, finished_at, versions, note) VALUES ('failed', now(), %s::jsonb, %s)"
)
# versions are merged rather than overwritten -- a standalone polarity run writes the revision it labelled
# with only in RUN_START (analysis/polarity/pipeline.py), and overwriting it wholesale here loses what a
# four-hour gemma4 pass was labelling with when it died, out of analysis_health.polarity_version
# (contracts/versioning.md).
RUN_CLOSE: LiteralString = (
    "UPDATE analysis_run SET status = %s, finished_at = now(), versions = versions || %s::jsonb, "
    "note = %s WHERE run_id = %s"
)
RUN_SKIPPED: LiteralString = (
    "INSERT INTO analysis_run (status, finished_at, versions, note) "
    "VALUES ('partial', now(), '{}'::jsonb, %s)"
)
NOTE_OF: LiteralString = "SELECT note FROM analysis_run WHERE run_id = %s"
# The tag attached to a mark that has already been reported once. status cannot make this distinction: the
# most common death in practice (an ollama exception, a statement_timeout) is caught by FAILURES and _close
# shuts it as failed, so looking only at 'running' misses that half month entirely; and dropping status
# instead leaves the months of an owned scope filled by nobody and the same partial comes out every night.
REPORTED = "stale-reported"
# While the analyze lock is held, an open rewriting mark belongs only to a dead run -- a live run cannot take
# the lock.
ABANDONED: LiteralString = "SELECT run_id, note FROM analysis_run WHERE note LIKE %s AND note NOT LIKE %s"
ABANDONED_CLOSE: LiteralString = (
    "UPDATE analysis_run SET status = 'failed', finished_at = coalesce(finished_at, now()), "
    "note = note || %s WHERE run_id = %s"
)
# polarity opens its own run and closes it here even on failure. aggregate also finds a run by note and opens
# or revives it into 'running' (`_run_id` in analysis/aggregate/pipeline.py), but that run_id does not come
# back up here, so a standalone `analyze aggregate` that fails leaves that row at 'running' -- with no mark,
# the next run cannot find it either.
OPENS_RUN = ("polarity",)
METRICS: LiteralString = (
    "SELECT (SELECT count(*) FROM metrics_need WHERE run_id = %s), "
    "(SELECT count(*) FROM metrics_wish WHERE run_id = %s)"
)


@dataclass(frozen=True)
class StageOutcome:
    stage: str
    status: str  # ok | failed
    run_id: int | None = None
    counts: dict[str, int] = field(default_factory=dict)
    detail: str = ""

    @property
    def note(self) -> str:
        rows = " ".join(f"{name}={n}" for name, n in self.counts.items())
        tail = f" {self.status}:{self.detail}" if self.status != OK else ""
        return f"{NOTE.format(stage=self.stage)} {rows}{tail}".strip()


def _detail(stage: str, failure: Exception) -> str:
    message = str(failure).strip().splitlines()[0] if str(failure).strip() else type(failure).__name__
    return f"{stage} {message[:DETAIL_CHARS]}"


def _versions(conn: psycopg.Connection[Any], polarity_version: str = POLARITY_VERSION) -> dict[str, Any]:
    """Decision of #17: lexicon is the active version + ruleset -- an aspect dictionary is switched on per
    ruleset."""
    lexicon = load_lexicon(conn)
    aspects = {ruleset: load_aspects(conn, ruleset).version for ruleset in RULESETS}
    conn.rollback()
    return {
        "linker": LINKER_VERSION,
        "extractor": EXTRACTOR_VERSION,
        "polarity": polarity_version,
        "aggregate": AGGREGATE_VERSION,
        "lexicon": {"entity": lexicon.version, "aspect": aspects},
    }


def _abandoned(conn: psycopg.Connection[Any]) -> tuple[str, ...]:
    """The months a dead run half-rewrote -- found by the mark whether the death was caught (failed) or not
    (running).

    What is closed here is the status alone: filling that month is the owner's job (the rules exclude a scope
    that has an owner), and the mark stays in the note and keeps saying which month it was. It is said only
    once, so a tag is attached.
    """
    with conn.cursor() as cur:
        cur.execute(ABANDONED, (f"%{MARKER}%", f"%{REPORTED}%"))
        found = [(int(run_id), str(note or "")) for run_id, note in cur.fetchall()]
        for run_id, _ in found:
            cur.execute(ABANDONED_CLOSE, (f" {REPORTED}", run_id))
    conn.commit()
    return tuple(note.partition(MARKER)[2].split(" ")[0] for _, note in found)


def _carried(conn: psycopg.Connection[Any], run_id: int, note: str) -> str:
    """If a failure message erases the mark, nobody knows which month is half done -- only that fragment is
    carried into the new note."""
    with conn.cursor() as cur:
        cur.execute(NOTE_OF, (run_id,))
        row = cur.fetchone()
    _, found, tail = (row[0] if row and row[0] else "").partition(MARKER)
    return f"{note} {MARKER}{tail}" if found else note


def _amend(outcome: StageOutcome, stale: Sequence[str]) -> StageOutcome:
    """Even on success it does not end quietly: the hole left by a dead run, not by this one, is said with the
    exit code."""
    if not stale or outcome.status != OK:
        return outcome
    return replace(outcome, status=PARTIAL, detail=STALE.format(" ".join(stale)))


def _reported(conn: psycopg.Connection[Any], outcome: StageOutcome) -> StageOutcome:
    """단독 stage 실행이 찾아낸 구멍도 run 행에 남는다 — 종료 코드는 크론 메일에만 있고, 계약이
    운영자에게 보라고 한 것은 `needs.analysis_health` 의 그 행이다 (entrypoints.md §분석 실행).

    polarity opens its own run and closes it `ok`, so that row is closed again as partial -- the same one row
    as `run_all`, and since versions are only merged, what that pass labelled with stays. The note of
    aggregate is the natural key `_run_id` uses to find the run again (analysis/aggregate/pipeline.py) and
    link has no run row at all, so those two leave one reporting row where the run that yielded the lock is
    already writing.
    """
    if outcome.status == OK:
        return outcome
    if outcome.stage in OPENS_RUN and outcome.run_id is not None:
        return _close(conn, outcome, {})
    conn.rollback()
    # No numbers are carried -- no metrics hang off this row, so the view's numbers and the note's numbers
    # would disagree.
    with conn.cursor() as cur:
        cur.execute(RUN_SKIPPED, (StageOutcome(outcome.stage, PARTIAL, None, {}, outcome.detail).note,))
    conn.commit()
    return outcome


def _skipped(conn: psycopg.Connection[Any], stage: str) -> StageOutcome:
    outcome = StageOutcome(stage, PARTIAL, None, {}, SKIPPED)
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute(RUN_SKIPPED, (outcome.note,))
    conn.commit()
    return outcome


def _scope_categories(conn: psycopg.Connection[Any], scope: str) -> tuple[str, ...]:
    """The source category values this scope's lexicon_category is seen on, over the whole table.

    Wider than the expansion `scopes_for` does (that one sees only this run's population), and the
    gap is itself a reason a scope can be silent: the labels sit under another extractor_version.
    """
    with conn.cursor() as cur:
        cur.execute(SCOPE_CATEGORIES, (scope,))
        found = tuple(str(r[0]) for r in cur.fetchall() if r[0])
    conn.rollback()
    return found


def _amend_silent_scope(
    conn: psycopg.Connection[Any], outcome: StageOutcome, scope: str | None, close_run: bool = False
) -> StageOutcome:
    """A scoped run that reached aggregate and wrote 0 metrics_need rows is not a quiet success (#38).

    Unscoped runs (the 05:00 cron) never take this branch — that predicate never fires without a scope.
    Runs only `_amend`(stale) already ran on: this must still speak even when that already made the
    outcome PARTIAL, so both reasons land in the one note instead of the second silently winning
    (review round 1 #3) — never called on a FAILED outcome, so status here is always OK or PARTIAL.
    """
    if scope is None or outcome.status == FAILED or "metrics_need" not in outcome.counts:
        return outcome
    if outcome.counts.get("metrics_need"):
        return outcome
    # The category lookup is a nicety, not the finding — losing it must never leave the run open
    # (review round 1 #1): a `statement_timeout` here used to escape uncaught past `run_all`/`_one`
    # and `cosmai/cli.py`, killing the process with the run still 'running' and nothing said.
    try:
        categories = _scope_categories(conn, scope)
        hint = ", ".join(categories) if categories else "none found for that lexicon_category"
    except psycopg.Error as lookup_failure:
        conn.rollback()
        hint = f"category lookup failed: {str(lookup_failure).splitlines()[0][:DETAIL_CHARS]}"
    silent_detail = SILENT_SCOPE.format(scope=scope, categories=hint)
    detail = f"{outcome.detail}; {silent_detail}" if outcome.detail else silent_detail
    amended = replace(outcome, status=PARTIAL, detail=detail)
    # standalone `analyze aggregate` already closed its own run as 'ok' before we could see the counts.
    if close_run and outcome.run_id is not None:
        try:
            with conn.cursor() as cur:
                cur.execute(SILENT_CLOSE, (f" {PARTIAL}:{detail}", outcome.run_id))
            conn.commit()
        except psycopg.Error as close_failure:
            # same convention as _close(): a write that can't land must say so, not vanish silently.
            conn.rollback()
            tail = f"run-not-closed {str(close_failure).splitlines()[0][:DETAIL_CHARS]}"
            return replace(amended, status=FAILED, detail=f"{detail} {tail}")
    return amended


def _metrics_counts(conn: psycopg.Connection[Any], run_id: int) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(METRICS, (run_id, run_id))
        row = cur.fetchone()
    conn.rollback()
    return {"metrics_need": int(row[0]), "metrics_wish": int(row[1])} if row else {}


def _close(
    conn: psycopg.Connection[Any],
    outcome: StageOutcome,
    versions: dict[str, Any],
) -> StageOutcome:
    payload = json.dumps(versions, ensure_ascii=False)
    try:
        conn.rollback()
        run_id = outcome.run_id
        with conn.cursor() as cur:
            if run_id is None:
                cur.execute(RUN_FAILED, (payload, outcome.note))
            else:
                note = outcome.note if outcome.status == OK else _carried(conn, run_id, outcome.note)
                cur.execute(RUN_CLOSE, (outcome.status, payload, note, run_id))
        conn.commit()
    # After the session is cut (idle_in_transaction and the like) even a rollback throws -- one line in the
    # cron mail instead of a traceback.
    except psycopg.Error as unreachable:
        detail = f"{outcome.detail} run-not-closed {str(unreachable).splitlines()[0][:DETAIL_CHARS]}"
        return replace(outcome, status=FAILED, detail=detail)
    return replace(outcome, run_id=run_id)


def run_all(
    conn: psycopg.Connection[Any],
    since: date | None,
    scope: str | None,
    missing: bool,
    commerce_schema: str,
    youtube_schema: str,
    captured_at: date | None,
    polarity: Polarity | None = None,
    owners: Mapping[str, Owner] = OWNERS,
    stale: Sequence[str] = (),
) -> StageOutcome:
    counts: dict[str, int] = {}
    versions: dict[str, Any] = {}
    run_id: int | None = None
    stage = "link"

    def opened(found: int) -> None:
        nonlocal run_id
        run_id = found

    try:
        versions = _versions(conn, polarity.version if polarity else POLARITY_VERSION)
        linked = link_stage.run(
            conn, since=since, commerce_schema=commerce_schema, youtube_schema=youtube_schema
        )
        counts.update({name: linked[name] for name in LINK_COUNTS})
        stage = "polarity"
        found = polarity_stage.run(
            conn,
            since=since,
            scope=scope,
            missing=missing,
            commerce_schema=commerce_schema,
            youtube_schema=youtube_schema,
            polarity=polarity,
            owners=owners,
            on_run_open=opened,
        )
        # Not the number the upsert really inserted but the number attempted -- a sentence whose natural key
        # collides with the seed cannot make a row of its own.
        counts.update({"attempted_need": found.need_rows, "attempted_wish": found.wish_rows})
        stage = "aggregate"
        aggregate_stage.run(
            conn,
            scope=scope,
            run_id=run_id,
            commerce_schema=commerce_schema,
            captured_at=captured_at,
            extractors=POPULATION,
        )
        if run_id is not None:
            counts.update(_metrics_counts(conn, run_id))
    except FAILURES as failure:
        outcome = StageOutcome("all", FAILED, run_id, counts, _detail(stage, failure))
        return _close(conn, outcome, versions)
    outcome = _amend_silent_scope(conn, _amend(StageOutcome("all", OK, run_id, counts), stale), scope)
    return _close(conn, outcome, versions)


def run_stage(
    conn: psycopg.Connection[Any],
    stage: str,
    *,
    since: date | None = None,
    scope: str | None = None,
    missing: bool = False,
    commerce_schema: str = COMMERCE_SCHEMA,
    youtube_schema: str = YOUTUBE_SCHEMA,
    captured_at: date | None = None,
    polarity: Polarity | None = None,
    owners: Mapping[str, Owner] = OWNERS,
) -> StageOutcome:
    """One run = one lock. Failing to take it, nothing is opened and it yields (analysis/locks.py)."""
    with analyze_lock(conn) as held:
        if not held:
            return _skipped(conn, stage)
        stale = _abandoned(conn)
        if stage == "all":
            return run_all(
                conn,
                since,
                scope,
                missing,
                commerce_schema,
                youtube_schema,
                captured_at,
                polarity,
                owners,
                stale,
            )
        return _one(
            conn,
            stage,
            since,
            scope,
            missing,
            commerce_schema,
            youtube_schema,
            captured_at,
            polarity,
            owners,
            stale,
        )


def _one(
    conn: psycopg.Connection[Any],
    stage: str,
    since: date | None,
    scope: str | None,
    missing: bool,
    commerce_schema: str,
    youtube_schema: str,
    captured_at: date | None,
    polarity: Polarity | None,
    owners: Mapping[str, Owner],
    stale: Sequence[str],
) -> StageOutcome:
    run_id: int | None = None

    def opened(found: int) -> None:
        nonlocal run_id
        run_id = found

    try:
        if stage == "link":
            linked = link_stage.run(
                conn, since=since, commerce_schema=commerce_schema, youtube_schema=youtube_schema
            )
            done = StageOutcome(stage, OK, None, {n: linked[n] for n in LINK_COUNTS})
            return _reported(conn, _amend(done, stale))
        if stage == "polarity":
            # This stage opens and closes its own run -- the run of a standalone execution is polarity's.
            found = polarity_stage.run(
                conn,
                since=since,
                scope=scope,
                missing=missing,
                commerce_schema=commerce_schema,
                youtube_schema=youtube_schema,
                polarity=polarity,
                owners=owners,
                on_run_open=opened,
            )
            counts = {"attempted_need": found.need_rows, "attempted_wish": found.wish_rows}
            return _reported(conn, _amend(StageOutcome(stage, OK, found.run_id, counts), stale))
        aggregated = aggregate_stage.run(
            conn,
            scope=scope,
            commerce_schema=commerce_schema,
            captured_at=captured_at,
            extractors=POPULATION,
        )
        counted = StageOutcome(stage, OK, aggregated, _metrics_counts(conn, aggregated))
        # The order is the invariant: `_amend_silent_scope` closes its own run row as partial with
        # SILENT_CLOSE, so putting it inside `_reported` attaches a reporting row to a single silence and
        # leaves two partial rows.
        reported = _reported(conn, _amend(counted, stale))
        return _amend_silent_scope(conn, reported, scope, close_run=True)
    except FAILURES as failure:
        outcome = StageOutcome(stage, FAILED, run_id, {}, _detail(stage, failure))
        if stage not in OPENS_RUN:
            try:
                conn.rollback()
            except psycopg.Error:
                pass
            return outcome
        # A refusal also comes before the run is opened (someone else's scope) -- then there is no row to
        # close, so a new failed row is left.
        return _close(conn, outcome, {})
