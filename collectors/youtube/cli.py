"""`cosmai collect youtube` -- wired for #8, live since #183. `fetcher` is the seam: the default is
now `collectors/youtube/transport.py`'s `LiveFetcher` (three routes -- the YouTube Data API, yt-dlp
and timedtext) where it used to be a `_RaisingFetcher`, and tests plug a fixture-backed fake into the
same seam, the shape collectors/commerce/cli.py uses for its `Fetcher`.

Four datasets, matching contracts/entrypoints.md's `youtube datasets: watch | work | flatten | prune`:
`watch` only enqueues (issue #8's fan-out cap lives entirely in `queue.py`, exercised here); `work`
claims queued jobs and collects them; `flatten` turns their artifacts into the snapshot tables; `prune`
ages artifacts and finished jobs out. Exit codes follow contracts/entrypoints.md's exit codes: 0 ok, 1
partial, 2 blocked.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy import Connection

from collectors.youtube import flatten, queue, roster, sources, transport
from collectors.youtube.models import (
    COMMENT_REFETCH_WINDOW_DAYS,
    FRESHNESS,
    Dataset,
    JobState,
)
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage import db as storage_db
from collectors.youtube.storage.tables import artifacts, comments, jobs
from collectors.youtube.watchlist import WatchlistError, read_watchlist
from db import runtime as needs_runtime
from db import secrets

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


#: contracts/secrets.md: the listing route's key. Since #183 it is no longer "trending only" -- the
#: panel channels are listed with it too.
DATA_API_SECRET_KEY = "YOUTUBE_DATA_API_TOKEN"


def live_fetcher(secrets_path: str | Path | None = None) -> transport.LiveFetcher:
    """The default `work` fetcher since #183 -- what replaced `_RaisingFetcher`.

    A missing key is not fatal here, unlike `collectors/naver/cli.py`'s `secrets.require`: two of the
    four job kinds (comments, transcripts) need no key at all, and a run that can still collect them
    is partial rather than blocked. `LiveFetcher` refuses the Data API routes by itself, naming the
    key, and those jobs fail one at a time the way any other failed job does."""
    found = secrets.load(secrets_path)
    key = found.get(DATA_API_SECRET_KEY)
    budget = transport.RequestBudget.from_scope()
    ytdlp = transport.YtdlpRoute(budget=budget)
    return transport.LiveFetcher(
        data_api=transport.DataApiClient(key, budget=budget) if key else None,
        ytdlp=ytdlp,
        timedtext=transport.TimedtextRoute(ytdlp=ytdlp, budget=budget),
        budget=budget,
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
    roster_url: str | None = None,
    read_roster: bool = True,
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
    if wanted is Dataset.WORK and fetcher is None:
        # Built before the engine so a missing secret file is reported without a connection open.
        fetcher = live_fetcher()
    engine = storage_db.create_engine(database_url or storage_db.runtime_url())
    payloads = PayloadStore(payload_root or Path("var") / "youtube-payloads")
    try:
        prune_result: PruneResult | None = None
        with engine.begin() as conn:
            if wanted is Dataset.WATCH:
                return _run_watch(
                    conn,
                    watchlist_path or DEFAULT_WATCHLIST,
                    now=now,
                    roster_url=roster_url,
                    read_roster=read_roster,
                )
            if wanted is Dataset.WORK:
                assert fetcher is not None  # noqa: S101 - set just above when the caller passed none
                return _run_work(conn, payloads, fetcher, now=now)
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


def _panel_directives(roster_url: str | None) -> list[Any]:
    """Read once, on its own engine, and let it go: the roster is 43 rows read at boot, and holding
    a second pool open for the rest of a watch pass would spend a `needs_runtime` connection the
    analysis stages are sized against."""
    engine = storage_db.create_engine(roster_url or needs_runtime.runtime_url())
    try:
        with engine.begin() as conn:
            return roster.read_panel_channels(conn)
    finally:
        engine.dispose()


def _run_watch(
    conn: Connection,
    watchlist_path: Path,
    *,
    now: datetime,
    roster_url: str | None = None,
    read_roster: bool = True,
) -> int:
    try:
        file_directives = read_watchlist(watchlist_path)
    except WatchlistError as error:
        print(str(error))
        return 2

    panel: list[Any] = []
    if read_roster:
        try:
            panel = _panel_directives(roster_url)
        except (roster.RosterError, SystemExit) as error:
            # Blocked, not partial: a `watch` that silently queues only the operator file because the
            # roster query failed is #39's "no queued jobs" with a new cause and the same silence.
            print(f"the panel roster could not be read: {error}")
            return 2
    directives = roster.merge(panel, file_directives)

    if not directives:
        print(f"nothing to watch: the panel roster is empty and {watchlist_path} holds no directives")
        return 2

    capped = 0
    queued = 0
    for directive in directives:
        # A job carries exactly one follow_up_kind, so a directive naming several is several listing
        # jobs -- same rule the archived cli._watch_pass used. `refresh` (index == 0) is written for
        # shape parity with the archived Job.refresh column but read by nothing here: since the #8
        # fix round 2 freshness cache, whether a listing actually re-fetches is decided entirely by
        # `_fresh_artifact`'s `fresh_until > now`, not by which enqueue set this flag.
        for index, follow_up in enumerate(directive.follow_ups or (None,)):
            outcome = queue.enqueue(
                conn,
                kind=directive.kind,
                target=directive.target,
                dataset=Dataset.WATCH.value,
                follow_up_kind=follow_up,
                refresh=index == 0,
                now=now,
            )
            if outcome is queue.EnqueueOutcome.ENQUEUED:
                queued += 1
            elif outcome is queue.EnqueueOutcome.CAPPED:
                capped += 1
                source = f"{watchlist_path} line {directive.line}" if directive.line else "panel roster"
                print(
                    f"{source}: queue is at MAX_QUEUE_DEPTH, skipping {directive.kind} {directive.target!r}"
                )
    if capped:
        return 1
    print(f"queued {queued} job(s) from {len(panel)} panel channel(s) and {watchlist_path}")
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
        .returning(
            jobs.c.identifier,
            jobs.c.kind,
            jobs.c.target,
            jobs.c.dataset,
            jobs.c.follow_up_kind,
            jobs.c.started_at,
        )
    )
    return conn.execute(stmt).all()


def _fresh_artifact(conn: Connection, *, kind: str, target: str, now: datetime) -> Any | None:
    """The newest artifact for (kind, target) still inside its freshness window, or None -- the
    cache `_collect_one` consults before spending a request. `fresh_until` is stamped at write time
    (`now + FRESHNESS.get(kind)`), so this is one indexed comparison, not a per-row calculation."""
    return conn.execute(
        sa.select(
            artifacts.c.identifier,
            artifacts.c.digest,
            artifacts.c.byte_count,
            artifacts.c.fetch_route,
        )
        .where(artifacts.c.kind == kind, artifacts.c.target == target, artifacts.c.fresh_until > now)
        .order_by(artifacts.c.fetched_at.desc())
        .limit(1)
    ).first()


#: Work 3: "a block (403/429) is exit 2". These are exactly the four `error_code` values
#: `db/views/collector_health.sql` counts as `blocked` for the youtube arm -- kept identical on
#: purpose, so the exit code and the view can never call the same run two different things (the
#: mismatch contracts/entrypoints.md already records for NAVER's 401, #182 M2).
BLOCKED_CODES = frozenset({"quota", "rate_limited", "http_403", "http_429"})

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


def _route_of(error: Exception) -> str:
    """Which source the failure came from. #183 made this a question with more than one answer: the
    Data API answers with a status and a JSON `reason`, yt-dlp answers with prose and no status at
    all, and timedtext answers with a status and no body worth reading. An error carrying no route
    is classified as the Data API's, which is what every pre-#183 caller of this module meant --
    `_is_quota_exceeded` was written against a Data API 403 body and nothing else could reach it."""
    route = getattr(error, "route", None)
    return route if isinstance(route, str) else transport.Route.DATA_API


def _classify_error(error: Exception) -> str:
    """Bucket a fetch failure into a small vocabulary #77's collector_health view can count as
    `blocked` (403/429, the same statuses commerce's fetch_log.status already treats as blocked) vs
    `failed` -- contracts/entrypoints.md 수집기 절 documents this vocabulary as the source #77 reads.

    #183: the route decides how the failure is read, because the same word means different things
    on different routes and one of the three has no status to read at all.

      data_api   `.code` plus, for a 403, the `reason` in the body -- quotaExceeded is a spent quota
                 and not a refusal, and the two are the same status.
      ytdlp      no status ever. The bot check is the one failure that is evidence about the address
                 rather than about the video, so it is `rate_limited`; a private or removed video is
                 `unavailable`; anything else is `transport`.
      timedtext  `.code`, and never a quota -- captions are not metered, so reading a 403 body for
                 `quotaExceeded` there would only ever find nothing.

    `unavailable` is new here and lands in `failed`, not `blocked`: a members-only video is a fact
    about that video, and counting it as blocked would make one channel's access policy look like
    the collector being refused."""
    route = _route_of(error)
    if isinstance(error, transport.BudgetExhausted):
        # The run stopped itself. Not the source's answer, and not a status -- calling it
        # `rate_limited` would put our own cap into collector_health's `blocked` count.
        return "budget"
    if route == transport.Route.YTDLP:
        if isinstance(error, transport.RateLimited):
            return "rate_limited"
        if isinstance(error, transport.Unavailable):
            return "unavailable"
        return "transport"
    if isinstance(error, transport.Unavailable):
        return "unavailable"
    code = getattr(error, "code", None)
    if isinstance(code, int):
        if code == 403 and route == transport.Route.DATA_API and _is_quota_exceeded(error):
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
    """#101 decision: elapsed_ms is the whole job's wall time (claim to finish), not just the fetch
    round trip -- unlike commerce's fetch_log.elapsed_ms, a youtube job's cache hit spends no fetch
    at all, so a fetch-only measure would leave every cache-hit row NULL. Both timestamps come from
    the same injected `now`/`started_at` clock the rest of this module already uses, so this value
    and `finished_at - started_at` computed later from the same columns can never disagree."""
    return int((now - started_at).total_seconds() * 1000)


@dataclass(frozen=True, slots=True)
class _Collected:
    """What one job produced. `short` is separate from `ok` on purpose: the job succeeded, an
    artifact was written and the rows are real -- the source simply handed back less than it said it
    held, which contracts/entrypoints.md calls partial ("some failed **or were truncated**") and
    which nothing before #183 could observe at all."""

    ok: bool
    short: bool = False
    blocked: bool = False


def _shortfall(dump: Mapping[str, Any]) -> str | None:
    """The #90 axis, read off what the transport reported rather than off the request.

    Two ways a listing ends early and only one of them is the source's doing. `truncated` is ours --
    the item cap or the publication window -- and is noted, not counted against the run. A walk that
    ended on its own holding fewer entries than the first page's `totalResults` claimed is the
    source giving us less than it said it had, and that is the failure that looks exactly like
    success: on #90 the fake answered every request plausibly, the suite was green, and production
    wrote 0 rows."""
    expected = dump.get("expected_count")
    returned = dump.get("returned_count")
    if not isinstance(expected, int) or not isinstance(returned, int):
        return None
    if dump.get("truncated"):
        return None
    if returned >= expected:
        return None
    return f"the source reported {expected} item(s) and returned {returned}"


def _comment_follow_ups(conn: Connection, videos: Sequence[Mapping[str, Any]], *, now: datetime) -> list[str]:
    """Which listed videos get a `video.comments` job this pass (#183's re-fetch window).

    A video published inside `COMMENT_REFETCH_WINDOW_DAYS` is fanned out every pass and
    `FRESHNESS_SECONDS['video.comments']` decides whether that job actually spends a fetch. A video
    older than the window is fanned out **once, ever** -- at first observation -- and never again.
    Without the window a daily cadence re-fetches every panel video's comments every day; against
    13,979 videos that is the quota shape the archive measured as 5,098 of 5,679 units, every day,
    forever.

    It is applied here, at the fan-out, rather than in `work`'s collection of a job or in the
    queue's dedupe: by either of those points the row exists, `MAX_QUEUE_DEPTH` has already counted
    it, and the fan-out is where the 224k-duplicate incident happened. (The decision comment places
    this "in `watch`"; in this package the listing is expanded during `work`, from the listing job's
    `follow_up_kind`, which is the same fan-out under a different dataset name.)

    "Ever" is asked of two tables because neither alone survives: `artifacts` is pruned at
    `PRUNE_MAX_AGE_DAYS`, so an artifact row would quietly stop being evidence after a month and the
    whole panel would re-fetch; `comments` is never pruned but is only written once `flatten` has
    run, so it is blind to a harvest collected an hour ago. Two set queries, not two per video.
    """
    cutoff = now - timedelta(days=COMMENT_REFETCH_WINDOW_DAYS)
    inside: list[str] = []
    outside: list[str] = []
    for video in videos:
        video_id = video.get("video_id")
        if not video_id:
            continue
        published = video.get("published_at")
        # No publication instant is not "new": it is unknown, and the cheap reading of unknown is
        # the one that collects the video once rather than every day forever.
        if isinstance(published, datetime) and published >= cutoff:
            inside.append(video_id)
        else:
            outside.append(video_id)
    if not outside:
        return inside

    already = set(
        conn.execute(
            sa.select(artifacts.c.target).where(
                artifacts.c.kind == "video.comments", artifacts.c.target.in_(outside)
            )
        ).scalars()
    )
    already.update(
        conn.execute(
            sa.select(comments.c.video_id).where(comments.c.video_id.in_(outside)).distinct()
        ).scalars()
    )
    return inside + [video_id for video_id in outside if video_id not in already]


def _collect_one(
    conn: Connection, payloads: PayloadStore, fetcher: Fetcher, job: Any, *, now: datetime
) -> _Collected:
    cached = _fresh_artifact(conn, kind=job.kind, target=job.target, now=now)
    short: str | None = None
    if cached is not None:
        # A fresh artifact already answers this question -- no fetch, no new artifact row. This is
        # what keeps a directive naming 3 follow-up kinds from re-walking the same listing 3 times in
        # one watch pass (#8 fix round 2 report): jobs 2 and 3 land here and reuse job 1's artifact.
        payload = payloads.get(job.kind, cached.digest)
        digest, byte_count = cached.digest, cached.byte_count
        # No new artifact row, so no route to write: the cached row already carries the one that
        # fetched it, and a cache hit is not a second observation of the source.
        route = None
    else:
        try:
            dump = fetcher.fetch(FetchSpec(kind=job.kind, target=job.target))
            payload = _normalize(job.kind, dump)
            route = dump.get("fetch_route")
            short = _shortfall(dump) if job.kind in LISTING_KINDS else None
        except Exception as error:  # noqa: BLE001 - one job's failure must not stop the batch
            code = _classify_error(error)
            conn.execute(
                sa.update(jobs)
                .where(jobs.c.identifier == job.identifier)
                .values(
                    state=JobState.FAILED.value,
                    finished_at=now,
                    elapsed_ms=_elapsed_ms(job.started_at, now),
                    error_code=code,
                    error_message=str(error),
                )
            )
            return _Collected(ok=False, blocked=code in BLOCKED_CODES)

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
                # #183: which source answered. Written here, from what the transport reported, and
                # not guessed from the kind -- `video.metadata` takes either route.
                fetch_route=route,
            )
        )

    if job.follow_up_kind is not None and job.kind in LISTING_KINDS:
        videos = payload["videos"]
        if job.follow_up_kind == "video.comments":
            video_ids = _comment_follow_ups(conn, videos, now=now)
        else:
            video_ids = [video["video_id"] for video in videos]
        # #102: the fanned-out job inherits the listing job's own dataset -- it exists only because
        # that job's follow_up_kind named it, not because of anything `work` itself decided.
        queue.fan_out_follow_up(
            conn, video_ids=video_ids, follow_up_kind=job.follow_up_kind, dataset=job.dataset, now=now
        )

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
    if short is not None:
        print(f"{job.kind} {job.target!r}: {short}")
        return _Collected(ok=True, short=True)
    return _Collected(ok=True)


def _run_work(conn: Connection, payloads: PayloadStore, fetcher: Fetcher, *, now: datetime) -> int:
    claimed = _claim(conn, limit=DEFAULT_WORK_BATCH, now=now)
    if not claimed:
        print("no queued jobs")
        return 0
    outcomes: list[_Collected] = []
    blocked = False
    for index, job in enumerate(claimed):
        outcome = _collect_one(conn, payloads, fetcher, job, now=now)
        outcomes.append(outcome)
        if outcome.blocked:
            # Work 3: a block is the whole source refusing us, so every later request in this batch
            # would be refused the same way and would only deepen it. The jobs already claimed but
            # not yet attempted go back to QUEUED -- leaving them RUNNING would strand them, since
            # nothing here reclaims a job whose worker stopped (the lease machinery is out of scope).
            blocked = True
            _requeue(conn, [row.identifier for row in claimed[index + 1 :]])
            break

    failures = sum(not outcome.ok for outcome in outcomes)
    short = sum(outcome.short for outcome in outcomes)
    print(f"worked {len(outcomes)} job(s), {failures} failed, {short} short")
    for note in _notes_of(fetcher):
        print(note)
    if blocked:
        print(f"blocked: {len(claimed) - len(outcomes)} claimed job(s) returned to the queue")
        return 2
    # A short listing is partial by contracts/entrypoints.md's own words -- "1 partial (some failed
    # or were truncated)" -- and is the one failure that otherwise reports 0 and writes half a
    # channel (#90).
    return 1 if failures or short else 0


def _requeue(conn: Connection, identifiers: Sequence[str]) -> None:
    if not identifiers:
        return
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.identifier.in_(identifiers))
        .values(state=JobState.QUEUED.value, started_at=None)
    )


def _notes_of(fetcher: Fetcher) -> list[str]:
    """A transport may have something to say that is not an exception -- #183's owner-only
    `videos.list` part being refused, for instance, which leaves a `source_metadata` key null on
    every affected row and raises nothing at all. Optional, so a fixture-backed fake needs no such
    method."""
    notes = getattr(fetcher, "notes", None)
    if not callable(notes):
        return []
    produced = notes()
    return [str(note) for note in produced] if isinstance(produced, list) else []


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
