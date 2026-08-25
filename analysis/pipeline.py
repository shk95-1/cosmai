"""`cosmai analyze <stage>` 의 배선 한 자리: link → polarity → aggregate 를 한 run 으로 묶는다 (#5).

세 유닛의 `pipeline.run()` 은 여기서만 이어 붙는다. 각 단계의 배치 커밋 경계는 그대로 둔다 — 전체를
한 트랜잭션으로 감싸면 needs_runtime 의 transaction_timeout 60s 안에 끝날 수 없다 (db/bootstrap.sql).
run 행은 polarity 가 열고 aggregate 가 그 run_id 로 metrics 를 쓴다: 셋이 한 run 을 나눠 쓴다.
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
from analysis.polarity.ownership import OWNERS
from analysis.polarity.pipeline import MARKER
from analysis.types import Polarity

__all__ = ["POPULATION", "StageOutcome", "run_stage"]

COMMERCE_SCHEMA = "trend_radar"
YOUTUBE_SCHEMA = "tubedepth"
# aggregate 는 이 run 이 방금 쓴 버전 계열만 센다 — 시드(slice-*)를 같은 scope 에 섞으면 한 문장이 두 번
# 세어지고 어떤 규칙이 만든 수인지 되짚을 수 없다. 고른 모집단은 analysis_run.versions.extractor 에 남는다.
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
# category (need_mention.category) — a --scope can own the first axis and miss the second entirely,
# so a multi-hour pass writes labels and aggregates none of them. wish is never scoped (comments carry
# no category), so it alone hitting 0 is normal; both hitting 0 after aggregate ran is not.
SILENT_SCOPE = (
    "--scope {scope!r} wrote 0 metrics_need and 0 metrics_wish rows: aggregate scopes by the source "
    "category (need_mention.category), not lexicon_category — rerun aggregate with --scope matching "
    "the source category: {categories}"
)
SCOPE_CATEGORIES: LiteralString = (
    "SELECT DISTINCT category FROM need_mention WHERE lexicon_category = %s ORDER BY 1"
)
SILENT_CLOSE: LiteralString = (
    "UPDATE analysis_run SET status = 'partial', note = note || %s WHERE run_id = %s"
)
# 재현에 필요한 것은 첫 줄이다 — psycopg 는 여기에 쿼리 전문을 붙여 note 를 통째로 삼킨다.
DETAIL_CHARS = 160

FAILURES = (psycopg.Error, LookupError, ValueError)
NOTE: LiteralString = "analyze:{stage}"
RUN_FAILED: LiteralString = (
    "INSERT INTO analysis_run (status, finished_at, versions, note) VALUES ('failed', now(), %s::jsonb, %s)"
)
# versions 는 덮지 않고 합친다 — 단독 폴라리티 실행은 자기가 라벨한 판본을 RUN_START 에서만 적고
# (analysis/polarity/pipeline.py), 여기서 통째로 덮으면 4시간짜리 gemma4 패스가 무엇으로 라벨하다
# 죽었는지가 analysis_health.polarity_version 에서 사라진다 (contracts/versioning.md).
RUN_CLOSE: LiteralString = (
    "UPDATE analysis_run SET status = %s, finished_at = now(), versions = versions || %s::jsonb, "
    "note = %s WHERE run_id = %s"
)
RUN_SKIPPED: LiteralString = (
    "INSERT INTO analysis_run (status, finished_at, versions, note) "
    "VALUES ('partial', now(), '{}'::jsonb, %s)"
)
NOTE_OF: LiteralString = "SELECT note FROM analysis_run WHERE run_id = %s"
# 이미 한 번 말한 표식에 붙는 꼬리표. status 로는 이 구별이 안 된다: 실무에서 가장 흔한 죽음(ollama
# 예외·statement_timeout)은 FAILURES 로 잡혀 _close 가 failed 로 닫으므로 'running' 만 보면 그 반쪽
# 달을 통째로 놓치고, 반대로 status 를 빼기만 하면 주인 있는 scope 의 달은 아무도 메우지 않아 매일 밤
# 같은 partial 이 나온다.
REPORTED = "stale-reported"
# analyze 락을 쥔 동안 열려 있는 rewriting 표식은 죽은 실행의 것뿐이다 — 산 실행은 락을 못 잡는다.
ABANDONED: LiteralString = "SELECT run_id, note FROM analysis_run WHERE note LIKE %s AND note NOT LIKE %s"
ABANDONED_CLOSE: LiteralString = (
    "UPDATE analysis_run SET status = 'failed', finished_at = coalesce(finished_at, now()), "
    "note = note || %s WHERE run_id = %s"
)
# polarity 는 자기 run 을 열고 실패해도 여기서 닫는다. aggregate 도 note 로 run 을 찾아 열거나 되살려
# 'running' 으로 만들지만(analysis/aggregate/pipeline.py `_run_id`) 그 run_id 는 여기로 올라오지 않아,
# 단독 `analyze aggregate` 가 실패하면 그 행은 'running' 인 채 남는다 — 표식이 없어 다음 실행도 못 찾는다.
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
    """#17 판정: lexicon 은 활성 버전 + ruleset 이다 — aspect 사전은 ruleset 마다 따로 켜진다."""
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
    """죽은 실행이 반쯤 다시 쓰고 만 달들 — 죽음이 잡혔든(failed) 아니든(running) 표식으로 찾는다.

    여기서 닫아 두는 것은 상태뿐이다: 그 달을 메우는 것은 주인의 일이고(주인 있는 scope 는 규칙이
    배제한다), 표식은 note 에 남아 어느 달인지 계속 말한다. 말하는 것은 한 번뿐이라 꼬리표를 붙인다.
    """
    with conn.cursor() as cur:
        cur.execute(ABANDONED, (f"%{MARKER}%", f"%{REPORTED}%"))
        found = [(int(run_id), str(note or "")) for run_id, note in cur.fetchall()]
        for run_id, _ in found:
            cur.execute(ABANDONED_CLOSE, (f" {REPORTED}", run_id))
    conn.commit()
    return tuple(note.partition(MARKER)[2].split(" ")[0] for _, note in found)


def _carried(conn: psycopg.Connection[Any], run_id: int, note: str) -> str:
    """실패 메시지가 표식을 지우면 어느 달이 반쪽인지 아무도 모른다 — 그 토막만 새 note 로 옮긴다."""
    with conn.cursor() as cur:
        cur.execute(NOTE_OF, (run_id,))
        row = cur.fetchone()
    _, found, tail = (row[0] if row and row[0] else "").partition(MARKER)
    return f"{note} {MARKER}{tail}" if found else note


def _amend(outcome: StageOutcome, stale: Sequence[str]) -> StageOutcome:
    """성공했어도 조용히 끝내지 않는다: 이 실행이 아니라 죽은 실행이 남긴 구멍을 종료 코드로 말한다."""
    if not stale or outcome.status != OK:
        return outcome
    return replace(outcome, status=PARTIAL, detail=STALE.format(" ".join(stale)))


def _skipped(conn: psycopg.Connection[Any], stage: str) -> StageOutcome:
    outcome = StageOutcome(stage, PARTIAL, None, {}, SKIPPED)
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute(RUN_SKIPPED, (outcome.note,))
    conn.commit()
    return outcome


def _scope_categories(conn: psycopg.Connection[Any], scope: str) -> tuple[str, ...]:
    """The source category values aggregate actually filters on for this scope's lexicon_category."""
    with conn.cursor() as cur:
        cur.execute(SCOPE_CATEGORIES, (scope,))
        found = tuple(str(r[0]) for r in cur.fetchall() if r[0])
    conn.rollback()
    return found


def _amend_silent_scope(
    conn: psycopg.Connection[Any], outcome: StageOutcome, scope: str | None, close_run: bool = False
) -> StageOutcome:
    """A scoped run that reached aggregate and wrote 0 metrics rows is not a quiet success (#38).

    Unscoped runs (the 05:00 cron) never take this branch — that predicate never fires without a scope.
    """
    if scope is None or outcome.status != OK or "metrics_need" not in outcome.counts:
        return outcome
    if outcome.counts.get("metrics_need") or outcome.counts.get("metrics_wish"):
        return outcome
    categories = _scope_categories(conn, scope)
    hint = ", ".join(categories) if categories else "none found for that lexicon_category"
    detail = SILENT_SCOPE.format(scope=scope, categories=hint)
    amended = replace(outcome, status=PARTIAL, detail=detail)
    # standalone `analyze aggregate` already closed its own run as 'ok' before we could see the counts.
    if close_run and outcome.run_id is not None:
        with conn.cursor() as cur:
            cur.execute(SILENT_CLOSE, (f" {PARTIAL}:{detail}", outcome.run_id))
        conn.commit()
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
    # 세션이 끊긴 뒤(idle_in_transaction 등)에는 rollback 조차 던진다 — 크론 메일에 트레이스백 대신 한 줄.
    except psycopg.Error as unreachable:
        detail = f"{outcome.detail} run-not-closed {str(unreachable).splitlines()[0][:DETAIL_CHARS]}"
        return replace(outcome, status=FAILED, detail=detail)
    return replace(outcome, run_id=run_id)


def run_all(
    conn: psycopg.Connection[Any],
    since: date | None,
    scope: str | None,
    commerce_schema: str,
    youtube_schema: str,
    captured_at: date | None,
    polarity: Polarity | None = None,
    owners: Mapping[str, str] = OWNERS,
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
            commerce_schema=commerce_schema,
            youtube_schema=youtube_schema,
            polarity=polarity,
            owners=owners,
            on_run_open=opened,
        )
        # upsert 가 실제로 넣은 수가 아니라 시도한 수다 — 시드와 자연키가 겹치는 문장은 자기 행을 못 만든다.
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
    commerce_schema: str = COMMERCE_SCHEMA,
    youtube_schema: str = YOUTUBE_SCHEMA,
    captured_at: date | None = None,
    polarity: Polarity | None = None,
    owners: Mapping[str, str] = OWNERS,
) -> StageOutcome:
    """한 실행 = 한 락. 못 잡으면 아무것도 열지 않고 양보한다 (analysis/locks.py)."""
    with analyze_lock(conn) as held:
        if not held:
            return _skipped(conn, stage)
        stale = _abandoned(conn)
        if stage == "all":
            return run_all(
                conn, since, scope, commerce_schema, youtube_schema, captured_at, polarity, owners, stale
            )
        return _one(
            conn, stage, since, scope, commerce_schema, youtube_schema, captured_at, polarity, owners, stale
        )


def _one(
    conn: psycopg.Connection[Any],
    stage: str,
    since: date | None,
    scope: str | None,
    commerce_schema: str,
    youtube_schema: str,
    captured_at: date | None,
    polarity: Polarity | None,
    owners: Mapping[str, str],
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
            return _amend(StageOutcome(stage, OK, None, {n: linked[n] for n in LINK_COUNTS}), stale)
        if stage == "polarity":
            # 이 단계는 자기 run 을 열고 닫는다 — 단독 실행의 run 은 polarity 것이다.
            found = polarity_stage.run(
                conn,
                since=since,
                scope=scope,
                commerce_schema=commerce_schema,
                youtube_schema=youtube_schema,
                polarity=polarity,
                owners=owners,
                on_run_open=opened,
            )
            counts = {"attempted_need": found.need_rows, "attempted_wish": found.wish_rows}
            return _amend(StageOutcome(stage, OK, found.run_id, counts), stale)
        aggregated = aggregate_stage.run(
            conn,
            scope=scope,
            commerce_schema=commerce_schema,
            captured_at=captured_at,
            extractors=POPULATION,
        )
        outcome = _amend(StageOutcome(stage, OK, aggregated, _metrics_counts(conn, aggregated)), stale)
        return _amend_silent_scope(conn, outcome, scope, close_run=True)
    except FAILURES as failure:
        outcome = StageOutcome(stage, FAILED, run_id, {}, _detail(stage, failure))
        if stage not in OPENS_RUN:
            try:
                conn.rollback()
            except psycopg.Error:
                pass
            return outcome
        # 거절은 run 이 열리기 전에도 온다 (남의 scope) — 그때는 닫을 행이 없으니 새 failed 행을 남긴다.
        return _close(conn, outcome, {})
