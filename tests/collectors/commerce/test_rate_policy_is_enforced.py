"""The rate policy is behaviour, not four numbers in a dataclass.

#7 ported oliveyoung's 5s/1, glowpick's 5s/1/20, daisomall's 30s/1 and hwahae's 1s/2 with a diff of
zero against the original -- and nothing that read them. `configured_interval_s` was recorded,
`SourceReport.retries` was never incremented, `FetchAttempt.attempt` was the literal 1,
`SourcePolicy.user_agent` reached no request and `max_requests_per_run` stopped nothing. Every
assertion here is on what the engine did to a fake fetcher, so a future rewrite that keeps the
constants and drops the enforcement fails here rather than on the sites (#10 §A-1).

No network and no real sleeping: the clock is injected, and the rendezvous points that make the
concurrency claims meaningful are barriers with generous timeouts. A timed-out barrier is reported
as its own failure rather than swallowed, so a loaded machine cannot turn "the threads never got to
overlap" into "the ceiling was breached".
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from collectors.commerce.contract import Fetch, Payload, Scope, Source, SourcePolicy, Yield
from collectors.commerce.engine import (
    FetchAttempt,
    NullJournal,
    RateLimited,
    RunReport,
    _Lane,
    collect,
    exit_code_for,
)
from collectors.commerce.gate import Gate, GateState
from collectors.commerce.models import Dataset

AT = datetime(2026, 8, 24, 3, tzinfo=UTC)

# Long enough that no scheduler hiccup reaches them, and paid only when a test is already failing.
BARRIER_TIMEOUT_S = 10.0
JOIN_TIMEOUT_S = 30.0


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
        # 이 가짜 소스는 리뷰를 걷지 않는다 -- 빈 값이 답이다(#144 의 review_body_datasets).
        review_body_datasets: ClassVar[frozenset[Dataset]] = frozenset()
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


class _CountingFetcher:
    """Answers everything and counts. The budget tests care only about how many requests got out."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0

    def fetch(self, fetch: Fetch) -> Payload:
        with self._lock:
            self.calls += 1
        return _ok(fetch)


class _ConcurrentFetcher:
    """Counts how many fetches are inside this method at once. The rendezvous is what makes the count
    meaningful: it releases only once `expected` requests are inside together, so a lane that never
    overlaps them times out and says so instead of quietly reporting a peak of one."""

    def __init__(self, expected: int, timeout: float = BARRIER_TIMEOUT_S) -> None:
        self._barrier = threading.Barrier(expected, timeout=timeout)
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0
        self.stalled = False

    def fetch(self, fetch: Fetch) -> Payload:
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            try:
                self._barrier.wait()
            except threading.BrokenBarrierError:
                self.stalled = True
            return _ok(fetch)
        finally:
            with self._lock:
                self._in_flight -= 1


class _RendezvousGate(Gate):
    """A gate that admits nobody until `parties` workers are asking for it, once.

    Real contention needs a real block. With a fake clock and an in-memory fetcher a lane is
    effectively single-threaded -- one worker walks the whole frontier before the GIL ever moves --
    so a budget check racing its own increment would never be caught by simply spawning workers.
    Holding every worker here, one step before the budget is charged, is what puts them all in that
    window at once.

    Once only, and that is not a detail: a cyclic barrier deadlocks this test on the very code it
    exists to catch. A lane that overshoots its budget has workers still willing to fetch after the
    barrier trips, and the first one back round would wait out the timeout alone -- turning "the cap
    leaked" into "the rendezvous stalled". Admission is counted, so exactly the first `parties`
    arrivals wait and everyone after walks through.
    """

    def __init__(self, policy: SourcePolicy, parties: int, timeout: float = BARRIER_TIMEOUT_S) -> None:
        super().__init__(policy, clock=_FakeClock().time, sleep=lambda _: None)
        self._barrier = threading.Barrier(parties, timeout=timeout)
        self._parties = parties
        self._admission = threading.Lock()
        self._arrived = 0
        self.stalled = False

    def _rendezvous(self) -> None:
        with self._admission:
            if self._arrived >= self._parties:
                return
            self._arrived += 1
        try:
            self._barrier.wait()
        except threading.BrokenBarrierError:
            self.stalled = True

    @contextmanager
    def acquire(self) -> Iterator[None]:
        # Inside the real gate, not in front of it. The token bucket takes a mutex, so a rendezvous
        # placed before `super().acquire()` is undone by it: the workers leave the barrier together
        # and immediately queue up single file again, and the first one out laps the rest before the
        # last one has charged anything. Held here, all `parties` are past every one of the gate's
        # own locks with only the budget left between them.
        with super().acquire():
            self._rendezvous()
            yield


def _collect(source: Source, fetcher, clock: _FakeClock, journal: _RecordingJournal | None = None):
    return collect(
        sources=[source],
        dataset=Dataset.RANKING,
        sink=_NullSink(),
        captured_at=AT,
        fetcher=fetcher,
        journal=journal,
        clock=clock.time,
        sleep=clock.sleep,
    )


def _run(source: Source, fetcher, clock: _FakeClock, journal: _RecordingJournal | None = None):
    return _collect(source, fetcher, clock, journal).sources["fake"]


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


# --- the request budget -------------------------------------------------------


def test_a_frontier_larger_than_the_budget_stops_at_the_budget():
    clock = _FakeClock()
    policy = SourcePolicy(min_interval_s=0.0, concurrency=1, max_requests_per_run=4)
    source = _source(policy, seeds_count=10)
    fetcher = _CountingFetcher()

    run: RunReport = _collect(source, fetcher, clock)
    report = run.sources["fake"]

    assert fetcher.calls == 4, f"{fetcher.calls} requests went out against a budget of 4"
    assert report.requests == 4
    assert report.budget_exhausted, "the run walked less of the site than the source asked for"
    assert not report.ok
    # Truncated, not refused: cron gets 1 (partial), not 2 (blocked) and not 0.
    assert exit_code_for(run) == 1


def test_a_retry_is_charged_to_the_same_budget_as_the_first_attempt():
    clock = _FakeClock()
    policy = SourcePolicy(min_interval_s=0.0, concurrency=1, max_attempts=3, max_requests_per_run=4)
    source = _source(policy, seeds_count=5)
    fetcher = _FlakyFetcher(99, RateLimited("slow down", status=429))

    report = _run(source, fetcher, clock)

    # Three attempts at the first seed and one at the second: a retry is a request the site sees, so
    # the budget counts requests rather than URLs and two of the five seeds are never reached.
    assert fetcher.calls == 4, f"{fetcher.calls} requests went out against a budget of 4"
    assert report.requests == 4
    assert report.retries == 3
    assert report.budget_exhausted


def test_the_request_budget_holds_when_workers_race_for_the_last_request():
    # concurrency > 1 is where a budget check that is not atomic with its increment leaks: every
    # worker reads `requests == budget - 1` before any of them writes, and all of them fire. Nothing
    # shipped exercises this yet -- hwahae is the only source above one worker and it declares no
    # budget -- so raising a source's concurrency would quietly break the cap.
    #
    # One seed per worker, deliberately. Give the frontier spare items and the worker that trips the
    # barrier runs on, finishes a second request and halts the lane while the other five are still
    # between the barrier and their own increment -- the cap then holds by a scheduling accident and
    # the test says nothing. With nothing left to pick up, every worker is parked in the window at
    # once and the only thing standing between them and an overshoot is the budget itself.
    #
    # What this can and cannot catch: the rendezvous holds the workers where the gate does, so it
    # catches a budget check separated from its increment by the gate -- which is the shape the bug
    # had. It would not catch a window two bytecodes wide, because nothing here can force a thread
    # switch inside one. `_charge` keeping both under one lock is what makes that moot.
    #
    # The lane is built here rather than through `collect` for one reason: the gate is a constructor
    # argument, and this needs one that blocks.
    policy = SourcePolicy(min_interval_s=0.0, concurrency=6, max_requests_per_run=2)
    gate = _RendezvousGate(policy, parties=policy.concurrency)
    fetcher = _CountingFetcher()
    lane = _Lane(
        source=_source(policy, seeds_count=policy.concurrency),
        fetcher=fetcher,
        sink=_NullSink(),
        dataset=Dataset.RANKING,
        captured_at=AT,
        journal=NullJournal(),
        board=None,
        gate=gate,
    )

    report = lane.run()

    assert not gate.stalled, "the six workers never reached the gate together: no race was run"
    assert fetcher.calls == 2, f"{fetcher.calls} requests went out against a budget of 2"
    assert report.requests == 2
    assert report.budget_exhausted


# --- the concurrency ceiling --------------------------------------------------


def test_no_more_requests_are_in_flight_than_the_policy_allows():
    clock = _FakeClock()
    policy = SourcePolicy(min_interval_s=0.0, concurrency=3)
    source = _source(policy, seeds_count=6)
    fetcher = _ConcurrentFetcher(expected=policy.concurrency)

    report = _run(source, fetcher, clock)

    # Two separate claims: that three requests did overlap, and that no fourth ever joined them.
    assert not fetcher.stalled, "three requests never overlapped: the rendezvous inside fetch timed out"
    assert fetcher.max_in_flight <= policy.concurrency, (
        f"{fetcher.max_in_flight} requests were in flight against a concurrency of {policy.concurrency}"
    )
    assert report.final_concurrency == policy.concurrency


def test_the_gate_caps_in_flight_requests_whatever_the_caller_spawns():
    # The ceiling belongs to the gate, not to a worker count: #10 plugs a live transport in behind
    # the same gate, and it must not be able to widen the source's limit by adding threads.
    limit = 2
    spawned = 6
    gate = Gate(
        SourcePolicy(min_interval_s=0.0, concurrency=limit), clock=_FakeClock().time, sleep=lambda _: None
    )
    lock = threading.Lock()
    in_flight = 0
    peak = 0
    trouble: list[str] = []
    start = threading.Barrier(spawned, timeout=BARRIER_TIMEOUT_S)
    # Whoever is inside the gate waits for the rest of the slots to fill. That both proves the slots
    # overlap and holds them, so a gate handing out too many is caught doing it -- and it does that
    # without a wall-clock sleep, which on a loaded machine would prove neither.
    inside = threading.Barrier(limit, timeout=BARRIER_TIMEOUT_S)

    def one() -> None:
        nonlocal in_flight, peak
        try:
            start.wait()
            with gate.acquire():
                with lock:
                    in_flight += 1
                    peak = max(peak, in_flight)
                inside.wait()
                with lock:
                    in_flight -= 1
        except threading.BrokenBarrierError:
            trouble.append(f"a rendezvous timed out after {BARRIER_TIMEOUT_S}s; the gate never filled")
        except Exception as exc:  # noqa: BLE001 - reported below rather than dying inside a thread
            trouble.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=one) for _ in range(spawned)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT_S)

    assert not [t for t in threads if t.is_alive()], "a thread never came back out of the gate"
    assert not trouble, trouble[0]
    assert peak <= limit, f"{peak} requests were in flight against a concurrency of {limit}"
    # A separate claim from the ceiling: the ceiling is not being met by never overlapping at all.
    assert peak == limit


# --- how the gate adapts ------------------------------------------------------


def _gate(policy: SourcePolicy) -> Gate:
    return Gate(policy, clock=_FakeClock().time, sleep=lambda _: None)


def test_a_refusal_halves_the_concurrency():
    gate = _gate(SourcePolicy(min_interval_s=1.0, concurrency=4))

    gate.observe(429)

    assert gate.snapshot().concurrency == 2, "a 429 that only widens the interval keeps four workers on"


def test_the_doubling_stops_at_the_ceiling_however_long_the_refusals_last():
    gate = _gate(SourcePolicy(min_interval_s=1.0, concurrency=1))

    for _ in range(20):
        gate.observe(429)

    # Two claims, because comparing only against the constant would pass for any value it held.
    assert gate.snapshot().interval_s <= 3600.0, (
        "an uncapped doubling parks the source past the hour it is scheduled in"
    )
    assert gate.snapshot().interval_s == Gate.MAX_INTERVAL_S


def test_an_explicit_retry_after_is_honoured_past_the_ceiling():
    gate = _gate(SourcePolicy(min_interval_s=1.0, concurrency=1))

    gate.observe(429, retry_after=Gate.MAX_INTERVAL_S * 2)

    assert gate.snapshot().interval_s == Gate.MAX_INTERVAL_S * 2, "that number came from the server"


def test_the_gate_creeps_back_only_after_a_run_of_good_responses():
    gate = _gate(SourcePolicy(min_interval_s=5.0, concurrency=2))
    gate.observe(429)
    assert gate.snapshot() == GateState(interval_s=10.0, concurrency=1)

    for _ in range(Gate.RECOVER_AFTER - 1):
        gate.observe(200)
    assert gate.snapshot() == GateState(interval_s=10.0, concurrency=1), (
        "the response right after a 429 is often fine and proves nothing"
    )

    gate.observe(200)
    assert gate.snapshot() == GateState(interval_s=5.0, concurrency=2), "the gate never creeps back"


def test_a_fresh_refusal_restarts_the_run_of_good_responses():
    gate = _gate(SourcePolicy(min_interval_s=5.0, concurrency=2))
    gate.observe(429)

    for _ in range(Gate.RECOVER_AFTER - 1):
        gate.observe(200)
    gate.observe(429)
    for _ in range(Gate.RECOVER_AFTER - 1):
        gate.observe(200)

    assert gate.snapshot().concurrency == 1, "a refusal has to restart the count, not top it up"


# --- what every request carries -----------------------------------------------


@pytest.mark.parametrize("declared", ["cosmai-test/9.9", "cosmai-commerce/0.1 (+https://example.invalid)"])
def test_the_policys_user_agent_reaches_the_fetcher(declared: str):
    clock = _FakeClock()
    source = _source(SourcePolicy(min_interval_s=0.0, concurrency=1, user_agent=declared), seeds_count=1)
    fetcher = _StampingFetcher(clock)

    _run(source, fetcher, clock)

    headers = {k.lower(): v for k, v in fetcher.seen[0].headers}
    assert headers.get("user-agent") == declared


# --- what every shipped source has to declare ----------------------------------


def test_every_shipped_source_declares_a_request_budget():
    """A source with no `max_requests_per_run` has no worst case.

    Harmless while nothing paced and nothing fetched (#7); not harmless now that a lane really waits
    out `min_interval_s` between real requests, because `max_depth` alone bounds how deep a walk
    goes and not how wide. hwahae was the last one without a budget and got 20 for #10 (사용자 승인
    2026-08-24) -- twenty times the one request production measures per run.
    """
    from collectors.commerce import sources as _registered  # noqa: F401 -- import registers them
    from collectors.commerce.registry import SOURCES

    assert SOURCES, "an empty registry would assert nothing"
    unbounded = sorted(key for key, cls in SOURCES.items() if cls.policy.max_requests_per_run is None)
    assert not unbounded, f"these sources have no ceiling on a run's requests: {unbounded}"
