"""Sources are lanes that run together, and one lane's pace is nobody else's problem.

#25: `collect()` walked sources one after another, so a run cost the *sum* of its sources instead of
the slowest one -- production measured the hourly ranking walk at 202s against the old collector's
90s for the same four sites (trend_radar.fetch_log, 2026-08-24). The old collector ran
`asyncio.gather(*(lane.run() for lane in lanes))`; this repo is synchronous throughout, so the lanes
are threads.

What that change must not cost, and what each test here holds:

  - pacing stays per source. Every lane has its own `Gate`, so a lane parked on its own token bucket
    must not hold up another source's. That is the one property a shared gate (or a single global
    pace lock) would quietly take away, and it is asserted by parking one source inside
    `Gate._take_token` and watching the other source finish anyway.
  - a lane that dies of a bug does not take the others with it. The run still ends loudly -- a crash
    is a bug, not a site -- but every other lane is walked to the end first.
  - the connection budget still holds. `PostgresSourceLock` pins one connection per *walking* lane,
    which sequential runs never had to count: one at a time was one connection.

No wall-clock sleeping and no network: every rendezvous is a barrier or an event with a generous
timeout, and a timed-out one is reported as its own failure rather than read as the property holding.
"""

from __future__ import annotations

import ast
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy.pool import QueuePool

from collectors.commerce import engine as engine_module
from collectors.commerce.contract import Fetch, Payload, Scope, Source, SourcePolicy, Yield
from collectors.commerce.engine import collect
from collectors.commerce.gate import Gate
from collectors.commerce.models import Dataset
from collectors.commerce.storage import db as storage_db

REPO_ROOT = Path(__file__).resolve().parents[3]
AT = datetime(2026, 8, 24, 3, tzinfo=UTC)

# Generous, and paid only when a test is already failing: reaching one of these means a lane never
# started, not that it was slow.
RENDEZVOUS_TIMEOUT_S = 10.0
JOIN_TIMEOUT_S = 30.0


def _source(source_key: str, *, seeds_count: int = 1, policy: SourcePolicy | None = None) -> Source:
    """A source with nothing site-specific about it, named so a fetcher can say who asked. A class
    per key because `Source.key` and `Source.policy` are ClassVars -- the same reason
    test_rate_policy_is_enforced.py builds one class per policy."""
    source_policy = policy or SourcePolicy(min_interval_s=0.0, concurrency=1)

    class _FakeSource:
        key: ClassVar[str] = source_key
        datasets: ClassVar[frozenset[Dataset]] = frozenset({Dataset.RANKING})
        # This fake source doesn't walk reviews -- an empty value is the answer (#144's review_body_datasets).
        review_body_datasets: ClassVar[frozenset[Dataset]] = frozenset()
        scope: ClassVar[Scope] = {Dataset.RANKING: {"seeds": seeds_count}}
        policy: ClassVar[SourcePolicy] = source_policy

        def seeds(self, dataset: Dataset, *, board: str | None = None) -> Sequence[Fetch]:
            del board
            return tuple(
                Fetch(url=f"https://example.invalid/{source_key}/{i}", dataset=dataset)
                for i in range(seeds_count)
            )

        def parse(self, payload: Payload) -> Yield:
            del payload
            return Yield()

    return _FakeSource()


class _NullSink:
    def write(self, records) -> None:
        return None


def _ok(fetch: Fetch) -> Payload:
    return Payload(fetch=fetch, status=200, body=b"{}", final_url=fetch.url, headers={}, elapsed_ms=1)


def _collect(sources, **kwargs):
    return collect(
        sources=list(sources),
        dataset=Dataset.RANKING,
        sink=_NullSink(),
        captured_at=AT,
        **kwargs,
    )


# --- the lanes overlap --------------------------------------------------------


class _RendezvousFetcher:
    """Every request waits until `parties` of them are inside `fetch` together. Sequential lanes
    cannot get two sources in here at once, so they break the barrier instead of passing quietly."""

    def __init__(self, parties: int) -> None:
        self._barrier = threading.Barrier(parties, timeout=RENDEZVOUS_TIMEOUT_S)
        self._lock = threading.Lock()
        self.stalled = False
        self.urls: list[str] = []

    def fetch(self, fetch: Fetch) -> Payload:
        try:
            self._barrier.wait()
        except threading.BrokenBarrierError:
            self.stalled = True
        with self._lock:
            self.urls.append(fetch.url)
        return _ok(fetch)


def test_two_sources_are_walked_at_the_same_time():
    fetcher = _RendezvousFetcher(parties=2)

    report = _collect([_source("a"), _source("b")], fetcher=fetcher)

    assert not fetcher.stalled, (
        "two sources never had a request in flight together: the lanes ran one after the other"
    )
    assert sorted(fetcher.urls) == ["https://example.invalid/a/0", "https://example.invalid/b/0"]
    assert report.ok


# --- pacing stays per source --------------------------------------------------


class _GateLog:
    """Every token this run's gates handed out, in the order they were handed out."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.grants: list[str] = []
        self.gates: list[Gate] = []

    def granted(self, key: str) -> None:
        with self._lock:
            self.grants.append(key)

    def taken_by(self, key: str) -> int:
        with self._lock:
            return sum(1 for k in self.grants if k == key)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.grants)


def _parking_gates(sources, log: _GateLog, park: str, released: threading.Event):
    """A `Gate` factory for `collect` to build lanes with: the one belonging to `park` blocks inside
    `_take_token` until `released`, and every gate reports the tokens it hands out.

    Wrapping the bound method rather than subclassing keeps the real gate underneath -- the claim is
    about the shipped `Gate`, not about a stand-in.
    """
    key_of = {id(source.policy): source.key for source in sources}

    def build(policy: SourcePolicy, clock, sleep) -> Gate:
        gate = Gate(policy, clock, sleep)
        key = key_of[id(policy)]
        inner = gate._take_token

        def take_token() -> None:
            if key == park:
                released.wait(timeout=RENDEZVOUS_TIMEOUT_S * 3)
            inner()
            log.granted(key)

        gate._take_token = take_token  # type: ignore[method-assign]
        log.gates.append(gate)
        return gate

    return build


def test_one_sources_pacing_never_holds_up_another(monkeypatch: pytest.MonkeyPatch):
    """The completion bar for #25, and the thing a shared gate would silently take away.

    `Gate._take_token` holds `_pace` across its sleep on purpose: waiters for *one* source queue
    there rather than each waiting out the interval in parallel. Parallel lanes are safe only while
    that queue is per source, so this parks `slow` inside its own `_take_token` -- holding its pace
    lock and its slot -- and asserts `fast` walks its whole frontier regardless.
    """
    slow = _source("slow", seeds_count=2)
    fast = _source("fast", seeds_count=3)
    log = _GateLog()
    released = threading.Event()
    monkeypatch.setattr(
        engine_module, "Gate", _parking_gates([slow, fast], log, park="slow", released=released)
    )

    done: list[object] = []
    walk = threading.Thread(
        target=lambda: done.append(_collect([slow, fast], fetcher=_PlainFetcher())),
        name="collect",
    )
    walk.start()
    try:
        deadline = time.monotonic() + RENDEZVOUS_TIMEOUT_S
        while log.taken_by("fast") < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        grants = log.snapshot()
        assert grants == ["fast"] * 3, (
            f"with one source parked inside its own _take_token, the other got {grants}: the two "
            "sources are sharing a pace, so parallel lanes doubled nothing and coupled everything"
        )
    finally:
        released.set()
        walk.join(timeout=JOIN_TIMEOUT_S)

    assert not walk.is_alive(), "the run never finished after the parked source was let go"
    report = done[0]
    assert report.ok, report.sources  # type: ignore[union-attr]
    assert len(log.gates) == 2, "the two sources did not get a gate each"
    assert log.gates[0] is not log.gates[1]


class _PlainFetcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.urls: list[str] = []

    def fetch(self, fetch: Fetch) -> Payload:
        with self._lock:
            self.urls.append(fetch.url)
        return _ok(fetch)


# --- a lane's failure is its own ----------------------------------------------


class _BoomFetcher:
    """Raises for one source and answers for every other. Not a `TransportError`: those are the
    site's failures and the lane already reports them. This is a bug in the walk."""

    def __init__(self, doomed: str) -> None:
        self._doomed = doomed
        self._lock = threading.Lock()
        self.urls: list[str] = []

    def fetch(self, fetch: Fetch) -> Payload:
        if f"/{self._doomed}/" in fetch.url:
            raise RuntimeError("a bug, not a site")
        with self._lock:
            self.urls.append(fetch.url)
        return _ok(fetch)


def test_a_lane_that_crashes_lets_the_others_finish_first():
    """Loud, but not at the other lanes' expense. `_Lane.run` re-raises a worker's crash so a bug
    cannot be mistaken for a quiet partial run; with lanes in parallel that must not become "the
    first source to hit a bug cancels the rest of the walk"."""
    fetcher = _BoomFetcher("boom")

    with pytest.raises(RuntimeError, match="a bug, not a site"):
        _collect([_source("boom"), _source("good", seeds_count=3)], fetcher=fetcher)

    assert sorted(fetcher.urls) == [f"https://example.invalid/good/{i}" for i in range(3)], (
        f"the surviving lane walked {fetcher.urls}: a crash in one source truncated another"
    )


# --- the connection budget ----------------------------------------------------


def test_the_lane_ceiling_leaves_every_lane_a_connection_to_write_through():
    """`PostgresSourceLock` holds one connection for a lane's whole walk, so lanes are connections in
    a way sequential sources never were. The pool has to outnumber them: at `lanes == POOL_SIZE`
    every slot is a held lock and the lanes' own workers -- sink and journal both -- wait out
    `pool_timeout` for a connection that only a finished lane can return."""
    assert storage_db.MAX_CONCURRENT_LANES >= 2, "one lane at a time is the sequential run again"
    assert storage_db.MAX_CONCURRENT_LANES < storage_db.POOL_SIZE, (
        f"{storage_db.MAX_CONCURRENT_LANES} lanes against a pool of {storage_db.POOL_SIZE}: the lock "
        "connections fill the pool and the run deadlocks on it instead of collecting"
    )


def test_two_overlapping_runs_fit_inside_the_roles_connection_limit():
    """The schedule puts two commerce runs on this role at once -- contracts/entrypoints.md §스케줄
    records the hourly ranking walk still running when 02:10 product and 04:15 review start, which is
    what the per-source advisory lock exists for. Both of them draw on `trend_radar_runtime`'s single
    CONNECTION LIMIT, so a per-process ceiling is only half the budget."""
    assert storage_db.OVERLAPPING_RUNS >= 2
    assert storage_db.POOL_SIZE * storage_db.OVERLAPPING_RUNS <= storage_db.ROLE_CONNECTION_LIMIT, (
        f"{storage_db.OVERLAPPING_RUNS} runs x {storage_db.POOL_SIZE} connections is more than "
        f"{storage_db.ROLE_CONNECTION_LIMIT}, and the loser gets FATAL: too many connections"
    )


def test_the_engine_pool_is_bounded_to_that_ceiling():
    """The numbers above are a budget only if the pool actually enforces them: SQLAlchemy's default
    is 5 plus 10 of overflow, which is twice the role's whole limit from one process."""
    engine = storage_db.create_engine("postgresql+psycopg://u:p@localhost:1/db")
    try:
        pool = engine.pool
        assert isinstance(pool, QueuePool), f"a {type(pool).__name__} bounds nothing"
        assert pool.size() == storage_db.POOL_SIZE
        assert pool._max_overflow == 0, "overflow puts the ceiling back where it was"
    finally:
        engine.dispose()


def test_the_cli_hands_the_engine_its_lane_ceiling():
    """`collect`'s `max_lanes` defaults to "every source at once", which is right for a test with no
    database behind it and wrong for the thing cron runs. Same shape as
    test_source_lock.py's check that no caller forgets `lock=`."""
    tree = ast.parse((REPO_ROOT / "collectors" / "commerce" / "cli.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("collect")
    ]
    assert calls, "no collect() call in cli.py; this check stopped seeing its subject"
    for call in calls:
        assert any(kw.arg == "max_lanes" for kw in call.keywords), (
            f"cli.py:{call.lineno} calls collect() without max_lanes: the run may open one "
            "connection per source with nothing capping them"
        )
