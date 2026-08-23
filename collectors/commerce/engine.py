"""One collection, start to finish, and what its outcome means to cron.

origin: service/trend-radar/src/trend_radar/engine/{collect,lane,journal}.py -- ported for #7 as a
single synchronous module. The original engine is asyncio-based (concurrent lanes, a request-pacing
gate, a browser/http transport); this issue is "collectors/commerce 이식", not a live-collection cutover
(#10, "라이브 수집 없음"), so what is ported here is the crawl-frontier and exit-code semantics that the
fixture/Postgres tests exercise -- BFS over `Fetch`/`Yield` with `max_depth`/`max_requests_per_run`
honoured and every attempt journaled. Concurrency, the request-pacing gate and a real HTTP/browser
`Fetcher` are #10's job, plugged in behind the same `Fetcher` protocol below.

The exit code is the part worth being deliberate about: this runs unattended, so the only thing most
runs will ever say is a number.

  0  every source collected, nothing refused, nothing truncated
  1  partial -- a source errored or hit its request budget
  2  a source was refused outright
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from collectors.commerce.contract import Fetch, Payload, Source, narrowed
from collectors.commerce.models import Dataset, Record


class TransportError(Exception):
    """Base for every way a fetch can fail. `status` is the HTTP status where one was seen."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class PermanentError(TransportError):
    """A response the engine should not retry -- a 404, a malformed request."""


class RateLimited(TransportError):
    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message, status=status)
        self.retry_after = retry_after


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


def _walk_one_source(
    source: Source,
    fetcher: Fetcher,
    sink: Sink,
    dataset: Dataset,
    captured_at: datetime,
    journal: Journal,
    board: str | None,
) -> SourceReport:
    report = SourceReport(key=source.key)
    if dataset not in source.datasets:
        report.scope = {}
        return report

    policy = source.policy
    report.configured_interval_s = policy.min_interval_s
    report.configured_concurrency = policy.concurrency
    report.request_budget = policy.max_requests_per_run

    # `board` is part of every source's `seeds` signature (contract.Source), but only means anything
    # to a REVIEW_LOW walk (#7) -- every other source ignores it.
    seeds = source.seeds(dataset, board=board)
    queue: list[Fetch] = list(seeds)
    seen: set[Fetch] = set()
    stop = False

    def enqueue(fetch: Fetch) -> None:
        if fetch.depth > policy.max_depth:
            report.dropped_over_depth += 1
            return
        key = dataclasses.replace(fetch, depth=0)
        if key in seen:
            report.deduped += 1
            return
        seen.add(key)
        queue.append(fetch)

    for seed in seeds:
        seen.add(dataclasses.replace(seed, depth=0))

    while queue and not stop:
        fetch = queue.pop(0)
        budget = policy.max_requests_per_run
        if budget is not None and report.requests >= budget:
            report.budget_exhausted = True
            break

        report.requests += 1
        status: int | None = None
        error: str | None = None
        elapsed_ms: int | None = None
        size: int | None = None
        payload: Payload | None = None
        try:
            try:
                payload = fetcher.fetch(fetch)
            except ChallengeBlocked as exc:
                status, error = exc.status or 403, str(exc)
                report.blocked_reason = str(exc)
                stop = True
                continue
            except PermanentError as exc:
                status, error = exc.status, str(exc)
                report.errors.append(f"{fetch.url}: {exc}")
                continue
            except (RateLimited, TransientError) as exc:
                status, error = exc.status, str(exc)
                report.errors.append(f"{fetch.url}: gave up: {exc}")
                continue
            except TransportError as exc:
                status, error = exc.status, str(exc)
                report.errors.append(f"{fetch.url}: {exc}")
                continue

            status = payload.status
            elapsed_ms = payload.elapsed_ms
            size = len(payload.body)
            stamped = dataclasses.replace(payload, captured_at=captured_at)
            try:
                result = source.parse(stamped)
            except Exception as exc:  # noqa: BLE001 - a broken parser costs one request, not the run
                report.errors.append(f"{fetch.url}: parse failed: {exc}")
                continue

            if result.records:
                sink.write(result.records)
                report.records += len(result.records)
            for follow in result.follow:
                enqueue(dataclasses.replace(follow, depth=fetch.depth + 1))
        finally:
            journal.record(
                FetchAttempt(
                    source=source.key,
                    dataset=fetch.dataset,
                    url=fetch.url,
                    attempt=1,
                    status=status,
                    elapsed_ms=elapsed_ms,
                    bytes=size,
                    error=error,
                )
            )

    report.scope = narrowed(source.scope, [dataset])
    return report


def collect(
    sources: Sequence[Source],
    dataset: Dataset,
    sink: Sink,
    captured_at: datetime,
    fetcher: Fetcher,
    journal: Journal | None = None,
    board: str | None = None,
) -> RunReport:
    """Walk every source that declares `dataset`, one after another.

    Sequential rather than the original's per-source concurrent lanes: this issue has no live
    `Fetcher` to pace against (#10), so there is nothing here for concurrency to buy yet.
    """
    active_journal = journal or NullJournal()
    reports: dict[str, SourceReport] = {}
    for source in sources:
        if dataset not in source.datasets:
            continue
        reports[source.key] = _walk_one_source(
            source, fetcher, sink, dataset, captured_at, active_journal, board
        )
    return RunReport(captured_at=captured_at, sources=reports)
