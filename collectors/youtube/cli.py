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
from sqlalchemy import Connection, Engine

from collectors.youtube import flatten, queue, quota, roster, sources, transport
from collectors.youtube.models import (
    COMMENT_REFETCH_WINDOW_DAYS,
    FRESHNESS,
    VIDEO_METADATA_KIND,
    Dataset,
    JobState,
)
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage import db as storage_db
from collectors.youtube.storage.tables import artifacts, collector_runs, comments, jobs
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

#: Rows per table in one prune batch, and one batch is one transaction (#279).
#:
#: 500 is `flatten.DEFAULT_BATCH_SIZE`, and for the same reason: it is the size at which the work in
#: one transaction is measured in hundreds of milliseconds. A batch costs two statements -- the paired
#: delete, and one anti-join that hashes ~42k artifacts and ~335k jobs once -- so the number that
#: matters is not how many rows it deletes but how long the transaction holds their locks. At 500 the
#: transaction is an order of magnitude inside `statement_timeout=15s` and two inside
#: `transaction_timeout=60s`, which is where the old whole-backlog transaction died.
PRUNE_BATCH_SIZE = 500

#: How many batches one pass may run before it leaves the rest to tomorrow's pass (#279).
#:
#: 40 x 500 is 20,000 artifacts and 20,000 finished jobs a night. Production's first real backlog is
#: 5,457 expired artifacts, so tonight drains in one pass with room to spare; the cap is for the
#: pathological case (a backfill multiplying the payload volume), where an uncapped pass would run
#: for hours and still be running when the next cron tick arrives. Nothing is lost by stopping: the
#: rows and their files are still there, and the next pass starts where this one left off.
PRUNE_MAX_BATCHES = 40

#: How long a claim may sit `running` before the next pass takes it back (#277). Since the claim is
#: now committed before the first fetch, a worker killed outright leaves its jobs `running` and
#: nothing else was coming for them.
#:
#: Two hours, and the two ways of being wrong are not symmetric. Too short reclaims a batch a live
#: worker still holds: the pass would be fetched twice, which costs requests and writes a second
#: artifact row (legal since #274) but loses nothing. Too long only delays a dead worker's jobs. So
#: the number sits clear of any pass that is merely slow -- a full batch on the slowest route is
#: 50 x (60s timeout + 0.75s pause) = 51 minutes, and yt-dlp's comment extraction pages inside one
#: request with no wall-clock bound at all -- rather than as close to a live pass as it can get.
STALE_CLAIM_AFTER = timedelta(hours=2)


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
        if wanted is Dataset.WORK:
            assert fetcher is not None  # noqa: S101 - set just above when the caller passed none
            # The engine, not a connection: #277: `work` is the one dataset that goes on the
            # network, and it opens a short transaction per step instead of holding one across the
            # batch. The role kills a transaction at 60s and an idle-in-transaction session at 30s,
            # and a connection is idle in transaction for exactly as long as a fetch takes.
            return _run_work(engine, payloads, fetcher, now=now)
        if wanted is Dataset.PRUNE:
            # The engine, not a connection: #279, and the same reasoning as `work` above. One
            # transaction per batch keeps every one of them far inside `transaction_timeout=60s`,
            # where a single transaction over the whole backlog crossed it and rolled the pass back.
            return _run_prune(engine, payloads, now=now)
        if wanted is Dataset.FLATTEN:
            # The engine, not a connection (#280). The pass itself is still exactly one transaction
            # across one batch -- that shape is unchanged and measured in
            # tests/collectors/youtube/test_youtube_flatten_transaction.py -- but the run row is
            # written in a transaction of its own *after* it, which is the only way a pass the
            # server killed can still leave a row saying so.
            return _run_flatten(engine, payloads, now=now)
        with engine.begin() as conn:
            return _run_watch(
                conn,
                watchlist_path or DEFAULT_WATCHLIST,
                now=now,
                roster_url=roster_url,
                read_roster=read_roster,
            )
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
    (`now + FRESHNESS.get(kind)`), so this is one indexed comparison, not a per-row calculation.

    #274: several rows for one (kind, target) can be fresh at once, because a row whose payload file
    cannot be read is re-fetched and the new row lands beside it rather than replacing it. Newest
    `fetched_at` first is what lets the readable row win from the next pass on; `identifier` breaks a
    tie so two jobs for the same target inside one pass (one `now`) read the same row rather than
    whichever the planner happened to hand back."""
    return conn.execute(
        sa.select(
            artifacts.c.identifier,
            artifacts.c.digest,
            artifacts.c.byte_count,
            artifacts.c.fetch_route,
        )
        .where(artifacts.c.kind == kind, artifacts.c.target == target, artifacts.c.fresh_until > now)
        .order_by(artifacts.c.fetched_at.desc(), artifacts.c.identifier.desc())
        .limit(1)
    ).first()


#: Work 3: "a block (403/429) is exit 2". These are exactly the four `error_code` values
#: `db/views/collector_health.sql` counts as `blocked` for the youtube arm -- kept identical on
#: purpose, so the exit code and the view can never call the same run two different things (the
#: mismatch contracts/entrypoints.md already records for NAVER's 401, #182 M2).
BLOCKED_CODES = frozenset({"quota", "rate_limited", "http_403", "http_429"})

#: #274: the code for a job that died of something that is not the source's answer -- a stored
#: payload that could not be written, a normalizer meeting a shape nobody has seen. Not `transport`,
#: which contracts/entrypoints.md defines as a failure with no HTTP status *on the way to the
#: source*; and `failed`, never `blocked`, because nothing refused us.
UNEXPECTED_CODE = "internal"

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


#: What `_cached_payload` returns instead of a payload. A sentinel rather than None, because None is
#: a value a stored payload could in principle hold and "no answer" must not be one of them.
_MISS = object()


def _cached_payload(payloads: PayloadStore, job: Any, cached: Any) -> Any:
    """The fresh artifact's stored payload, or `_MISS` when the file behind the row cannot be read.

    #274: an artifact row whose file is not in the store is real -- production holds 3,707 of them,
    written by a fleet whose volume did not come with them. Read outside a try this raised out of
    the whole batch, rolled the transaction back and left every claimed job `queued`, so the next
    five-minute tick met the same row and died the same way. A row we cannot read answers nothing,
    which is a cache miss: the caller fetches, and the newer row it writes is the one
    `_fresh_artifact` hands back from the next pass on."""
    try:
        return payloads.get(job.kind, cached.digest)
    except (OSError, ValueError):
        # Kind and target, never the payload: this line exists to name which target re-fetched, and
        # a payload in a log is a harvest in a log.
        print(f"{job.kind} {job.target!r}: the fresh artifact's payload could not be read, re-fetching")
        return _MISS


def _fail(conn: Connection, job: Any, *, code: str, message: str, now: datetime) -> None:
    """This job `failed`, in whatever transaction the caller opened for it."""
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.identifier == job.identifier)
        .values(
            state=JobState.FAILED.value,
            finished_at=now,
            elapsed_ms=_elapsed_ms(job.started_at, now),
            error_code=code,
            error_message=message,
        )
    )


def _collect_one(
    engine: Engine, payloads: PayloadStore, fetcher: Fetcher, job: Any, *, now: datetime
) -> _Collected:
    """One job in three phases, with no transaction open across the network (#277).

      read   the freshness cache, one short transaction that ends before anything else happens.
      fetch  the stored payload and, on a miss, the source -- the part that can take a minute, and
             the part that must find the connection idle *outside* a transaction rather than in one.
      write  the artifact row, the fan-out and the job's state, in one short transaction: this
             job's whole outcome, so a failure anywhere in it leaves none of it behind.

    That last transaction is what #274's savepoint used to be. A savepoint could only ever isolate a
    job from its neighbours inside the one transaction the server was already killing."""
    with engine.begin() as conn:
        cached = _fresh_artifact(conn, kind=job.kind, target=job.target, now=now)
    payload: Any = _cached_payload(payloads, job, cached) if cached is not None else _MISS
    short: str | None = None
    route: str | None = None
    request_count: int | None = None
    # A fresh artifact already answers this question -- no fetch, no new artifact row. This is what
    # keeps a directive naming 3 follow-up kinds from re-walking the same listing 3 times in one
    # watch pass (#8 fix round 2 report): jobs 2 and 3 land here and reuse job 1's artifact.
    if cached is not None and payload is not _MISS:
        hit = True
        # No new artifact row, so no route to write: the cached row already carries the one that
        # fetched it, and a cache hit is not a second observation of the source.
        digest, byte_count = cached.digest, cached.byte_count
    else:
        hit = False
        try:
            dump = fetcher.fetch(FetchSpec(kind=job.kind, target=job.target))
            payload = _normalize(job.kind, dump)
            route = dump.get("fetch_route")
            request_count = dump.get("request_count")
            short = _shortfall(dump) if job.kind in LISTING_KINDS else None
        except Exception as error:  # noqa: BLE001 - one job's failure must not stop the batch
            code = _classify_error(error)
            with engine.begin() as conn:
                _fail(conn, job, code=code, message=str(error), now=now)
            return _Collected(ok=False, blocked=code in BLOCKED_CODES)

        # Outside the write transaction on purpose: a file write cannot roll back, and holding the
        # transaction open over it would put our own disk back inside the timeouts this fix is about.
        stored = payloads.put(job.kind, payload)
        digest, byte_count = stored.digest, stored.byte_count

    with engine.begin() as conn:
        if not hit:
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
                    # #183: which source answered. Written here, from what the transport reported,
                    # and not guessed from the kind -- `video.metadata` takes either route.
                    fetch_route=route,
                    # #272: what that fetch spent, when the route counted it. NULL where it did not,
                    # which `quota.py` reads as "never asked" and falls back on -- writing 0 would
                    # tell the day's bound this fetch sent no request at all.
                    request_count=request_count,
                )
            )

        if job.follow_up_kind is not None and job.kind in LISTING_KINDS:
            videos = payload["videos"]
            if job.follow_up_kind == "video.comments":
                video_ids = _comment_follow_ups(conn, videos, now=now)
            else:
                video_ids = [video["video_id"] for video in videos]
            # #102: the fanned-out job inherits the listing job's own dataset -- it exists only
            # because that job's follow_up_kind named it, not because of anything `work` decided.
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


def _collect_one_guarded(
    engine: Engine, payloads: PayloadStore, fetcher: Fetcher, job: Any, *, now: datetime
) -> _Collected:
    """`_collect_one`, with the batch held apart from any one job's death (#274).

    The fetch already fails one job at a time; everything around it did not, and a `FileNotFoundError`
    from the payload store took down a whole `work` tick. Whatever is raised here ends this job
    `failed` with a code and the loop goes on to the next.

    #277: the `failed` row is written in a transaction of its own. A DBAPI error aborts the
    transaction it happened in, so the UPDATE recording the failure has to be somewhere else --
    which used to mean rolling back to a savepoint first and is now simply the next transaction. The
    half of this job that had already landed (a fanned-out follow-up, a payload's artifact row) went
    back with the write transaction that raised, rather than being left beside a job that says it
    failed."""
    try:
        return _collect_one(engine, payloads, fetcher, job, now=now)
    except Exception as error:  # noqa: BLE001 - no single job may take the batch down
        # A transport error can only have come from the fetch, which classifies its own; anything
        # else reached here because something on our side broke, and saying `transport` would put
        # that on the source.
        code = _classify_error(error) if isinstance(error, transport.TransportError) else UNEXPECTED_CODE
        with engine.begin() as conn:
            # The class name too: for an unexpected failure it is half the evidence, and
            # `str(KeyError('videos'))` alone is a bare quoted word on the row.
            _fail(conn, job, code=code, message=f"{type(error).__name__}: {error}", now=now)
        return _Collected(ok=False, blocked=code in BLOCKED_CODES)


def _bound_the_day(conn: Connection, fetcher: Fetcher, *, now: datetime) -> None:
    """Hand this run what is left of the Data API's day before it claims anything (#259).

    `max_requests_per_run` bounds one invocation and `work` is a cron line every five minutes, so
    nothing in a process outlives the bound it kept. The day's spend is read from `artifacts` --
    the row a fetch already writes -- and the run's Data API budget is lowered to what remains.
    A run that finds the day spent gets a budget of zero, so its Data API jobs end as
    `BudgetExhausted` (`error_code='budget'`), exactly as the per-run budget already ends them,
    while the yt-dlp and timedtext jobs in the same batch are untouched."""
    restrict = getattr(fetcher, "restrict_data_api", None)
    if not callable(restrict):
        # A fixture-backed fake spends no quota and needs no bound.
        return
    remaining = quota.remaining_requests(conn, now=now)
    if remaining < int(transport.ROUTES[transport.Route.DATA_API]["max_requests_per_run"]):
        # Only when the day is the binding number: printing it every run would bury the run that
        # actually stopped.
        print(
            f"data_api: {remaining} request(s) left of the day's "
            f"{quota.MAX_REQUESTS_PER_DAY} (since {quota.day_start(now).isoformat()})"
        )
    restrict(remaining)


def _fresh_targets(conn: Connection, *, kind: str, targets: Sequence[str], now: datetime) -> set[str]:
    """Which of these targets a fresh artifact already answers -- one set query, not one per job."""
    if not targets:
        return set()
    return set(
        conn.execute(
            sa.select(artifacts.c.target).where(
                artifacts.c.kind == kind,
                artifacts.c.target.in_(list(targets)),
                artifacts.c.fresh_until > now,
            )
        ).scalars()
    )


def _prefetch_specs(
    conn: Connection, fetcher: Fetcher, claimed: Sequence[Any], *, now: datetime
) -> list[FetchSpec]:
    """Which of this batch's `video.metadata` targets the batched call should carry (#259).

    The database half of the coalescing, and it is all of the database half: #277 splits it from
    `_prefetch` so this read rides in the claim's transaction and the call itself goes out with
    nothing open. One `videos.list` call takes up to 50 ids, and the port issued one call per video
    -- 13,979 requests for a panel pass instead of 280, which is 5.6x the Data API's whole daily
    quota. The jobs stay one per video (`queue.py`'s claim, dedupe and depth cap are per video,
    `payloads.put` stores one dump per job and `sources.normalize_video_metadata` reads one
    `dump["id"]`), so what changes is only where the request is made. A target already answered by a
    fresh artifact is left out: it spends no fetch, and including it would cost the batch a slot
    that a video which does need one could have had.

    Optional on the fetcher, the way `notes` is: a fixture-backed fake in the tests neither batches
    nor needs to."""
    if not callable(getattr(fetcher, "prefetch", None)):
        return []
    targets = [job.target for job in claimed if job.kind == VIDEO_METADATA_KIND]
    fresh = _fresh_targets(conn, kind=VIDEO_METADATA_KIND, targets=targets, now=now)
    return [
        FetchSpec(kind=VIDEO_METADATA_KIND, target=target)
        for target in dict.fromkeys(targets)
        if target not in fresh
    ]


def _prefetch(fetcher: Fetcher, specs: Sequence[FetchSpec]) -> None:
    """The network half of #259's coalescing, called with no transaction open (#277)."""
    prefetch = getattr(fetcher, "prefetch", None)
    if specs and callable(prefetch):
        prefetch(list(specs))


def _reclaim_stale(conn: Connection, *, now: datetime) -> int:
    """Claims older than `STALE_CLAIM_AFTER` go back to the queue (#277).

    The claim commits before the first fetch now, so a worker killed outright -- a container
    stopped, the machine gone -- leaves its jobs `running` with nobody coming for them. `_claim` is
    the only writer of RUNNING and it stamps `started_at` in the same statement, so the age of the
    claim is on the row and no lease column is needed."""
    return conn.execute(
        sa.update(jobs)
        .where(jobs.c.state == JobState.RUNNING.value, jobs.c.started_at < now - STALE_CLAIM_AFTER)
        .values(state=JobState.QUEUED.value, started_at=None)
    ).rowcount


def _run_work(engine: Engine, payloads: PayloadStore, fetcher: Fetcher, *, now: datetime) -> int:
    """#277: one short transaction to claim, then one or two per job, and none of them open while a
    route is on the network. The role kills a transaction at 60s and an idle-in-transaction session
    at 30s, and a `work` pass that met a slow kind spent longer than either inside one."""
    with engine.begin() as conn:
        _bound_the_day(conn, fetcher, now=now)
        reclaimed = _reclaim_stale(conn, now=now)
        claimed = _claim(conn, limit=DEFAULT_WORK_BATCH, now=now)
        # Read here, sent below: the read belongs to the claim's transaction and the call does not.
        specs = _prefetch_specs(conn, fetcher, claimed, now=now)
    if reclaimed:
        print(f"reclaimed {reclaimed} job(s) whose claim was older than {STALE_CLAIM_AFTER}")
    if not claimed:
        print("no queued jobs")
        return 0

    # What this process holds and has not finished. A claim is committed, so handing it back is this
    # process's own job -- on the way out of a block, and on the way out of anything else.
    unfinished = {row.identifier for row in claimed}
    outcomes: list[_Collected] = []
    blocked = False
    try:
        _prefetch(fetcher, specs)
        for job in claimed:
            outcome = _collect_one_guarded(engine, payloads, fetcher, job, now=now)
            unfinished.discard(job.identifier)
            outcomes.append(outcome)
            if outcome.blocked:
                # Work 3: a block is the whole source refusing us, so every later request in this
                # batch would be refused the same way and would only deepen it. The jobs already
                # claimed but not yet attempted go back to QUEUED below.
                blocked = True
                break
    finally:
        # Only when there is something to hand back: an empty transaction here would be a round trip
        # every pass, and on the way out of a failure it is one more thing that can raise over the
        # exception that caused it.
        if unfinished:
            with engine.begin() as conn:
                _requeue(conn, sorted(unfinished))

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
    """#277: only a job still RUNNING is this process's to hand back. Two `work` passes can overlap
    by design, so by the time this runs a job may already have been reclaimed and finished by
    another -- and writing `queued` over that would undo someone else's work."""
    if not identifiers:
        return
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.identifier.in_(identifiers), jobs.c.state == JobState.RUNNING.value)
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


#: How much of a pass's own line the run row keeps. The line is built here, so nothing longer than
#: a sentence can reach it; the cap is for the failure note, which carries a driver's message.
RUN_NOTE_LIMIT = 500


def _record_run(
    engine: Engine,
    *,
    dataset: str,
    status: str,
    attempted: int,
    succeeded: int,
    failed: int,
    skipped: int,
    note: str,
    now: datetime,
) -> None:
    """One row per pass, in a transaction of its own (#280).

    Its own transaction because the pass's transaction may be the thing that died: a `flatten` batch
    the server ended leaves an aborted transaction in which no row can be written, and a run row
    that could only be written by a healthy pass would say nothing on exactly the night it matters.

    `started_at` and `finished_at` are both the pass's injected clock, the same `now` every other
    row this collector writes in a pass is stamped with (`_elapsed_ms` records that choice for a
    job). So the wall clock of a pass is not measured here; what the health views ask of this row is
    when it ran and how it ended, and both come from that one reading.
    """
    with engine.begin() as conn:
        conn.execute(
            sa.insert(collector_runs).values(
                identifier=uuid.uuid4().hex,
                dataset=dataset,
                status=status,
                started_at=now,
                finished_at=now,
                attempted=attempted,
                succeeded=succeeded,
                failed=failed,
                skipped=skipped,
                note=note[:RUN_NOTE_LIMIT] or None,
            )
        )


def _pass_failed_note(error: Exception) -> str:
    """One line about a pass that could not finish, with no SQL and no payload in it -- the same
    rule flatten's `_reason` follows: `str()` of a SQLAlchemy DBAPIError appends the statement and
    its bound parameters."""
    original = getattr(error, "orig", None)
    text = str(original if original is not None else error).strip()
    first_line = text.splitlines()[0] if text else ""
    return f"{type(error).__name__}: {first_line}"


def _run_flatten(engine: Engine, payloads: PayloadStore, *, now: datetime) -> int:
    try:
        with engine.begin() as conn:
            report = flatten.run(conn, payloads, now=now)
    except Exception as error:  # noqa: BLE001 - a pass that died must still say that it died
        # Without this row the stage simply goes quiet, which `pipeline_health` reads as `never` or
        # as an old success -- the one answer a health view must not give (#280).
        note = f"the pass could not finish: {_pass_failed_note(error)}"
        print(f"flatten: {note}")
        _record_run(
            engine,
            dataset=Dataset.FLATTEN.value,
            status="failed",
            attempted=0,
            succeeded=0,
            failed=0,
            skipped=0,
            note=note,
            now=now,
        )
        return 1

    # The two kinds of error apart (#275): an artifact skipped for good and one the next pass tries
    # again both used to print the same number, and the cron log was the only place either appeared.
    note = (
        f"flattened {report.flattened} artifact(s), {report.errors} error(s) "
        f"({report.skipped} recorded unflattenable, {report.deferred} to retry)"
    )
    print(note)
    # N1 of #275's review: the failure records in `jobs` are a count with no denominator -- one
    # failure in five hundred read as 0 percent ok. `attempted` is that denominator.
    _record_run(
        engine,
        dataset=Dataset.FLATTEN.value,
        status=_flatten_status(report),
        attempted=report.flattened + report.errors,
        succeeded=report.flattened,
        failed=report.deferred,
        skipped=report.skipped,
        note=note,
        now=now,
    )
    return 1 if report.errors else 0


def _flatten_status(report: flatten.FlattenReport) -> str:
    """A pass that examined nothing still ran, and reads `ok`: the stage's question is whether the
    cron line fired, and an empty backlog is the healthy answer to it every fifteen minutes."""
    if not report.errors:
        return "ok"
    return "partial" if report.flattened else "failed"


@dataclass(frozen=True, slots=True)
class PruneBatch:
    removed_artifacts: int
    removed_jobs: int
    orphaned: list[tuple[str, str]]


#: Why a pass stopped short of draining the backlog. Printed and kept on the run row, because the
#: cron log was the only place it appeared before #280.
#:
#: A `prune` pass counts its units of work in batches: #279 made the pass's cost a property of the
#: number of batches rather than of the candidates in one, so that is what the run row and
#: `collector_health`'s `requests` carry -- the rows removed go in the row's `note` instead.
_CAP_NOTE = "the per-pass cap of {cap} batches stopped this pass; the next one continues"


def _prune_status(*, succeeded: int, failed: int, capped: bool) -> str:
    """`partial` where the pass did some of its work and left the rest: a hit cap, or a batch that
    could not finish beside batches that could. `failed` only where not one batch landed -- that is
    the night `pipeline_health` has to stop counting as "it ran" (#154's line), and a capped pass is
    not it."""
    if failed and not succeeded:
        return "failed"
    return "partial" if (failed or capped) else "ok"


def _run_prune(engine: Engine, payloads: PayloadStore, *, now: datetime) -> int:
    cutoff = now - timedelta(days=PRUNE_MAX_AGE_DAYS)
    removed_artifacts = removed_jobs = removed_files = 0
    succeeded = failed = 0
    drained = False
    for _ in range(PRUNE_MAX_BATCHES):
        try:
            with engine.begin() as conn:
                batch = _prune_one_batch(conn, cutoff=cutoff)
            # The batch's transaction just committed (the `with` block exited); only now is it safe
            # to unlink -- a file delete can't roll back, so doing it first would strand rows over an
            # orphan file on any later failure in the same transaction.
            removed_files += sum(payloads.delete(kind, digest) for kind, digest in batch.orphaned)
        except Exception as error:  # noqa: BLE001 - the pass reports what it did, never a traceback
            # One batch at a time is exactly what #279 bought, so a batch that cannot finish costs
            # this batch and not the pass -- but it is not retried either: a lock this pass lost or a
            # statement the server cancelled would meet the same candidates again, and spending the
            # remaining batches on it only delays the report.
            failed += 1
            print(f"prune: a batch could not finish: {_pass_failed_note(error)}")
            break
        succeeded += 1
        removed_artifacts += batch.removed_artifacts
        removed_jobs += batch.removed_jobs
        if not batch.removed_artifacts and not batch.removed_jobs:
            drained = True
            break
    note = (
        f"pruned {removed_artifacts} artifact(s), {removed_jobs} finished job(s), "
        f"{removed_files} payload file(s)"
    )
    print(note)
    capped = not drained and not failed
    if capped:
        cap_note = _CAP_NOTE.format(cap=PRUNE_MAX_BATCHES)
        print(cap_note)
        note = f"{note}; {cap_note}"
    status = _prune_status(succeeded=succeeded, failed=failed, capped=capped)
    # #280: without this row a prune that dies every night and one that succeeds every night read
    # the same on the ops screen -- the cap's line above lived in the cron log alone, and the pass
    # exited 0 either way.
    _record_run(
        engine,
        dataset=Dataset.PRUNE.value,
        status=status,
        attempted=succeeded + failed,
        succeeded=succeeded,
        failed=failed,
        skipped=0,
        note=note,
        now=now,
    )
    # contracts/entrypoints.md's exit codes: 1 is "some failed or were truncated", which is both a
    # capped pass and one whose batch could not finish. 2 is reserved for a refusal, and nothing
    # refuses a prune -- it goes nowhere near a source.
    return 0 if status == "ok" else 1


def _prune_one_batch(conn: Connection, *, cutoff: datetime) -> PruneBatch:
    """Two statements, whatever the batch holds. The pair is what makes the pass's cost a property of
    the number of batches rather than of the candidate count: the deletes name their own candidates
    back, and one anti-join then asks the whole set at once which of them nothing names any more."""
    deleted = conn.execute(_delete_a_batch(cutoff)).all()
    removed_artifacts = sum(1 for row in deleted if row.from_artifacts)
    candidates = sorted({(row.kind, row.digest) for row in deleted if row.digest is not None})
    orphaned: list[tuple[str, str]] = []
    if candidates:
        orphaned = [(row.kind, row.digest) for row in conn.execute(_orphans_among(candidates)).all()]
    return PruneBatch(
        removed_artifacts=removed_artifacts,
        removed_jobs=len(deleted) - removed_artifacts,
        orphaned=orphaned,
    )


def _delete_a_batch(cutoff: datetime) -> sa.CompoundSelect:
    """One statement: both deletes, capped at PRUNE_BATCH_SIZE rows each, handing back the (kind,
    digest) pairs they removed. RETURNING is what replaces the two full scans the old pass ran up
    front to collect its candidates."""
    doomed_artifacts = (
        sa.delete(artifacts)
        .where(
            artifacts.c.identifier.in_(
                sa.select(artifacts.c.identifier)
                .where(artifacts.c.fetched_at < cutoff)
                .limit(PRUNE_BATCH_SIZE)
            )
        )
        .returning(artifacts.c.kind, artifacts.c.digest)
        .cte("doomed_artifacts")
    )
    doomed_jobs = (
        sa.delete(jobs)
        .where(
            jobs.c.identifier.in_(
                sa.select(jobs.c.identifier)
                .where(jobs.c.state.in_(FINISHED_STATES), jobs.c.finished_at < cutoff)
                .limit(PRUNE_BATCH_SIZE)
            )
        )
        .returning(jobs.c.kind, jobs.c.payload_digest)
        .cte("doomed_jobs")
    )
    return sa.union_all(
        sa.select(sa.true().label("from_artifacts"), doomed_artifacts.c.kind, doomed_artifacts.c.digest),
        sa.select(sa.false(), doomed_jobs.c.kind, doomed_jobs.c.payload_digest),
    )


def _orphans_among(candidates: list[tuple[str, str]]) -> sa.Select[tuple[str, str]]:
    """The batch's pairs that no surviving row names any more.

    `payloads.put(job.kind, payload)` (see _collect_one) means an `artifacts` row and a `jobs` row can
    name the *same* file by the same (kind, digest) pair -- a cached artifact is reused across several
    jobs' payload_digest -- so both tables are asked, on both columns. The pair is the unit and always
    has been: one body stored under two kinds is one digest and two files, and only the kind whose
    last row just went away loses its file.

    A separate statement rather than another CTE beside the deletes: every part of one statement reads
    the same snapshot, so an anti-join sitting next to the deletes would still see the rows they are
    removing. Here it runs after them, in the same transaction, and sees what actually survives.
    """
    candidate = sa.values(
        sa.column("kind", sa.String), sa.column("digest", sa.String), name="candidate"
    ).data(candidates)
    return sa.select(candidate.c.kind, candidate.c.digest).where(
        ~sa.select(sa.literal(1))
        .where(artifacts.c.kind == candidate.c.kind, artifacts.c.digest == candidate.c.digest)
        .exists()
        .correlate(candidate),
        ~sa.select(sa.literal(1))
        .where(jobs.c.kind == candidate.c.kind, jobs.c.payload_digest == candidate.c.digest)
        .exists()
        .correlate(candidate),
    )


__all__ = ["run", "Fetcher", "FetchSpec"]
