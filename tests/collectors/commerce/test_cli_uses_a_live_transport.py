"""The CLI's default fetcher is a real one now.

`collectors/commerce/cli.py` shipped a `_RaisingFetcher` from #7 to #10: every scheduled run ended in
`NotImplementedError` and the six cron lines in stack/docker-compose.yml were wired to a collector
that could not collect. This asserts the replacement is live *and* that every registered source can
actually be given one -- a source declaring `Transport.BROWSER` without the key its profile
directory needs fails at build time by design, and the place that would discover it at 03:00 is
here instead.

No socket and no Chromium: building a fetcher opens neither, which is the same laziness
test_transport_dispatch.py pins.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from collectors.commerce import cli
from collectors.commerce import sources as _sources  # noqa: F401 -- import registers every source
from collectors.commerce.contract import Transport
from collectors.commerce.engine import Fetcher, PermanentError
from collectors.commerce.registry import SOURCES
from collectors.commerce.transport.browser import BrowserFetcher
from collectors.commerce.transport.factory import DispatchingFetcher, LiveFetchers
from collectors.commerce.transport.http import HttpFetcher

LIVE = (HttpFetcher, BrowserFetcher)
AT = datetime(2026, 8, 24, 3, tzinfo=UTC)


def test_the_default_fetcher_is_a_live_transport():
    live = cli.live_fetchers()
    try:
        assert isinstance(live, LiveFetchers)
    finally:
        live.close()


def test_nothing_is_left_that_refuses_to_fetch():
    assert not hasattr(cli, "_RaisingFetcher"), "the #7 placeholder is what #10 replaces"


def test_every_registered_source_gets_a_fetcher_for_its_own_first_request():
    live = cli.live_fetchers()
    try:
        assert SOURCES, "the registry is empty; this would assert nothing"
        for key in sorted(SOURCES):
            source = SOURCES[key]()
            dispatcher = live(source)
            assert isinstance(dispatcher, DispatchingFetcher), key
            assert isinstance(dispatcher, Fetcher), key
            dataset = sorted(source.datasets, key=lambda d: d.value)[0]
            seeds = source.seeds(dataset, board=None) or source.seeds(dataset, board="suncare")
            assert seeds, f"{key} declares {dataset.value} but seeds nothing"
            picked = dispatcher.pick(seeds[0])
            assert isinstance(picked, LIVE), f"{key} got {type(picked).__name__}"
    finally:
        live.close()


def test_the_one_browser_source_is_the_one_the_registry_declares():
    # A second source quietly moving to BROWSER doubles the image's Chromium bill and needs a
    # profile directory of its own; it should be a decision, not a diff nobody read.
    browser = {key for key, cls in SOURCES.items() if cls.policy.transport is Transport.BROWSER}
    assert browser == {"oliveyoung"}


# --- and the run closes what it built --------------------------------------------------------------


class _RefusingFetcher:
    """Answers every request with a per-page error. No socket, and the walk still finishes."""

    def fetch(self, fetch):
        raise PermanentError("nothing here", status=404)

    def close(self) -> None:
        return None


class _RecordingFetchers(LiveFetchers):
    """Stands in for the transport `cli.live_fetchers()` builds, and remembers being closed."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def __call__(self, source) -> Fetcher:
        del source
        return _RefusingFetcher()

    def close(self) -> None:
        self.closed = True


@pytest.mark.postgres
def test_the_run_closes_the_transport_it_built(trend_radar_schema: str, monkeypatch):
    """A Chromium outliving the walk is a process tree per hour on a machine nobody is watching, and
    a `cosmai collect` that returns rather than exits leaves the httpx pool to whoever called it."""
    recorder = _RecordingFetchers()
    monkeypatch.setattr(cli, "live_fetchers", lambda: recorder)
    code = cli.run("product", database_url=trend_radar_schema, captured_at=AT)
    assert code == 1, "every request was refused, so the run is partial"
    assert recorder.closed


@pytest.mark.postgres
def test_the_run_closes_the_transport_even_when_the_walk_dies(trend_radar_schema: str, monkeypatch):
    class _Exploding(_RecordingFetchers):
        def __call__(self, source) -> Fetcher:
            del source

            class _Boom:
                def fetch(self, fetch):
                    raise ZeroDivisionError("a bug, not a site")

            return _Boom()

    recorder = _Exploding()
    monkeypatch.setattr(cli, "live_fetchers", lambda: recorder)
    with pytest.raises(ZeroDivisionError):
        cli.run("product", database_url=trend_radar_schema, captured_at=AT)
    assert recorder.closed


@pytest.mark.postgres
def test_an_injected_fetcher_is_the_callers_to_close(trend_radar_schema: str):
    # The other side of the same rule: a caller that hands in a fetcher keeps owning it, and one
    # closed out from under a caller that meant to reuse it fails on the *next* run, not this one.
    class _Owned(_RefusingFetcher):
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    owned = _Owned()
    cli.run("product", database_url=trend_radar_schema, fetcher=owned, captured_at=AT)
    assert not owned.closed
