"""A real Chromium, for the source that answers plain HTTP with a wall.

origin: service/trend-radar/src/trend_radar/transport/browser.py -- de-asynced for #10.

What this is: a browser, driven honestly, at the pace the gate allows, against a profile a person
can authorise once by hand. What this is not, and will not become: a way past a challenge. There is
no fingerprint patching here, no proxy rotation, no stealth plugin. If Chromium is challenged, that
is reported as `ChallengeBlocked` and the source collects nothing this hour -- which is a result.
The alternative, a rendered interstitial reaching a parser that finds no products in it, is
indistinguishable afterwards from a quiet hour, and that is the failure this module is shaped to
prevent.

The de-async is not a mechanical strip of `await`. Playwright's sync API binds its dispatcher to the
thread that starts it, and `engine._Lane` hands this fetcher requests from `policy.concurrency`
worker threads and then closes it from the thread that ran the walk -- so every call into Playwright
is funnelled onto one thread this class owns. That also settles how many pages can be open at once:
one. Every open page is a renderer process, and the original's page pool existed to keep four
sources from holding four each; a single driver thread is a tighter bound than any policy shipped
here asks for (the one browser source runs at concurrency 1).

The context is created lazily. A Chromium started for a source that never navigates -- because the
run was blocked, or because its dataset was not the one being walked -- is a process tree for
nothing.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import TracebackType
from typing import Any, TypeVar

from collectors.commerce.contract import Fetch, Payload, SourcePolicy
from collectors.commerce.engine import ChallengeBlocked, TransientError
from collectors.commerce.transport.challenge import raise_for_refusal

ContextFactory = Callable[[], Any]

# Relative on purpose: the image's WORKDIR is the checkout, so a deployment mounts a volume over
# this path instead of the code having to know where that volume lives.
DEFAULT_PROFILE_DIR = Path("var/browser-profiles")

_T = TypeVar("_T")


class BrowserFetcher:
    def __init__(
        self,
        policy: SourcePolicy,
        source_key: str,
        context_factory: ContextFactory | None = None,
        profile_dir: Path | None = None,
        headless: bool = True,
    ) -> None:
        self._policy = policy
        self._source_key = source_key
        self._profile_dir = profile_dir or DEFAULT_PROFILE_DIR
        self._headless = headless
        self._factory = context_factory or self._launch_persistent_context
        self._context: Any | None = None
        self._playwright: Any | None = None
        # max_workers=1 is the whole threading contract, and a pool spawns nothing until the first
        # submit -- so constructing one of these still costs nothing.
        self._driver = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"browser-{source_key}")
        self._closed = False

    def __enter__(self) -> BrowserFetcher:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def fetch(self, fetch: Fetch) -> Payload:
        if fetch.method.upper() != "GET":
            # page.goto() is a navigation: it carries no method and no body. A POST routed here
            # would go out as a GET and come back 405, or worse, 200 with something else that the
            # parser then finds nothing in. Sources that need POST set `Fetch.transport` to HTTP.
            raise ValueError(
                f"the browser transport cannot send {fetch.method.upper()}; "
                "set Fetch.transport=Transport.HTTP for this request"
            )
        started = time.monotonic()
        status, headers, body = self._on_driver(lambda: self._navigate(fetch))
        # Outside the driver thread: classification is ours, not Playwright's, and running it here
        # keeps the driver holding nothing while an exception unwinds through the lane.
        raise_for_refusal(status, headers, body, source_key=self._source_key)
        return Payload(
            fetch=fetch,
            status=status,
            body=body.encode("utf-8"),
            final_url=fetch.url,
            headers=headers,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    def _on_driver(self, work: Callable[[], _T]) -> _T:
        """Run `work` on the one thread that is allowed to talk to Playwright, and re-raise whatever
        it raised in the caller's thread."""
        if self._closed:
            raise RuntimeError("this browser fetcher is closed")
        return self._driver.submit(work).result()

    def _navigate(self, fetch: Fetch) -> tuple[int, dict[str, str], str]:
        context = self._ensure_context()
        page = context.new_page()
        try:
            return self._render(page, fetch)
        finally:
            # Closed even when navigation failed. A page leaked per failure only shows up on a bad
            # day, which is the day the collector most needs to keep running.
            page.close()

    def _render(self, page: Any, fetch: Fetch) -> tuple[int, dict[str, str], str]:
        timeout_ms = int(self._policy.timeout_s * 1000)
        try:
            response = page.goto(fetch.url, wait_until="domcontentloaded", timeout=timeout_ms)
            if fetch.click_before is not None:
                self._click_if_needed(page, fetch, timeout_ms)
            if fetch.wait_for is not None:
                # A single-page app answers 200 with an empty shell; without this the parser is
                # handed the shell and reports nothing found.
                page.wait_for_selector(fetch.wait_for, timeout=timeout_ms)
            body = page.content()
        except ChallengeBlocked:
            raise
        except Exception as exc:  # noqa: BLE001 - playwright raises its own hierarchy
            raise TransientError(f"render failed: {exc}") from exc

        status = getattr(response, "status", 200) if response is not None else 200
        headers: dict[str, str] = {}
        if response is not None and hasattr(response, "all_headers"):
            headers = dict(response.all_headers())
        return status, headers, body

    def _click_if_needed(self, page: Any, fetch: Fetch, timeout_ms: int) -> None:
        """Click `fetch.click_before`, but only if `fetch.wait_for`'s target is not already visible.

        Some tabs and accordions render already-open for some products and collapsed for others;
        clicking a toggle that is already open is how a click meant to reveal a section instead
        hides it. Checking first is the only way to make one `Fetch` correct for both cases.
        """
        if fetch.wait_for is not None:
            target = page.locator(fetch.wait_for).first
            if target.count() > 0 and target.is_visible():
                return  # already open; a click here would toggle it shut
        trigger = page.locator(fetch.click_before).first
        if trigger.count() == 0:
            return  # nothing to click; let wait_for below report the real problem
        trigger.click(timeout=timeout_ms)

    def _ensure_context(self) -> Any:
        # Caller is always the driver thread, which is why no lock guards this.
        if self._context is None:
            self._context = self._factory()
        return self._context

    def _launch_persistent_context(self) -> Any:
        from playwright.sync_api import sync_playwright

        # Persistent, and one directory per source: cookies a person accepted for one site are not
        # ours to send to another. The site refreshes them mid-session, so this is read-write.
        profile = self._profile_dir / self._source_key
        profile.mkdir(parents=True, exist_ok=True)

        self._playwright = sync_playwright().start()
        return self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=self._headless,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            # The only place a browser request can be told who we are: a navigation carries no
            # `fetch.headers`, so the engine's stamp cannot reach it. Same value either way.
            user_agent=self._policy.user_agent,
        )

    def close(self) -> None:
        if self._closed:
            return
        started = self._context is not None or self._playwright is not None
        if started:
            self._driver.submit(self._shutdown).result()
        self._closed = True
        self._driver.shutdown(wait=True)

    def _shutdown(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
