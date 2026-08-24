"""The rate policy is behaviour, not four numbers in a dataclass.

#7 ported oliveyoung's 5s/1, glowpick's 5s/1/20, daisomall's 30s/1 and hwahae's 1s/2 with a diff of
zero against the original -- and nothing that read them. `configured_interval_s` was recorded,
`SourceReport.retries` was never incremented, `FetchAttempt.attempt` was the literal 1, and
`SourcePolicy.user_agent` reached no request. Every assertion here is on what the engine did to a
fake fetcher, so a future rewrite that keeps the constants and drops the enforcement fails here
rather than on the sites (#10 §A-1). No network and no real sleeping: the clock is injected.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from collectors.commerce.contract import Fetch, Payload, Scope, Source, SourcePolicy, Yield
from collectors.commerce.engine import FetchAttempt, RateLimited, collect
from collectors.commerce.gate import Gate
from collectors.commerce.models import Dataset

AT = datetime(2026, 8, 24, 3, tzinfo=UTC)


class _FakeClock:
    """A clock that only moves when someone sleeps -- so a 30s interval costs the suite nothing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self.now += max(seconds, 0.0)


def _source(source_policy: SourcePolicy, seeds_count: int = 3) -> Source:
    """A source with nothing site-specific about it: `seeds_count` URLs, and a parser that keeps
    nothing. `policy` is a ClassVar on the protocol, so each policy under test gets its own class."""

    class _FakeSource:
        key: ClassVar[str] = "fake"
        datasets: ClassVar[frozenset[Dataset]] = frozenset({Dataset.RANKING})
        scope: ClassVar[Scope] = {Dataset.RANKING: {"seeds": seeds_count}}
        policy: ClassVar[SourcePolicy] = source_policy

        def seeds(self, dataset: Dataset, *, board: str | None = None) -> Sequence[Fetch]:
            del board
            return tuple(
                Fetch(url=f"https://example.invalid/{i}", dataset=dataset) for i in range(seeds_count)
            )

        def parse(self, payload: Payload) -> Yield:
            del payload
            return Yield()

    return _FakeSource()


class _NullSink:
    def write(self, records) -> None:
        return None


@dataclass
class _RecordingJournal:
    attempts: list[FetchAttempt] = field(default_factory=list)

    def record(self, attempt: FetchAttempt) -> None:
        self.attempts.append(attempt)


def _ok(fetch: Fetch) -> Payload:
    return Payload(fetch=fetch, status=200, body=b"{}", final_url=fetch.url, headers={}, elapsed_ms=1)


class _StampingFetcher:
    """Records when each request arrived, on the injected clock, and what it carried."""

    def __init__(self, clock: _FakeClock) -> None:
        self._clock = clock
        self.at: list[float] = []
        self.seen: list[Fetch] = []

    def fetch(self, fetch: Fetch) -> Payload:
        self.at.append(self._clock.time())
        self.seen.append(fetch)
        return _ok(fetch)


class _FlakyFetcher:
    """Raises `error` for the first `failures` calls, then succeeds."""

    def __init__(self, failures: int, error: Exception) -> None:
        self._failures = failures
        self._error = error
        self.calls = 0

    def fetch(self, fetch: Fetch) -> Payload:
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return _ok(fetch)


class _ConcurrentFetcher:
    """Counts how many fetches are inside this method at once. The barrier is what makes the count
    meaningful: a lane that never runs two requests together simply times out on it."""

    def __init__(self, expected: int, timeout: float = 1.0) -> None:
        self._barrier = threading.Barrier(expected, timeout=timeout)
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0

    def fetch(self, fetch: Fetch) -> Payload:
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            try:
                self._barrier.wait()
            except threading.BrokenBarrierError:
                pass  # a serial lane: the count above already recorded the truth
            return _ok(fetch)
        finally:
            with self._lock:
                self._in_flight -= 1


def _run(source: Source, fetcher, clock: _FakeClock, journal: _RecordingJournal | None = None):
    return collect(
        sources=[source],
        dataset=Dataset.RANKING,
        sink=_NullSink(),
        captured_at=AT,
        fetcher=fetcher,
        journal=journal,
        clock=clock.time,
        sleep=clock.sleep,
    ).sources["fake"]


def test_requests_to_one_source_are_spaced_by_the_policys_min_interval():
    clock = _FakeClock()
    source = _source(SourcePolicy(min_interval_s=5.0, concurrency=1), seeds_count=3)
    fetcher = _StampingFetcher(clock)

    report = _run(source, fetcher, clock)

    assert len(fetcher.at) == 3
    gaps = [b - a for a, b in zip(fetcher.at, fetcher.at[1:], strict=False)]
    assert all(gap >= 5.0 for gap in gaps), f"requests {gaps}s apart, policy says 5.0s"
    assert report.final_interval_s == 5.0


def test_a_rate_limited_request_is_retried_and_the_interval_widens_each_time():
    clock = _FakeClock()
    source = _source(SourcePolicy(min_interval_s=5.0, concurrency=1, max_attempts=3), seeds_count=1)
    fetcher = _FlakyFetcher(2, RateLimited("slow down", status=429))
    journal = _RecordingJournal()

    report = _run(source, fetcher, clock, journal)

    assert fetcher.calls == 3
    assert report.retries == 2, "SourceReport.retries stayed dead"
    assert [a.attempt for a in journal.attempts] == [1, 2, 3]
    assert clock.sleeps == [10.0, 20.0], "the gate did not widen after each 429"
    assert report.final_interval_s == 20.0
    assert report.ok


def test_a_source_that_keeps_refusing_gives_up_at_max_attempts_and_says_how_many():
    clock = _FakeClock()
    source = _source(SourcePolicy(min_interval_s=1.0, concurrency=1, max_attempts=2), seeds_count=1)
    fetcher = _FlakyFetcher(99, RateLimited("slow down", status=429))

    report = _run(source, fetcher, clock)

    assert fetcher.calls == 2
    assert report.retries == 1
    assert not report.ok
    assert "gave up after 2" in report.errors[0]


def test_an_honoured_retry_after_beats_the_doubling():
    clock = _FakeClock()
    source = _source(SourcePolicy(min_interval_s=1.0, concurrency=1, max_attempts=2), seeds_count=1)
    fetcher = _FlakyFetcher(1, RateLimited("slow down", status=429, retry_after=45.0))

    _run(source, fetcher, clock)

    assert clock.sleeps == [45.0], "Retry-After came from the server; doubling to 2.0s ignores it"


def test_no_more_requests_are_in_flight_than_the_policy_allows():
    clock = _FakeClock()
    policy = SourcePolicy(min_interval_s=0.0, concurrency=3)
    source = _source(policy, seeds_count=6)
    fetcher = _ConcurrentFetcher(expected=policy.concurrency)

    report = _run(source, fetcher, clock)

    assert fetcher.max_in_flight == policy.concurrency
    assert report.final_concurrency == policy.concurrency


def test_the_gate_caps_in_flight_requests_whatever_the_caller_spawns():
    # The ceiling belongs to the gate, not to a worker count: #10 plugs a live transport in behind
    # the same gate, and it must not be able to widen the source's limit by adding threads.
    gate = Gate(
        SourcePolicy(min_interval_s=0.0, concurrency=2), clock=_FakeClock().time, sleep=lambda _: None
    )
    lock = threading.Lock()
    in_flight = 0
    peak = 0
    start = threading.Barrier(6, timeout=2.0)

    def one() -> None:
        nonlocal in_flight, peak
        start.wait()
        with gate.acquire():
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.02)  # holds the slot long enough that an unenforced cap is visible
            with lock:
                in_flight -= 1

    threads = [threading.Thread(target=one) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert peak == 2, f"{peak} requests were in flight against a concurrency of 2"


@pytest.mark.parametrize("declared", ["cosmai-test/9.9", "cosmai-commerce/0.1 (+https://example.invalid)"])
def test_the_policys_user_agent_reaches_the_fetcher(declared: str):
    clock = _FakeClock()
    source = _source(SourcePolicy(min_interval_s=0.0, concurrency=1, user_agent=declared), seeds_count=1)
    fetcher = _StampingFetcher(clock)

    _run(source, fetcher, clock)

    headers = {k.lower(): v for k, v in fetcher.seen[0].headers}
    assert headers.get("user-agent") == declared
