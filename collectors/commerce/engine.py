"""One collection, start to finish, and what its outcome means to cron.

origin: service/trend-radar/src/trend_radar/engine/{collect,lane,journal}.py -- ported for #7 as a
single synchronous module, its rate policy re-introduced for #10 §A-1. #7 kept the crawl frontier and
the exit-code semantics but left `SourcePolicy`'s numbers unread: nothing paced, nothing retried,
nothing capped what was in flight, and `user_agent` reached no request. A lane now walks one source
behind a `Gate` (collectors/commerce/gate.py) with `policy.concurrency` workers, retries up to
`policy.max_attempts`, and stamps `policy.user_agent` onto every request it hands the fetcher -- so a
live transport (#10) cannot be plugged in behind this without the policy applying to it.

The exit code is the part worth being deliberate about: this runs unattended, so the only thing most
runs will ever say is a number.

  0  every source collected, nothing refused, nothing truncated
  1  partial -- a source errored, hit its request budget, or was skipped because another run held it
  2  a source was refused outright
"""

from __future__ import annotations

import dataclasses
import queue
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from collectors.commerce.contract import Fetch, Payload, Source, SourcePolicy, narrowed
from collectors.commerce.gate import Gate
from collectors.commerce.models import Dataset, Record


class TransportError(Exception):
    """Base for every way a fetch can fail. Carries what the gate needs to react: `status` is the HTTP
    status where one was seen, `retry_after` the server's own answer to "how long"."""

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class PermanentError(TransportError):
    """A response the engine should not retry -- a 404, a malformed request."""


class RateLimited(TransportError):
    """Worth another attempt, slower. The gate widens before the next one goes out."""


class TransientError(TransportError):
    """A response worth retrying -- a timeout, a 5xx."""


class ChallengeBlocked(TransportError):
    """The site refused outright (a WAF challenge, a hard 403). Halts the source; exit code 2."""


@runtime_checkable
class Fetcher(Protocol):
    def fetch(self, fetch: Fetch) -> Payload: ...


class FetcherFor(Protocol):
    """Builds the fetcher one source gets, when a single one will not do.

    A live transport is a property of the source, not of the run: `SourcePolicy` carries the
    timeout and the user agent the requests go out with, and `SourcePolicy.transport` decides
    whether they go out over httpx or through a Chromium holding that source's own profile. So
    `collect` takes either one ready-made `Fetcher` -- what every test injects -- or this, which it
    calls once per source it actually walks. Once per source and not once per request: building it
    lazily is the transport's own job (collectors/commerce/transport/factory.py), and a source that
    is skipped or blocked never reaches here at all.
    """

    def __call__(self, source: Source) -> Fetcher: ...


LOCK_HELD_ELSEWHERE = (
    "skipped: another run holds this source's lock, and walking it anyway would send the site twice "
    "the rate its policy allows"
)


class SourceLock(Protocol):
    """Whatever decides that one source is this run's to walk right now.

    The context manager's value answers "did we get it": False means someone else is already walking
    this source and this run stands down from it. The lock is held for as long as the block runs, so
    an implementation must hold whatever it holds across a whole walk, not a single statement.
    """

    def __call__(self, source_key: str) -> AbstractContextManager[bool]: ...


@contextmanager
def uncoordinated(source_key: str) -> Iterator[bool]:
    """The default: every source is ours. One process walking on its own -- a test, a one-off walk --
    has nobody to coordinate with, so only the CLI (the thing cron runs twice) installs a real lock.
    """
    del source_key
    yield True


@dataclass
class SourceReport:
    key: str
    requests: int = 0
    records: int = 0
    retries: int = 0
    deduped: int = 0
    dropped_over_depth: int = 0
    budget_exhausted: bool = False
    blocked_reason: str | None = None
    # Not an error and not a refusal: this run yielded the source to another one that already had it.
    skipped_reason: str | None = None
    errors: list[str] = field(default_factory=list)
    final_interval_s: float | None = None
    final_concurrency: int | None = None
    configured_interval_s: float | None = None
    configured_concurrency: int | None = None
    request_budget: int | None = None
    scope: Mapping[str, Mapping[str, int]] | None = None

    @property
    def stopped_short(self) -> bool:
        """One definition of "did not walk everything the source asked it to", so the report, the
        outcome column and the exit code cannot each count a dropped request differently."""
        return self.budget_exhausted or self.dropped_over_depth > 0

    @property
    def ok(self) -> bool:
        return (
            self.blocked_reason is None
            and self.skipped_reason is None
            and not self.errors
            and not self.stopped_short
        )


@dataclass
class FetchAttempt:
    source: str
    dataset: Dataset
    url: str
    attempt: int
    status: int | None
    elapsed_ms: int | None
    bytes: int | None
    error: str | None


class Journal(Protocol):
    def record(self, attempt: FetchAttempt) -> None: ...


class NullJournal:
    def record(self, attempt: FetchAttempt) -> None:
        return None


class Sink(Protocol):
    """Where a lane's parsed records go.

    Two requirements, and the second is the one with teeth:

    1. `write` is called from several threads at once. A lane runs `policy.concurrency` workers over
       one sink -- and since #25 every source's lane runs at once over that same sink, so the thread
       count is the sum across lanes -- while `_Lane._work` calls this outside `_lock` on purpose:
       serialising a database write behind the lock that guards the queue would pay for pacing
       twice. SQLAlchemy documents
       `Connection` as not thread-safe, so an implementation must not close over one; at two workers
       and short statements psycopg's own lock hides that, which is exactly why it is written down
       here rather than left to be discovered.
    2. Each call commits on its own. Threads sharing one `Connection` share one transaction, so a
       rollback anywhere discards every batch written since -- including other workers' rows that the
       run has already counted. `tests/collectors/commerce/test_sink_takes_concurrent_writes.py`
       holds this one.

    `cli._EngineSink` satisfies both by taking a connection out of the pool per call.
    """

    def write(self, records: Sequence[Record]) -> None: ...


@dataclass
class RunReport:
    captured_at: datetime
    sources: dict[str, SourceReport]

    @property
    def blocked(self) -> list[str]:
        return sorted(k for k, r in self.sources.items() if r.blocked_reason is not None)

    @property
    def skipped(self) -> list[str]:
        """Sources this run stood down from. Partial, never blocked: the site refused nothing, and
        the next run picks them up -- every write here is a natural-key upsert."""
        return sorted(k for k, r in self.sources.items() if r.skipped_reason is not None)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.sources.values())


def exit_code_for(report: RunReport) -> int:
    if report.blocked:
        return 2
    if not report.ok:
        return 1
    return 0


_DONE = object()  # the sentinel that retires a worker once the frontier is drained


def _with_user_agent(fetch: Fetch, policy: SourcePolicy) -> Fetch:
    """Stamp the source's own name onto the request, unless the source already named itself.

    The UA is applied here rather than inside a transport so it cannot be forgotten: `Fetcher.fetch`
    receives one `Fetch`, and that `Fetch` carries the header. A transport plugged in for #10 sends
    `fetch.headers` or it is not sending the request it was handed.
    """
    if any(name.lower() == "user-agent" for name, _ in fetch.headers):
        return fetch
    return dataclasses.replace(fetch, headers=(*fetch.headers, ("User-Agent", policy.user_agent)))


class _Lane:
    """One source's crawl: a frontier, a gate, and however many workers the policy allows.

    origin: trend_radar/engine/lane.py, asyncio tasks swapped for threads. The frontier, the report
    and the halt flag are shared across those workers, so every mutation of them holds `_lock` while
    those workers are running -- `_finished` writes the report without it, after the last one is
    joined. The request budget is the one counter read and spent inside a single hold (`_charge`),
    because it is the only shared number a worker decides on rather than just adds to. Pacing and the
    in-flight ceiling are the gate's, under locks of its own that this class never takes.
    """

    def __init__(
        self,
        source: Source,
        fetcher: Fetcher,
        sink: Sink,
        dataset: Dataset,
        captured_at: datetime,
        journal: Journal,
        board: str | None,
        gate: Gate,
    ) -> None:
        self._source = source
        self._policy = source.policy
        self._fetcher = fetcher
        self._sink = sink
        self._dataset = dataset
        self._captured_at = captured_at
        self._journal = journal
        self._board = board
        self._gate = gate

        self._queue: queue.Queue[object] = queue.Queue()
        self._seen: set[Fetch] = set()
        self._report = SourceReport(key=source.key)
        self._lock = threading.Lock()
        self._stop = False
        self._crash: BaseException | None = None

    def run(self) -> SourceReport:
        if self._dataset not in self._source.datasets:
            self._report.scope = {}
            return self._report

        # Seeds enter through the frontier rather than straight onto the queue, so a source naming
        # the same URL twice spends one request on it -- and `deduped` now counts seed collisions
        # alongside follow-link ones.
        #
        # `board` is part of every source's `seeds` signature (contract.Source), but only means
        # anything to a REVIEW_LOW walk (#7) -- every other source ignores it.
        for seed in self._source.seeds(self._dataset, board=self._board):
            self._enqueue(seed)

        if not self._queue.empty():
            workers = [
                threading.Thread(target=self._worker, name=f"{self._source.key}-{i}", daemon=True)
                for i in range(self._policy.concurrency)
            ]
            for worker in workers:
                worker.start()
            self._queue.join()
            for _ in workers:
                self._queue.put(_DONE)
            for worker in workers:
                worker.join()

        report = self._finished()
        if self._crash is not None:
            # A worker died of something that is not a transport failure: a bug, not a site. The run
            # ends the way it did when this walk was a plain loop -- loudly.
            raise self._crash
        return report

    def _finished(self) -> SourceReport:
        state = self._gate.snapshot()
        self._report.final_interval_s = state.interval_s
        self._report.final_concurrency = state.concurrency
        self._report.configured_interval_s = self._policy.min_interval_s
        self._report.configured_concurrency = self._policy.concurrency
        self._report.request_budget = self._policy.max_requests_per_run
        self._report.scope = narrowed(self._source.scope, [self._dataset])
        return self._report

    def _halted(self) -> bool:
        with self._lock:
            return self._stop

    def _halt(self) -> None:
        """Stop taking new work without abandoning what is already queued: the queued items still
        have to be drained or `queue.join()` never returns, so the workers discard them instead."""
        with self._lock:
            self._stop = True

    def _enqueue(self, fetch: Fetch) -> None:
        with self._lock:
            if fetch.depth > self._policy.max_depth:
                self._report.dropped_over_depth += 1
                return
            # Depth is not part of identity: the same URL reached two ways is one request, and
            # counting it twice would spend the budget on nothing.
            key = dataclasses.replace(fetch, depth=0)
            if key in self._seen:
                self._report.deduped += 1
                return
            self._seen.add(key)
        self._queue.put(fetch)

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _DONE:
                    return
                if not self._halted():
                    self._handle(item)  # pyright: ignore[reportArgumentType]
            except BaseException as exc:  # noqa: BLE001 - see _crash: re-raised from run()
                with self._lock:
                    if self._crash is None:
                        self._crash = exc
                self._halt()
            finally:
                self._queue.task_done()

    def _handle(self, fetch: Fetch) -> None:
        payload = self._fetch_with_retries(fetch)
        if payload is None:
            return
        stamped = dataclasses.replace(payload, captured_at=self._captured_at)
        try:
            result = self._source.parse(stamped)
        except Exception as exc:  # noqa: BLE001 - a broken parser costs one request, not the run
            with self._lock:
                self._report.errors.append(f"{fetch.url}: parse failed: {exc}")
            return

        if result.records:
            self._sink.write(result.records)
            with self._lock:
                self._report.records += len(result.records)
        for follow in result.follow:
            self._enqueue(dataclasses.replace(follow, depth=fetch.depth + 1))

    def _fetch_with_retries(self, fetch: Fetch) -> Payload | None:
        request = _with_user_agent(fetch, self._policy)
        for attempt in range(1, self._policy.max_attempts + 1):
            if self._halted() or self._over_budget():
                return None

            with self._gate.acquire():
                if self._halted() or not self._charge():
                    return None
                # Filled in by whichever branch below runs, and written once in the `finally`. Every
                # path out of this block -- success, refusal, a retry's `continue` -- leaves a row,
                # which is the only way the journal and the report's request count can agree.
                status: int | None = None
                error: str | None = None
                elapsed_ms: int | None = None
                size: int | None = None
                try:
                    try:
                        payload = self._fetcher.fetch(request)
                    except ChallengeBlocked as exc:
                        status, error = exc.status or 403, str(exc)
                        self._gate.observe(exc.status or 403, challenged=True)
                        with self._lock:
                            self._report.blocked_reason = str(exc)
                        self._halt()
                        return None
                    except PermanentError as exc:
                        status, error = exc.status, str(exc)
                        self._gate.observe(exc.status or 0)
                        with self._lock:
                            self._report.errors.append(f"{fetch.url}: {exc}")
                        return None
                    except (RateLimited, TransientError) as exc:
                        status, error = exc.status, str(exc)
                        self._gate.observe(exc.status or 0, exc.retry_after)
                        if attempt == self._policy.max_attempts:
                            with self._lock:
                                self._report.errors.append(f"{fetch.url}: gave up after {attempt}: {exc}")
                            return None
                        with self._lock:
                            self._report.retries += 1
                        continue
                    except TransportError as exc:
                        status, error = exc.status, str(exc)
                        with self._lock:
                            self._report.errors.append(f"{fetch.url}: {exc}")
                        return None

                    status = payload.status
                    elapsed_ms = payload.elapsed_ms
                    size = len(payload.body)
                    self._gate.observe(payload.status)
                    return payload
                finally:
                    self._journal.record(
                        FetchAttempt(
                            source=self._source.key,
                            dataset=fetch.dataset,
                            url=fetch.url,
                            attempt=attempt,
                            status=status,
                            elapsed_ms=elapsed_ms,
                            bytes=size,
                            error=error,
                        )
                    )

    def _truncate(self) -> None:
        """Caller holds `_lock`. Running out of budget is a reportable outcome and a reason to stop
        taking new work; both places that can discover it record it the same way from here."""
        self._report.budget_exhausted = True
        self._stop = True

    def _over_budget(self) -> bool:
        """A look at the budget in front of the gate, so a lane with nothing left to spend is not
        first made to wait out the source's interval to be told so -- daisomall's is 30 seconds.

        Advisory only: this can say no, but a yes means nothing by the time the gate lets go of the
        worker. `_charge` is what enforces, so a mutation that pins this to False costs a run one
        needless wait and breaks no assertion here -- correctly, because it guards latency, not the
        cap.
        """
        budget = self._policy.max_requests_per_run
        if budget is None:
            return False
        with self._lock:
            if self._report.requests < budget:
                return False
            self._truncate()
            return True

    def _charge(self) -> bool:
        """Book one request against the run's budget, or refuse it. This is the enforcement.

        A retry is another request the site sees, so every attempt is charged to the budget the first
        one was. Reading the count and spending it happen inside a single hold of `_lock` on purpose:
        with the check and the increment in separate holds, every worker sitting on `budget - 1`
        passes before any of them writes and a source at concurrency N sends N - 1 requests it had no
        budget for. That race is shipped now: hwahae runs two workers and, since #10, a budget of 20,
        so this hold is the only thing between it and 21 requests.
        `tests/collectors/commerce/test_rate_policy_is_enforced.py` puts workers in that window.

        Booked inside the gate, one step before the request goes out, so the count cannot run ahead
        of the journal.
        """
        budget = self._policy.max_requests_per_run
        with self._lock:
            if budget is not None and self._report.requests >= budget:
                self._truncate()
                return False
            self._report.requests += 1
            return True


def collect(
    sources: Sequence[Source],
    dataset: Dataset,
    sink: Sink,
    captured_at: datetime,
    fetcher: Fetcher | FetcherFor,
    journal: Journal | None = None,
    board: str | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    lock: SourceLock = uncoordinated,
    max_lanes: int | None = None,
) -> RunReport:
    """Walk every source that declares `dataset`, all of them at once.

    One lane per source, running together (#25). #7 walked them one after another on the grounds that
    nothing here waited on wall clock; once #10 gave every source a real transport behind a real
    `Gate`, waiting on wall clock was all a run did, and the cost of the sum showed up in production
    -- the hourly ranking walk took 202s against the original collector's 90s over the same four
    sites (trend_radar.fetch_log, 2026-08-24). The original overlapped them with
    `asyncio.gather(*(lane.run() for lane in lanes))`; this package is synchronous from the fetchers
    up, so the lanes are threads and nothing above them changes shape.

    What parallel lanes do *not* touch is the pace: every lane still owns a `Gate` of its own, so a
    source's interval, burst and in-flight ceiling are exactly what they were when it was walked
    alone. The sum only ever counted sources waiting for each other.
    (tests/collectors/commerce/test_sources_walk_in_parallel_lanes.py holds that.) `clock` and
    `sleep` are injected so the pacing is testable without spending a source's 30-second interval.

    `max_lanes` caps how many walk together, and it is a *connection* budget rather than a taste:
    `lock` pins one connection per walking lane for the length of that walk, which one-at-a-time
    never had to count. `None` means every source at once -- right for a test with no database
    behind it, wrong for the thing cron runs, so `collectors/commerce/cli.py` passes
    `storage/db.py`'s `MAX_CONCURRENT_LANES` and the test file above fails a caller that does not.

    That gate is per lane and so per process, which is what `lock` is above: the source another run
    already holds is skipped rather than walked at twice the policy's rate. The default coordinates
    with nobody; `collectors/commerce/storage/locks.py` is the one the CLI installs. A default that
    quiet needs a keeper, so tests/collectors/commerce/test_source_lock.py reads every caller outside
    the tests and fails the one that leaves `lock=` off.
    """
    active_journal = journal or NullJournal()
    walked = [source for source in sources if dataset in source.datasets]
    if not walked:
        return RunReport(captured_at=captured_at, sources={})

    def walk(source: Source) -> SourceReport:
        # Held for the whole walk of this one source, and only this one: a source another run has is
        # given up, the rest of the run carries on.
        with lock(source.key) as held:
            if not held:
                return SourceReport(key=source.key, skipped_reason=LOCK_HELD_ELSEWHERE)
            lane = _Lane(
                source=source,
                # `Fetcher` is runtime-checkable and has one member, so this asks the plain
                # question "did the caller hand us something that can fetch": a factory cannot,
                # and nothing that can is ever called as one.
                fetcher=fetcher if isinstance(fetcher, Fetcher) else fetcher(source),
                sink=sink,
                dataset=dataset,
                captured_at=captured_at,
                journal=active_journal,
                board=board,
                gate=Gate(source.policy, clock, sleep),
            )
            return lane.run()

    lanes = len(walked) if max_lanes is None else max(1, min(len(walked), max_lanes))
    reports: dict[str, SourceReport] = {}
    crashes: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=lanes, thread_name_prefix="lane") as pool:
        started = [(source, pool.submit(walk, source)) for source in walked]
        # Every future is waited on before anything is raised, and they are read in the caller's
        # source order: a lane that dies of a bug must not cancel the sources beside it, and the
        # report a run logs must not depend on which lane happened to finish first.
        for source, future in started:
            try:
                reports[source.key] = future.result()
            except BaseException as exc:  # noqa: BLE001 - re-raised below, once every lane is home
                crashes.append(exc)

    if crashes:
        # Still loud: a crash here is a bug, not a site, and #7's plain loop ended the run with it.
        # One is re-raised as itself so the run log keeps saying `RuntimeError: ...`; several are
        # grouped because picking one of them would throw away the others' tracebacks.
        raise crashes[0] if len(crashes) == 1 else BaseExceptionGroup("lanes crashed", crashes)
    return RunReport(captured_at=captured_at, sources=reports)
