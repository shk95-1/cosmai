"""Which fetcher a request gets, and what a walk costs when it never needs a browser.

origin: service/trend-radar/tests/transport/test_dispatch.py, extended for #10 with the two claims
the port has to make on its own: that a source needing neither transport builds neither, and that
one `collect` run can hand different sources different transports at all.

A source is not one transport. oliveyoung's ranking sits behind a Cloudflare challenge and needs a
real browser; its review API is on a host that answers plain HTTP and only accepts POST, which a
browser navigation cannot send. So `Fetch.transport` overrides `SourcePolicy.transport`, and this is
what dispatches on it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from collectors.commerce import sources as _sources  # noqa: F401 -- import registers every source
from collectors.commerce.contract import Fetch, Source, SourcePolicy, Transport
from collectors.commerce.engine import collect
from collectors.commerce.models import Dataset
from collectors.commerce.registry import SOURCES
from collectors.commerce.sources.oliveyoung import _review_fetch  # pyright: ignore[reportPrivateUsage]
from collectors.commerce.transport import factory as factory_module
from collectors.commerce.transport.browser import BrowserFetcher
from collectors.commerce.transport.factory import LiveFetchers, build_fetcher
from collectors.commerce.transport.http import HttpFetcher

BROWSER_POLICY = SourcePolicy(min_interval_s=0.0, concurrency=1, transport=Transport.BROWSER)
HTTP_POLICY = SourcePolicy(min_interval_s=0.0, concurrency=1)
AT = datetime(2026, 8, 24, 3, tzinfo=UTC)


def _fetch(**kw: object) -> Fetch:
    kwargs: dict[str, object] = {"url": "https://x/1", "dataset": Dataset.RANKING}
    kwargs.update(kw)
    return Fetch(**kwargs)  # pyright: ignore[reportArgumentType]


def test_a_fetch_with_no_transport_uses_the_source_default():
    fetcher = build_fetcher(BROWSER_POLICY, source_key="oliveyoung")
    try:
        assert isinstance(fetcher.pick(_fetch()), BrowserFetcher)
    finally:
        fetcher.close()


def test_a_fetch_may_ask_for_http_even_on_a_browser_source():
    fetcher = build_fetcher(BROWSER_POLICY, source_key="oliveyoung")
    try:
        assert isinstance(fetcher.pick(_fetch(transport=Transport.HTTP)), HttpFetcher)
    finally:
        fetcher.close()


def test_a_fetch_may_ask_for_a_browser_on_an_http_source():
    fetcher = build_fetcher(HTTP_POLICY, source_key="hwahae")
    try:
        assert isinstance(fetcher.pick(_fetch(transport=Transport.BROWSER)), BrowserFetcher)
    finally:
        fetcher.close()


def test_the_transport_a_request_did_not_ask_for_is_never_built():
    fetcher = build_fetcher(HTTP_POLICY, source_key="hwahae")
    try:
        fetcher.pick(_fetch())
        assert fetcher.built() == {Transport.HTTP}
    finally:
        fetcher.close()


def test_one_fetcher_per_transport_however_many_requests_ask_for_it():
    fetcher = build_fetcher(BROWSER_POLICY, source_key="oliveyoung")
    try:
        first = fetcher.pick(_fetch())
        assert fetcher.pick(_fetch(url="https://x/2")) is first
    finally:
        fetcher.close()


def test_oliveyoungs_real_shape_needs_both_transports_in_one_walk():
    """Not a hypothetical: the shipped source declares BROWSER and then routes its POST-only review
    API back over HTTP with `Fetch.transport`. Read from the registry so a source that stopped doing
    either is caught here rather than at 03:00."""
    source = SOURCES["oliveyoung"]()
    assert source.policy.transport is Transport.BROWSER
    seeds = source.seeds(Dataset.RANKING)
    assert seeds, "oliveyoung declares a ranking walk"
    assert seeds[0].transport is None, "the ranking takes the source default, which is the browser"

    fetcher = build_fetcher(source.policy, source_key=source.key)
    try:
        fetcher.pick(seeds[0])
        fetcher.pick(_review_fetch("A000000", "RECENT"))
        assert fetcher.built() == {Transport.HTTP, Transport.BROWSER}
    finally:
        fetcher.close()


def test_the_review_api_that_only_accepts_post_asks_for_http_by_name():
    review = _review_fetch("A000000", "RECENT")
    assert review.method == "POST"
    assert review.transport is Transport.HTTP, "a POST cannot be a navigation"
    fetcher = build_fetcher(SOURCES["oliveyoung"].policy, source_key="oliveyoung")
    try:
        assert isinstance(fetcher.pick(review), HttpFetcher)
    finally:
        fetcher.close()


def test_a_browser_source_without_a_key_fails_at_build_time_not_at_first_fetch():
    # A run should not spend its other sources' politeness budget before finding this out.
    with pytest.raises(ValueError, match="source_key"):
        build_fetcher(BROWSER_POLICY)


def test_an_http_source_that_asks_for_a_browser_without_a_key_still_fails():
    fetcher = build_fetcher(HTTP_POLICY)
    try:
        with pytest.raises(ValueError, match="source_key"):
            fetcher.pick(_fetch(transport=Transport.BROWSER))
    finally:
        fetcher.close()


def test_an_http_source_needs_no_source_key_at_all():
    fetcher = build_fetcher(HTTP_POLICY)
    try:
        assert isinstance(fetcher.pick(_fetch()), HttpFetcher)
    finally:
        fetcher.close()


def test_closing_the_dispatcher_closes_everything_it_built():
    # Not just "forgets": a Chromium is a process tree and an httpx client holds sockets, and the
    # cron container runs one of these per source every hour. Both refuse work once closed, which is
    # the observable difference between closing them and dropping the references.
    fetcher = build_fetcher(BROWSER_POLICY, source_key="oliveyoung")
    browser = fetcher.pick(_fetch())
    http = fetcher.pick(_fetch(transport=Transport.HTTP))
    fetcher.close()
    assert fetcher.built() == set()
    for closed in (browser, http):
        with pytest.raises(RuntimeError):
            closed.fetch(_fetch())


def test_closing_the_run_closes_every_source_fetcher():
    live = LiveFetchers()
    picked = [live(SOURCES[key]()).pick(_fetch()) for key in ("hwahae", "oliveyoung")]  # pyright: ignore[reportAttributeAccessIssue]
    live.close()
    for closed in picked:
        with pytest.raises(RuntimeError):
            closed.fetch(_fetch())


# --- what a walk that needs no browser costs ------------------------------------------------------


class _ExplodingBrowser:
    """Stands in for `BrowserFetcher` in the one test that must never reach it."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("a source that never asks for a browser must not build one")


def _http_only_source(policy: SourcePolicy) -> Source:
    class _FakeSource:
        key = "fake-http"
        datasets = frozenset({Dataset.RANKING})
        scope = {Dataset.RANKING: {"seeds": 1}}

        def seeds(self, dataset: Dataset, *, board: str | None = None):
            del board
            return (Fetch(url="https://site.invalid/rank", dataset=dataset),)

        def parse(self, payload):
            del payload
            from collectors.commerce.contract import Yield

            return Yield()

    _FakeSource.policy = policy  # pyright: ignore[reportAttributeAccessIssue]
    return _FakeSource()  # pyright: ignore[reportReturnType]


class _NullSink:
    def write(self, records) -> None:
        return None


def test_a_walk_of_http_only_sources_starts_no_chromium(monkeypatch):
    """Every Chromium is a process tree, and the four commerce sources run in one container. A
    dispatcher that built a browser eagerly would pay for one on every source, including the three
    that answer plain HTTP."""
    monkeypatch.setattr(factory_module, "BrowserFetcher", _ExplodingBrowser)
    live = LiveFetchers(http_transport=httpx.MockTransport(lambda _: httpx.Response(200, text="{}")))
    source = _http_only_source(HTTP_POLICY)
    try:
        report = collect(
            sources=[source],
            dataset=Dataset.RANKING,
            sink=_NullSink(),
            captured_at=AT,
            fetcher=live,
            sleep=lambda _: None,
        )
    finally:
        live.close()
    # The lane really walked: without this, a `collect` that returned before touching the transport
    # would satisfy the claim by doing nothing.
    assert report.sources["fake-http"].requests == 1


def test_the_same_run_hands_different_sources_different_transports():
    live = LiveFetchers()
    try:
        http_side = live(_http_only_source(HTTP_POLICY))
        browser_side = live(SOURCES["oliveyoung"]())
        assert isinstance(http_side.pick(_fetch()), HttpFetcher)  # pyright: ignore[reportAttributeAccessIssue]
        assert isinstance(browser_side.pick(_fetch()), BrowserFetcher)  # pyright: ignore[reportAttributeAccessIssue]
    finally:
        live.close()


def test_one_fetcher_per_source_so_a_profile_is_never_shared():
    live = LiveFetchers()
    try:
        oliveyoung = SOURCES["oliveyoung"]()
        assert live(oliveyoung) is live(SOURCES["oliveyoung"]())
        assert live(oliveyoung) is not live(_http_only_source(HTTP_POLICY))
    finally:
        live.close()
