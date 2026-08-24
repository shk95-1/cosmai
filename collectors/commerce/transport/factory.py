"""Which fetcher a request gets, and which source a fetcher belongs to.

origin: service/trend-radar/src/trend_radar/transport/factory.py -- de-asynced for #10, plus
`LiveFetchers`, which the original had no need for: its CLI built one fetcher per source because it
ran a lane per source, while `engine.collect` here walks sources in sequence behind a single
`fetcher` argument. That argument is why `engine.FetcherFor` exists.

A source is not always one transport. oliveyoung's ranking sits behind a Cloudflare challenge and
needs a real browser; its review API is on a host that answers plain HTTP and only accepts POST,
which a browser navigation cannot send at all. So `Fetch.transport` overrides the source's default,
and `DispatchingFetcher` routes on it.

Both are built lazily. A browser for a source that never asks for one is a Chromium per run for
nothing.
"""

from __future__ import annotations

import threading
from pathlib import Path

import httpx

from collectors.commerce.contract import Fetch, Payload, Source, SourcePolicy, Transport
from collectors.commerce.engine import Fetcher
from collectors.commerce.transport.browser import BrowserFetcher
from collectors.commerce.transport.http import HttpFetcher


class DispatchingFetcher:
    """Holds at most one fetcher per transport and routes each request to it."""

    def __init__(
        self,
        policy: SourcePolicy,
        source_key: str | None = None,
        profile_dir: Path | None = None,
        headless: bool = True,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        if policy.transport is Transport.BROWSER and source_key is None:
            # Fails here rather than at first fetch: a run should not spend its other sources'
            # politeness budget before finding this out.
            raise ValueError("a browser fetcher needs a source_key to pick its profile directory")
        self._policy = policy
        self._source_key = source_key
        self._profile_dir = profile_dir
        self._headless = headless
        self._http_transport = http_transport
        self._fetchers: dict[Transport, Fetcher] = {}
        # A lane runs `policy.concurrency` workers over one of these, so two of them can reach a
        # cold transport at the same moment; without this they build two clients and one is dropped.
        self._lock = threading.Lock()

    def pick(self, fetch: Fetch) -> Fetcher:
        transport = fetch.transport or self._policy.transport
        with self._lock:
            existing = self._fetchers.get(transport)
            if existing is not None:
                return existing
            built = self._build(transport)
            self._fetchers[transport] = built
            return built

    def _build(self, transport: Transport) -> Fetcher:
        if transport is Transport.HTTP:
            return HttpFetcher(self._policy, transport=self._http_transport)
        if self._source_key is None:
            # Profiles are per source; a shared one would send a site's cookies to another site.
            raise ValueError("a browser fetcher needs a source_key to pick its profile directory")
        return BrowserFetcher(
            self._policy,
            source_key=self._source_key,
            profile_dir=self._profile_dir,
            headless=self._headless,
        )

    def built(self) -> set[Transport]:
        with self._lock:
            return set(self._fetchers)

    def fetch(self, fetch: Fetch) -> Payload:
        return self.pick(fetch).fetch(fetch)

    def close(self) -> None:
        with self._lock:
            built = list(self._fetchers.values())
            self._fetchers.clear()
        for fetcher in built:
            fetcher.close()  # pyright: ignore[reportAttributeAccessIssue]


def build_fetcher(
    policy: SourcePolicy,
    source_key: str | None = None,
    profile_dir: Path | None = None,
    headless: bool = True,
    http_transport: httpx.BaseTransport | None = None,
) -> DispatchingFetcher:
    return DispatchingFetcher(
        policy,
        source_key=source_key,
        profile_dir=profile_dir,
        headless=headless,
        http_transport=http_transport,
    )


class LiveFetchers:
    """The run's whole transport: one `DispatchingFetcher` per source, built as each is reached.

    This is what `engine.collect` calls when it is handed a `FetcherFor` rather than a `Fetcher`.
    One per source and not one for the run, because the things a fetcher is configured from are the
    source's: `SourcePolicy` carries the timeout and the user agent, and a browser profile belongs
    to exactly one site. `close` is the caller's to make -- `collectors/commerce/cli.py` does it in
    a `finally`, which is what keeps a Chromium from outliving the walk that started it.

    `http_transport` is a seam for the tests, which drive whole runs through `httpx.MockTransport`;
    production leaves it None and gets a real client.
    """

    def __init__(
        self,
        profile_dir: Path | None = None,
        headless: bool = True,
        http_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._profile_dir = profile_dir
        self._headless = headless
        self._http_transport = http_transport
        self._by_source: dict[str, DispatchingFetcher] = {}

    def __call__(self, source: Source) -> Fetcher:
        existing = self._by_source.get(source.key)
        if existing is not None:
            return existing
        built = build_fetcher(
            source.policy,
            source_key=source.key,
            profile_dir=self._profile_dir,
            headless=self._headless,
            http_transport=self._http_transport,
        )
        self._by_source[source.key] = built
        return built

    def close(self) -> None:
        for fetcher in self._by_source.values():
            fetcher.close()
        self._by_source.clear()
