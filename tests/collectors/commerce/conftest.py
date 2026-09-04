"""A Playwright that never launches, shared by every browser-transport test.

origin: service/trend-radar/tests/transport/test_browser.py's fakes -- de-asynced for #10. What the
browser tests assert is *our* policy (what counts as a refusal, whether a page is closed when
navigation dies, when a Chromium is started at all); a real browser would make those assertions slow
and occasionally wrong for reasons that have nothing to do with the policy. Whether Chromium
actually gets past oliveyoung's challenge is not a question a test can answer -- that is the shadow
run (#10 condition 3).

The fixture owns closing: `BrowserFetcher` holds a thread, and a test that forgot to close one would
leak it into every test that runs after.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest

from collectors.commerce.contract import SourcePolicy, Transport
from collectors.commerce.transport.browser import BrowserFetcher


@dataclass
class FakeResponse:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)

    def all_headers(self) -> dict[str, str]:
        return dict(self.headers)


@dataclass
class FakeLocator:
    page: FakePage
    selector: str

    @property
    def first(self) -> FakeLocator:
        return self

    def count(self) -> int:
        return 1 if self.selector in (self.page.visible | self.page.hidden) else 0

    def is_visible(self) -> bool:
        return self.selector in self.page.visible

    def click(self, timeout: float | None = None) -> None:
        del timeout
        self.page.clicked.append(self.selector)


@dataclass
class FakePage:
    body: str = "<html><body>ok</body></html>"
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    goto_error: BaseException | None = None
    # Selectors the rendered page holds, split by whether they are visible: a collapsed disclosure
    # section exists in the markup and is not visible.
    visible: frozenset[str] = frozenset()
    hidden: frozenset[str] = frozenset()
    waited_for: list[str] = field(default_factory=list)
    clicked: list[str] = field(default_factory=list)
    located: list[str] = field(default_factory=list)
    closed: bool = False

    def goto(self, url: str, **kwargs: object) -> FakeResponse:
        del url, kwargs
        if self.goto_error is not None:
            raise self.goto_error
        return FakeResponse(self.status, dict(self.headers))

    def locator(self, selector: str) -> FakeLocator:
        self.located.append(selector)
        return FakeLocator(self, selector)

    def wait_for_selector(self, selector: str, **kwargs: object) -> None:
        del kwargs
        self.waited_for.append(selector)

    def content(self) -> str:
        return self.body

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeContext:
    template: FakePage = field(default_factory=FakePage)
    pages: list[FakePage] = field(default_factory=list)
    closed: bool = False
    # How many times the fetcher asked for a context. A Chromium per run for a source that never
    # navigates is the cost this counter exists to catch.
    starts: int = 0
    # Which threads reached Playwright. The sync API binds its dispatcher to one, so more than one
    # here is the production-only failure the fetcher's own thread exists to prevent.
    threads: set[int] = field(default_factory=set)

    def new_page(self) -> FakePage:
        self.threads.add(threading.get_ident())
        page = FakePage(
            body=self.template.body,
            status=self.template.status,
            headers=dict(self.template.headers),
            goto_error=self.template.goto_error,
            visible=self.template.visible,
            hidden=self.template.hidden,
        )
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


MakeBrowser = Callable[..., "tuple[BrowserFetcher, FakeContext]"]


@pytest.fixture
def make_browser() -> Iterator[MakeBrowser]:
    opened: list[BrowserFetcher] = []

    def build(
        page: dict[str, object] | None = None, **policy_overrides: object
    ) -> tuple[BrowserFetcher, FakeContext]:
        """`page` is what the rendered page answers with (FakePage's fields); the rest is policy."""
        context = FakeContext(template=FakePage(**(page or {})))  # pyright: ignore[reportArgumentType]
        kwargs: dict[str, object] = {
            "min_interval_s": 0.0,
            "concurrency": 1,
            "timeout_s": 1.0,
            "transport": Transport.BROWSER,
        }
        kwargs.update(policy_overrides)

        def factory() -> FakeContext:
            context.starts += 1
            context.threads.add(threading.get_ident())
            return context

        fetcher = BrowserFetcher(
            SourcePolicy(**kwargs),  # pyright: ignore[reportArgumentType]
            source_key="probe",
            context_factory=factory,
        )
        opened.append(fetcher)
        return fetcher, context

    yield build
    for fetcher in opened:
        fetcher.close()
