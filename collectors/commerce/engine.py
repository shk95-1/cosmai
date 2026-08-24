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
  1  partial -- a source errored or hit its request budget
  2  a source was refused outright
"""

from __future__ import annotations

import dataclasses
import queue
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

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


class Fetcher(Protocol):
    def fetch(self, fetch: Fetch) -> Payload: ...


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
        return self.blocked_reason is None and not self.errors and not self.stopped_short


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
    def write(self, records: Sequence[Record]) -> None: ...


@dataclass
class RunReport:
    captured_at: datetime
    sources: dict[str, SourceReport]

    @property
    def blocked(self) -> list[str]:
        return sorted(k for k, r in self.sources.items() if r.blocked_reason is not None)

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
    and the halt flag are shared across those workers, so every mutation of them holds `_lock`; the
    pacing and the in-flight ceiling are the gate's, not this class's.
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
            if self._halted() or self._spent():
                return None

            with self._gate.acquire():
                if self._halted():
                    return None
                with self._lock:
                    self._report.requests += 1
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
        return None

    def _spent(self) -> bool:
        """A retry is another request the site sees, so it is charged to the same budget the first
        attempt was -- checked before the gate, so a spent budget costs no waiting."""
        budget = self._policy.max_requests_per_run
        if budget is None:
            return False
        with self._lock:
            if self._report.requests < budget:
                return False
            self._report.budget_exhausted = True
            self._stop = True
            return True


def collect(
    sources: Sequence[Source],
    dataset: Dataset,
    sink: Sink,
    captured_at: datetime,
    fetcher: Fetcher,
    journal: Journal | None = None,
    board: str | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RunReport:
    """Walk every source that declares `dataset`, one source after another.

    Sources run in sequence rather than as the original's parallel lanes -- a run collects four sites
    an hour and nothing here is waiting on wall clock -- but each source's own requests are paced and
    capped by its `SourcePolicy` through a `Gate` of its own. `clock` and `sleep` are injected so the
    pacing is testable without spending a source's 30-second interval.
    """
    active_journal = journal or NullJournal()
    reports: dict[str, SourceReport] = {}
    for source in sources:
        if dataset not in source.datasets:
            continue
        lane = _Lane(
            source=source,
            fetcher=fetcher,
            sink=sink,
            dataset=dataset,
            captured_at=captured_at,
            journal=active_journal,
            board=board,
            gate=Gate(source.policy, clock, sleep),
        )
        reports[source.key] = lane.run()
    return RunReport(captured_at=captured_at, sources=reports)
