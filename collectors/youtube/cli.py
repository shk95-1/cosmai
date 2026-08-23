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

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy import Connection

from collectors.youtube import flatten, queue, sources
from collectors.youtube.models import Dataset, JobState
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
        with engine.begin() as conn:
            if wanted is Dataset.WATCH:
                return _run_watch(conn, watchlist_path or DEFAULT_WATCHLIST, now=now)
            if wanted is Dataset.WORK:
                return _run_work(conn, payloads, fetcher or _RaisingFetcher(), now=now)
            if wanted is Dataset.FLATTEN:
                return _run_flatten(conn, payloads, now=now)
            return _run_prune(conn, now=now)
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
        # jobs -- same rule the archived cli._watch_pass used. Only the first is forced (refresh=True):
        # forcing every one would re-run the same enumeration once per follow-up kind.
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


def _claim(conn: Connection, *, limit: int) -> Sequence[Any]:
    rows = conn.execute(
        sa.select(jobs.c.identifier, jobs.c.kind, jobs.c.target, jobs.c.follow_up_kind)
        .where(jobs.c.state == JobState.QUEUED.value)
        .order_by(jobs.c.scheduled_at, jobs.c.created_at)
        .limit(limit)
    ).all()
    if rows:
        ids = [row.identifier for row in rows]
        conn.execute(sa.update(jobs).where(jobs.c.identifier.in_(ids)).values(state=JobState.RUNNING.value))
    return rows


def _collect_one(
    conn: Connection, payloads: PayloadStore, fetcher: Fetcher, job: Any, *, now: datetime
) -> bool:
    try:
        dump = fetcher.fetch(FetchSpec(kind=job.kind, target=job.target))
        if job.kind in LISTING_KINDS:
            payload = sources.normalize_listing(dump, source_kind=job.kind)
        elif job.kind == "video.metadata":
            payload = sources.normalize_video_metadata(dump)
        elif job.kind == "video.comments":
            payload = sources.normalize_comments(dump)
        elif job.kind == "video.transcript":
            payload = sources.parse_json3_transcript(
                dump, language=dump.get("language", ""), is_automatic=bool(dump.get("is_automatic"))
            )
        else:
            raise ValueError(f"no source for job kind {job.kind!r}")
    except Exception as error:  # noqa: BLE001 - one job's failure must not stop the batch
        conn.execute(
            sa.update(jobs)
            .where(jobs.c.identifier == job.identifier)
            .values(
                state=JobState.FAILED.value,
                finished_at=now,
                error_code=type(error).__name__,
                error_message=str(error),
            )
        )
        return False

    stored = payloads.put(job.kind, payload)
    artifact_id = uuid.uuid4().hex
    conn.execute(
        sa.insert(artifacts).values(
            identifier=artifact_id,
            kind=job.kind,
            target=job.target,
            fingerprint=f"{job.kind}:{job.target}",
            digest=stored.digest,
            byte_count=stored.byte_count,
            fetched_at=now,
            fresh_until=now,
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
            payload_digest=stored.digest,
            payload_bytes=stored.byte_count,
        )
    )
    return True


def _run_work(conn: Connection, payloads: PayloadStore, fetcher: Fetcher, *, now: datetime) -> int:
    claimed = _claim(conn, limit=DEFAULT_WORK_BATCH)
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


def _run_prune(conn: Connection, *, now: datetime) -> int:
    cutoff = now - timedelta(days=PRUNE_MAX_AGE_DAYS)
    removed_artifacts = conn.execute(sa.delete(artifacts).where(artifacts.c.fetched_at < cutoff)).rowcount
    removed_jobs = conn.execute(
        sa.delete(jobs).where(jobs.c.state.in_(FINISHED_STATES), jobs.c.finished_at < cutoff)
    ).rowcount
    print(f"pruned {removed_artifacts} artifact(s), {removed_jobs} finished job(s)")
    return 0


__all__ = ["run", "Fetcher", "FetchSpec"]
