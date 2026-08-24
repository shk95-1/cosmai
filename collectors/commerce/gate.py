"""How fast one source is allowed to go, and how that changes under refusal.

origin: service/trend-radar/src/trend_radar/engine/gate.py -- ported for #10 §A-1, asyncio primitives
swapped for threading ones. #7 carried the four sources' policy numbers across with a diff of zero
against the original and nothing that reads them; this is the code that reads them.

Two limits apply at once, and they answer different questions:

  - a concurrency slot  -- how many requests may be in flight together
  - a token bucket      -- how many requests may be started per unit of time

Concurrency alone would let two workers fire two requests in the same millisecond and then idle;
pacing alone would let a single slow response pile up behind itself. Sites care about both.

The gate adapts rather than evades: it starts at the policy's numbers, widens whenever the server says
it is unhappy, and creeps back afterwards. Over an hour that collects more than hammering does,
because the run that gets blocked collects nothing at all.

The clock and sleep are injected so tests can assert on a 30-second interval without spending 30
seconds, and so the numbers under test are the ones the sources actually ship.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from collectors.commerce.contract import SourcePolicy

# Statuses that say something about *us* rather than about the site. A 500 is the site having a bad
# time and slowing down will not help it; a 429, a 503 or a refusal is the site declining to serve
# this client at this rate.
_BACKOFF_STATUSES = frozenset({403, 429, 503})


@dataclass(frozen=True, slots=True)
class GateState:
    interval_s: float
    concurrency: int


class Gate:
    # A cap on the doubling, so a run of refusals cannot park a source at an interval longer than the
    # hour it is scheduled in. An explicit Retry-After is honoured past this: that number came from
    # the server.
    MAX_INTERVAL_S = 300.0

    # How many consecutive good responses buy one step back toward the policy. One is too few -- the
    # response right after a 429 is often fine and proves nothing.
    RECOVER_AFTER = 5

    def __init__(
        self,
        policy: SourcePolicy,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._policy = policy
        self._clock = clock
        self._sleep = sleep

        # Two locks, and the split is not arbitrary. `_slots` owns `_interval`, `_limit`,
        # `_in_flight` and `_consecutive_ok`; `_pace` owns `_tokens` and `_last_refill`. `_interval`
        # is the one that crosses -- `_refill` and `_time_to_next_token` read it under `_pace`
        # instead -- so that a worker asleep on the pace lock can never stop `observe()` from
        # widening the interval, which is the moment widening matters most. A float read is atomic
        # under the GIL: the reader gets the old value or the new one, never a torn one, and a widen
        # that lands mid-wait takes effect from the next token rather than the one being waited on.
        self._interval = policy.min_interval_s
        self._limit = policy.concurrency
        self._in_flight = 0
        self._slots = threading.Condition()

        self._tokens = float(policy.burst)
        self._last_refill = clock()
        self._pace = threading.Lock()

        self._consecutive_ok = 0

    def snapshot(self) -> GateState:
        with self._slots:
            return GateState(self._interval, self._limit)

    @contextmanager
    def acquire(self) -> Iterator[None]:
        # Slot first, then token. The other order would spend a token and then sit waiting for a slot,
        # which is a request's worth of politeness budget burnt on nothing.
        self._take_slot()
        try:
            self._take_token()
            yield
        finally:
            self._release_slot()

    def observe(self, status: int, retry_after: float | None = None, challenged: bool = False) -> None:
        """Feed one response back into the pacing. Never blocks, so a worker can report a result
        without first winning the pace lock it is about to queue behind."""
        if challenged or status in _BACKOFF_STATUSES:
            self._back_off(retry_after)
        elif 200 <= status < 400:
            self._recover()

    def _back_off(self, retry_after: float | None) -> None:
        with self._slots:
            doubled = min(self._interval * 2 or 1.0, self.MAX_INTERVAL_S)
            if retry_after is not None:
                doubled = max(doubled, retry_after)
            self._interval = max(self._policy.min_interval_s, doubled)
            self._limit = max(1, self._limit // 2)
            self._consecutive_ok = 0

    def _recover(self) -> None:
        with self._slots:
            self._consecutive_ok += 1
            if self._consecutive_ok < self.RECOVER_AFTER:
                return
            self._consecutive_ok = 0
            # Additive: give back one step of interval and one worker. The step is the policy's own
            # interval, floored so a source configured at zero can still climb out of a backoff.
            step = max(self._policy.min_interval_s, 0.5)
            self._interval = max(self._policy.min_interval_s, self._interval - step)
            if self._limit < self._policy.concurrency:
                self._limit += 1
                self._slots.notify()

    def _take_slot(self) -> None:
        with self._slots:
            while self._in_flight >= self._limit:
                self._slots.wait()
            self._in_flight += 1

    def _release_slot(self) -> None:
        with self._slots:
            self._in_flight -= 1
            self._slots.notify()

    def _take_token(self) -> None:
        # Held across the sleep on purpose: pacing is a property of the source, not of one worker, so
        # waiters queue here rather than each waiting out the full interval in parallel and then all
        # firing at once.
        with self._pace:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                self._sleep(self._time_to_next_token())

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        self._last_refill = now
        if self._interval <= 0:
            self._tokens = float(self._policy.burst)
            return
        self._tokens = min(float(self._policy.burst), self._tokens + elapsed / self._interval)

    def _time_to_next_token(self) -> float:
        # Known limit: a widened interval -- an explicit Retry-After included -- reaches the wait only
        # through this multiplication, so it delays the *next* token and nothing else. A source at
        # burst > 1 with tokens still banked would fire them back to back straight after a 429,
        # honouring the new interval only once the bank ran dry. All four shipped sources take the
        # default burst of 1 (contract.SourcePolicy), where there is never anything banked; raising
        # one means making Retry-After spend the bank here too.
        if self._interval <= 0:
            return 0.0
        return (1.0 - self._tokens) * self._interval


__all__ = ["Gate", "GateState"]
