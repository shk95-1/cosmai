"""`cosmai collect youtube` -- wired for #8. `fetcher` is the seam a live cutover (#10, "라이브 yt-dlp
호출 없음" here) plugs a real yt-dlp-backed `Fetcher` into; tests plug a fixture-backed fake in, the same
shape collectors/commerce/cli.py uses for its `Fetcher`.

Four datasets, matching contracts/entrypoints.md's `youtube datasets: watch | work | flatten | prune`:
`watch` only enqueues (issue #8's fan-out cap lives entirely in `queue.py`, exercised here); `work`
claims queued jobs and collects them; `flatten` turns their artifacts into the snapshot tables; `prune`
ages artifacts and finished jobs out. Exit codes follow contracts/entrypoints.md 종료 코드: 0 ok, 1
partial, 2 blocked.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy import Connection

from collectors.youtube import flatten, queue, sources
from collectors.youtube.models import FRESHNESS, Dataset, JobState
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage import db as storage_db
from collectors.youtube.storage.tables import artifacts, jobs
from collectors.youtube.watchlist import WatchlistError, read_watchlist

LISTING_KINDS = frozenset({"channel.videos", "search.videos", "playlist.items", "trending.videos"})

DEFAULT_WATCHLIST = Path(__file__).resolve().parent / "watchlist.txt"
DEFAULT_WORK_BATCH = 50
FINISHED_STATES = (JobState.SUCCEEDED.value, JobState.FAILED.value, JobState.CANCELLED.value)
# 30 days: matches the archived RetentionPolicy.DEFAULT_MAXIMUM_AGE (retention.py) -- one property of
# the data (a month of history is what artifacts.fresh_until ever needed), not reinvented here.
PRUNE_MAX_AGE_DAYS = 30


@dataclass(frozen=True, slots=True)
class FetchSpec:
    kind: str
    target: str


class Fetcher(Protocol):
    def fetch(self, spec: FetchSpec) -> dict[str, Any]: ...


class _RaisingFetcher:
    """The default fetcher: fails loudly rather than opening a real socket. A live cutover (#10)
    replaces this."""

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:  # pragma: no cover - only if actually called
        raise NotImplementedError(
            "collectors.youtube has no live transport yet; see issue #10 (cutover). "
            "Tests inject a fixture-backed fetcher instead of calling the CLI's default."
        )


def run(
    dataset: str,
    board: str | None = None,
    since: str | None = None,
    *,
    database_url: str | None = None,
    fetcher: Fetcher | None = None,
    watchlist_path: Path | None = None,
    payload_root: Path | None = None,
    captured_at: datetime | None = None,
) -> int:
    """Run one dataset for one pass. `board`/`since` are accepted for the entrypoint's shape
    (contracts/entrypoints.md); neither means anything to any youtube dataset today."""
    del board, since
    try:
        wanted = Dataset(dataset)
    except ValueError:
        known = ", ".join(d.value for d in Dataset)
        print(f"no dataset named {dataset!r}; known: {known}")
        return 2

    now = captured_at or datetime.now(UTC)
    engine = storage_db.create_engine(database_url or storage_db.runtime_url())
    payloads = PayloadStore(payload_root or Path("var") / "youtube-payloads")
    try:
        prune_result: PruneResult | None = None
        with engine.begin() as conn:
            if wanted is Dataset.WATCH:
                return _run_watch(conn, watchlist_path or DEFAULT_WATCHLIST, now=now)
            if wanted is Dataset.WORK:
                return _run_work(conn, payloads, fetcher or _RaisingFetcher(), now=now)
            if wanted is Dataset.FLATTEN:
                return _run_flatten(conn, payloads, now=now)
            prune_result = _run_prune(conn, now=now)
        # Row deletes just committed above (the `with` block exited); only now is it safe to unlink
        # files -- a file delete can't roll back, so doing it before commit would strand rows over an
        # orphan file on any later failure in the same transaction.
        removed_files = sum(payloads.delete(kind, digest) for kind, digest in prune_result.orphaned)
        print(
            f"pruned {prune_result.removed_artifacts} artifact(s), "
            f"{prune_result.removed_jobs} finished job(s), {removed_files} payload file(s)"
        )
        return 0
    finally:
        engine.dispose()


def _run_watch(conn: Connection, watchlist_path: Path, *, now: datetime) -> int:
    try:
        directives = read_watchlist(watchlist_path)
    except WatchlistError as error:
        print(str(error))
        return 2
    if not directives:
        print(f"nothing to watch: {watchlist_path} holds no directives")
        return 2

    capped = 0
    queued = 0
    for directive in directives:
        # A job carries exactly one follow_up_kind, so a directive naming several is several listing
        # jobs -- same rule the archived cli._watch_pass used. `refresh` (index == 0) is written for
        # shape parity with the archived Job.refresh column but read by nothing here: since the #8
        # 수정 라운드 2 freshness cache, whether a listing actually re-fetches is decided entirely by
        # `_fresh_artifact`'s `fresh_until > now`, not by which enqueue set this flag.
        for index, follow_up in enumerate(directive.follow_ups or (None,)):
            outcome = queue.enqueue(
                conn,
                kind=directive.kind,
                target=directive.target,
                follow_up_kind=follow_up,
                refresh=index == 0,
                now=now,
            )
            if outcome is queue.EnqueueOutcome.ENQUEUED:
                queued += 1
            elif outcome is queue.EnqueueOutcome.CAPPED:
                capped += 1
                print(
                    f"{watchlist_path} line {directive.line}: queue is at MAX_QUEUE_DEPTH, "
                    f"skipping {directive.kind} {directive.target!r}"
                )
    if capped:
        return 1
    print(f"queued {queued} job(s) from {watchlist_path}")
    return 0


def _claim(conn: Connection, *, limit: int, now: datetime) -> Sequence[Any]:
    """One atomic statement, not select-then-update: two `work` passes overlapping (a slow cron tick
    still running when the next fires, or a live worker daemon started alongside the batch CLI --
    #10) raced select-then-update at READ COMMITTED and could both pick up the same queued row before
    either's UPDATE landed. `FOR UPDATE SKIP LOCKED` on the candidate CTE is the archived
    `JobRepository.claim`'s own fix for exactly this (worker.py), applied here as one UPDATE .. FROM
    a locked subquery so no other connection can see this batch as still queued in between.

    #101: stamps `started_at=now` in the same UPDATE -- this is the only place a job becomes RUNNING,
    so it is the only correct place to record when it started."""
    candidates = (
        sa.select(jobs.c.identifier)
        .where(jobs.c.state == JobState.QUEUED.value)
        .order_by(jobs.c.scheduled_at, jobs.c.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .cte("claim_candidates")
    )
    stmt = (
        sa.update(jobs)
        .where(jobs.c.identifier.in_(sa.select(candidates.c.identifier)))
        .values(state=JobState.RUNNING.value, started_at=now)
        .returning(jobs.c.identifier, jobs.c.kind, jobs.c.target, jobs.c.follow_up_kind, jobs.c.started_at)
    )
    return conn.execute(stmt).all()


def _fresh_artifact(conn: Connection, *, kind: str, target: str, now: datetime) -> Any | None:
    """The newest artifact for (kind, target) still inside its freshness window, or None -- the
    cache `_collect_one` consults before spending a request. `fresh_until` is stamped at write time
    (`now + FRESHNESS.get(kind)`), so this is one indexed comparison, not a per-row calculation."""
    return conn.execute(
        sa.select(artifacts.c.identifier, artifacts.c.digest, artifacts.c.byte_count)
        .where(artifacts.c.kind == kind, artifacts.c.target == target, artifacts.c.fresh_until > now)
        .order_by(artifacts.c.fetched_at.desc())
        .limit(1)
    ).first()


_QUOTA_EXCEEDED_REASON = "quotaExceeded"


def _is_quota_exceeded(error: Exception) -> bool:
    """YouTube Data API signals quota exhaustion as 403 with `reason: quotaExceeded` in the JSON
    body -- not as a distinct status code -- so telling it apart from a 403 that means something
    else (forbidden, accessNotConfigured) requires reading the body, not just `error.code`."""
    read = getattr(error, "read", None)
    if read is None:
        return False
    try:
        body = read()
    except Exception:  # noqa: BLE001 - a body we can't read just isn't quotaExceeded
        return False
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return False
    reasons = (item.get("reason") for item in payload.get("error", {}).get("errors") or [])
    return _QUOTA_EXCEEDED_REASON in reasons


def _classify_error(error: Exception) -> str:
    """Bucket a fetch failure into a small vocabulary #77's collector_health view can count as
    `blocked` (403/429, the same statuses commerce's fetch_log.status already treats as blocked) vs
    `failed` -- contracts/entrypoints.md 수집기 절 documents this vocabulary as the source #77 reads.
    `error.code` is how `urllib.error.HTTPError` (and any transport built the same shape) carries a
    status; a failure with none reached no HTTP response at all (DNS, socket, timeout)."""
    code = getattr(error, "code", None)
    if isinstance(code, int):
        if code == 403 and _is_quota_exceeded(error):
            return "quota"
        if code == 429:
            return "rate_limited"
        return f"http_{code}"
    return "transport"


def _normalize(kind: str, dump: dict[str, Any]) -> Any:
    if kind in LISTING_KINDS:
        return sources.normalize_listing(dump, source_kind=kind)
    if kind == "video.metadata":
        return sources.normalize_video_metadata(dump)
    if kind == "video.comments":
        return sources.normalize_comments(dump)
    if kind == "video.transcript":
        return sources.parse_json3_transcript(
            dump, language=dump.get("language", ""), is_automatic=bool(dump.get("is_automatic"))
        )
    raise ValueError(f"no source for job kind {kind!r}")


def _elapsed_ms(started_at: datetime, now: datetime) -> int:
    """#101 결정: elapsed_ms is the whole job's wall time (claim to finish), not just the fetch
    round trip -- unlike commerce's fetch_log.elapsed_ms, a youtube job's cache hit spends no fetch
    at all, so a fetch-only measure would leave every cache-hit row NULL. Both timestamps come from
    the same injected `now`/`started_at` clock the rest of this module already uses, so this value
    and `finished_at - started_at` computed later from the same columns can never disagree."""
    return int((now - started_at).total_seconds() * 1000)


def _collect_one(
    conn: Connection, payloads: PayloadStore, fetcher: Fetcher, job: Any, *, now: datetime
) -> bool:
    cached = _fresh_artifact(conn, kind=job.kind, target=job.target, now=now)
    if cached is not None:
        # A fresh artifact already answers this question -- no fetch, no new artifact row. This is
        # what keeps a directive naming 3 follow-up kinds from re-walking the same listing 3 times in
        # one watch pass (#8 수정 라운드 2 report): jobs 2 and 3 land here and reuse job 1's artifact.
        payload = payloads.get(job.kind, cached.digest)
        digest, byte_count = cached.digest, cached.byte_count
    else:
        try:
            dump = fetcher.fetch(FetchSpec(kind=job.kind, target=job.target))
            payload = _normalize(job.kind, dump)
        except Exception as error:  # noqa: BLE001 - one job's failure must not stop the batch
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.identifier == job.identifier)
                .values(
                    state=JobState.FAILED.value,
                    finished_at=now,
                    elapsed_ms=_elapsed_ms(job.started_at, now),
                    error_code=_classify_error(error),
                    error_message=str(error),
                )
            )
            return False

        stored = payloads.put(job.kind, payload)
        digest, byte_count = stored.digest, stored.byte_count
        conn.execute(
            sa.insert(artifacts).values(
                identifier=uuid.uuid4().hex,
                kind=job.kind,
                target=job.target,
                fingerprint=f"{job.kind}:{job.target}",
                digest=digest,
                byte_count=byte_count,
                fetched_at=now,
                fresh_until=now + FRESHNESS.get(job.kind, timedelta(0)),
                schema_version="1",
            )
        )

    if job.follow_up_kind is not None and job.kind in LISTING_KINDS:
        video_ids = [v["video_id"] for v in payload["videos"]]
        queue.fan_out_follow_up(conn, video_ids=video_ids, follow_up_kind=job.follow_up_kind, now=now)

    conn.execute(
        sa.update(jobs)
        .where(jobs.c.identifier == job.identifier)
        .values(
            state=JobState.SUCCEEDED.value,
            finished_at=now,
            elapsed_ms=_elapsed_ms(job.started_at, now),
            payload_digest=digest,
            payload_bytes=byte_count,
        )
    )
    return True


def _run_work(conn: Connection, payloads: PayloadStore, fetcher: Fetcher, *, now: datetime) -> int:
    claimed = _claim(conn, limit=DEFAULT_WORK_BATCH, now=now)
    if not claimed:
        print("no queued jobs")
        return 0
    failures = sum(0 if _collect_one(conn, payloads, fetcher, job, now=now) else 1 for job in claimed)
    print(f"worked {len(claimed)} job(s), {failures} failed")
    return 1 if failures else 0


def _run_flatten(conn: Connection, payloads: PayloadStore, *, now: datetime) -> int:
    report = flatten.run(conn, payloads, now=now)
    print(f"flattened {report.flattened} artifact(s), {report.errors} error(s)")
    return 1 if report.errors else 0


@dataclass
class PruneResult:
    removed_artifacts: int
    removed_jobs: int
    orphaned: list[tuple[str, str]]


def _run_prune(conn: Connection, *, now: datetime) -> PruneResult:
    # `payloads.put(job.kind, payload)` (see _collect_one) means an `artifacts` row and a `jobs` row
    # can name the *same* file by the same (kind, digest) pair -- a cached artifact is reused across
    # several jobs' payload_digest. So "still referenced" has to check both tables, not just the one
    # whose row this prune pass is deleting.
    cutoff = now - timedelta(days=PRUNE_MAX_AGE_DAYS)
    stale_artifacts = conn.execute(
        sa.select(artifacts.c.kind, artifacts.c.digest).where(artifacts.c.fetched_at < cutoff)
    ).all()
    stale_jobs = conn.execute(
        sa.select(jobs.c.kind, jobs.c.payload_digest).where(
            jobs.c.state.in_(FINISHED_STATES),
            jobs.c.finished_at < cutoff,
            jobs.c.payload_digest.is_not(None),
        )
    ).all()
    candidates = {(row.kind, row.digest) for row in stale_artifacts} | {
        (row.kind, row.payload_digest) for row in stale_jobs
    }

    removed_artifacts = conn.execute(sa.delete(artifacts).where(artifacts.c.fetched_at < cutoff)).rowcount
    removed_jobs = conn.execute(
        sa.delete(jobs).where(jobs.c.state.in_(FINISHED_STATES), jobs.c.finished_at < cutoff)
    ).rowcount

    orphaned = [(kind, digest) for kind, digest in candidates if not _still_referenced(conn, kind, digest)]
    return PruneResult(removed_artifacts=removed_artifacts, removed_jobs=removed_jobs, orphaned=orphaned)


def _still_referenced(conn: Connection, kind: str, digest: str) -> bool:
    """Same transaction as the deletes above, so this sees post-delete state -- a survivor row (a
    different job/artifact that happens to name the same payload) is exactly what must keep the file."""
    in_artifacts = conn.execute(
        sa.select(sa.literal(1)).where(artifacts.c.kind == kind, artifacts.c.digest == digest).limit(1)
    ).first()
    if in_artifacts is not None:
        return True
    in_jobs = conn.execute(
        sa.select(sa.literal(1)).where(jobs.c.kind == kind, jobs.c.payload_digest == digest).limit(1)
    ).first()
    return in_jobs is not None


__all__ = ["run", "Fetcher", "FetchSpec"]
