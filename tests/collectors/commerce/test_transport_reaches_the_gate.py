"""The transport and the gate, together, over a mocked wire.

Every other file here asserts one half. The value of this one is the seam between them, which is
where #10's whole point sits: a `Retry-After` the site sent has to survive being turned into an
exception, caught by `_fetch_with_retries`, handed to `Gate.observe` and applied to the live
interval. Each of those steps is asserted somewhere; that they are wired to each other is not, and a
transport that dropped the header would still pass every unit test in this directory.

Nothing here opens a socket: `httpx.MockTransport` answers, and the clock is injected so a widened
interval costs the suite nothing.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import ClassVar

import httpx
import pytest

from collectors.commerce.contract import DEFAULT_UA, Fetch, Payload, Scope, Source, SourcePolicy, Yield
from collectors.commerce.engine import collect, exit_code_for
from collectors.commerce.models import Dataset
from collectors.commerce.transport.factory import LiveFetchers

AT = datetime(2026, 8, 24, 3, tzinfo=UTC)
URL = "https://site.invalid/rank"


class _FakeClock:
    """A clock that only moves when someone sleeps, so a widened interval costs no wall time."""

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


class _NullSink:
    def write(self, records) -> None:
        return None


def _source(source_policy: SourcePolicy, seed_count: int = 1) -> Source:
    class _FakeSource:
        key: ClassVar[str] = "wired"
        datasets: ClassVar[frozenset[Dataset]] = frozenset({Dataset.RANKING})
        # 이 가짜 소스는 리뷰를 걷지 않는다 -- 빈 값이 답이다(#144 의 review_body_datasets).
        review_body_datasets: ClassVar[frozenset[Dataset]] = frozenset()
        scope: ClassVar[Scope] = {Dataset.RANKING: {"seeds": seed_count}}
        policy: ClassVar[SourcePolicy] = source_policy

        def seeds(self, dataset: Dataset, *, board: str | None = None) -> Sequence[Fetch]:
            del board
            return tuple(Fetch(url=f"{URL}/{i}", dataset=dataset) for i in range(seed_count))

        def parse(self, payload: Payload) -> Yield:
            del payload
            return Yield()

    return _FakeSource()


def _walk(policy: SourcePolicy, handler, seed_count: int = 1):
    clock = _FakeClock()
    requests: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    live = LiveFetchers(http_transport=httpx.MockTransport(recording))
    try:
        report = collect(
            sources=[_source(policy, seed_count)],
            dataset=Dataset.RANKING,
            sink=_NullSink(),
            captured_at=AT,
            fetcher=live,
            clock=clock.time,
            sleep=clock.sleep,
        )
    finally:
        live.close()
    return report, requests, clock


@pytest.mark.parametrize(
    "headers,expected_interval",
    [
        ({"Retry-After": "12"}, 12.0),
        # Without the header the gate falls back to doubling its own interval. Both branches, so a
        # transport that dropped Retry-After entirely would fail the first case and not the second.
        ({}, 2.0),
    ],
    ids=["server-said-12s", "no-header-so-doubling"],
)
def test_a_retry_after_on_the_wire_widens_the_live_interval(headers, expected_interval):
    # One attempt, so exactly one back-off is priced. `Gate._back_off` doubles on every refusal, and
    # a second attempt here would fold two of them together and hide which number did the widening.
    policy = SourcePolicy(min_interval_s=1.0, concurrency=1, max_attempts=1)
    report, requests, _ = _walk(policy, lambda _: httpx.Response(429, headers=headers))
    source = report.sources["wired"]
    assert len(requests) == 1
    assert source.configured_interval_s == 1.0
    assert source.final_interval_s == expected_interval


def test_a_rate_limited_request_is_retried_at_the_widened_pace():
    # The other half: the transport's RateLimited is what `_fetch_with_retries` retries on, and each
    # attempt is charged another back-off.
    policy = SourcePolicy(min_interval_s=1.0, concurrency=1, max_attempts=3)
    report, requests, clock = _walk(policy, lambda _: httpx.Response(429, headers={"Retry-After": "12"}))
    source = report.sources["wired"]
    assert len(requests) == 3
    assert source.retries == 2
    assert source.final_interval_s == 48.0, "12 -> 24 -> 48: the doubling outgrows the header"
    assert max(clock.sleeps) >= 12.0, "the widened interval is what the worker actually waited"


def test_a_challenge_stops_the_source_without_a_second_request():
    """Retrying a challenge is how a soft block becomes a hard one. The honest outcome is a run that
    says this source collected nothing and why -- never an empty result that reads like a quiet
    hour, and never exit 1, which is the code that means "look at the parser"."""
    policy = SourcePolicy(min_interval_s=0.0, concurrency=1, max_attempts=3)
    report, requests, _ = _walk(
        policy,
        lambda _: httpx.Response(200, headers={"cf-mitigated": "challenge"}, text="<html/>"),
        seed_count=3,
    )
    source = report.sources["wired"]
    assert len(requests) == 1, f"a challenge must not be retried; {len(requests)} requests went out"
    assert source.blocked_reason is not None
    assert "challenge" in source.blocked_reason
    assert report.blocked == ["wired"]
    assert exit_code_for(report) == 2


def test_a_bare_403_stops_the_source_the_same_way_a_challenge_page_does():
    # An edge can answer 403 to every path. Counting that as one dropped URL would leave a fully
    # refused run looking like a healthy one with some broken links.
    policy = SourcePolicy(min_interval_s=0.0, concurrency=1)
    report, requests, _ = _walk(policy, lambda _: httpx.Response(403, text="403 Forbidden"), seed_count=3)
    assert len(requests) == 1
    assert exit_code_for(report) == 2


def test_a_404_costs_one_url_and_leaves_the_run_partial_rather_than_blocked():
    policy = SourcePolicy(min_interval_s=0.0, concurrency=1)
    report, requests, _ = _walk(policy, lambda _: httpx.Response(404), seed_count=2)
    source = report.sources["wired"]
    assert len(requests) == 2, "a 404 is about one page, so the walk carries on"
    assert source.blocked_reason is None
    assert len(source.errors) == 2
    assert exit_code_for(report) == 1


def test_the_user_agent_the_policy_declares_is_what_the_site_sees():
    """The engine stamps it and the transport has to forward it. Without both halves the request
    goes out as `python-httpx/...`, which is a crawler a site operator cannot look up or ask to
    stop."""
    policy = SourcePolicy(min_interval_s=0.0, concurrency=1)
    _, requests, _ = _walk(policy, lambda _: httpx.Response(200, text="{}"))
    assert requests[-1].headers["user-agent"] == DEFAULT_UA
    assert DEFAULT_UA == policy.user_agent
    # Comparing the symbol to itself proves nothing about the string it holds. Pin the literal -- the
    # name this crawler answers to in an access log -- so an edit to the constant fails this
    # assertion instead of sailing through unnoticed.
    assert DEFAULT_UA == "cosmai-commerce/0.1 (+https://github.com/slopindustries/cosmai)"


def test_a_good_walk_over_the_real_transport_exits_zero():
    policy = SourcePolicy(min_interval_s=0.0, concurrency=1)
    report, requests, _ = _walk(policy, lambda _: httpx.Response(200, text="{}"), seed_count=3)
    assert len(requests) == 3
    assert exit_code_for(report) == 0
