"""The browser transport, driven against a Playwright that never launches.

origin: service/trend-radar/tests/transport/test_browser.py -- de-asynced for #10. The fakes live in
tests/collectors/commerce/conftest.py because the status table (test_transport_status_mapping.py)
drives them too.

The de-async is not a rewrite of the awaits. Playwright's sync API binds its dispatcher to the
thread that started it, and `engine._Lane` runs `policy.concurrency` worker threads, so every call
into Playwright is made on one thread this class owns. `test_every_playwright_call_lands_on_one_thread`
is the assertion that holds that -- it is what would otherwise fail only in production, and only
sometimes.

What none of this answers is whether Chromium actually gets past oliveyoung's challenge. That is the
shadow run (#10 condition 3), not a test.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from collectors.commerce.contract import Fetch, SourcePolicy, Transport
from collectors.commerce.engine import ChallengeBlocked, TransientError
from collectors.commerce.models import Dataset
from collectors.commerce.transport.browser import DEFAULT_PROFILE_DIR, BrowserFetcher

URL = "https://site.example/rank"


def _fetch(**kw: object) -> Fetch:
    kwargs: dict[str, object] = {"url": URL, "dataset": Dataset.RANKING}
    kwargs.update(kw)
    return Fetch(**kwargs)  # pyright: ignore[reportArgumentType]


def test_a_rendered_page_comes_back_as_a_payload(make_browser):
    fetcher, _ = make_browser(page={"body": "<html>rank</html>"})
    payload = fetcher.fetch(_fetch())
    assert payload.status == 200
    assert "rank" in payload.text()
    assert payload.final_url == URL


def test_a_wait_selector_is_awaited_before_the_content_is_read(make_browser):
    # A single-page app answers 200 with an empty shell; without the wait the parser is handed the
    # shell and reports nothing found, which reads afterwards like a quiet hour.
    fetcher, context = make_browser()
    fetcher.fetch(_fetch(wait_for=".product-list"))
    assert context.pages[0].waited_for == [".product-list"]


def test_a_collapsed_section_is_clicked_open_before_the_wait(make_browser):
    fetcher, context = make_browser(page={"visible": frozenset({"text=trigger"})})
    fetcher.fetch(_fetch(click_before="text=trigger", wait_for="text=table"))
    assert context.pages[0].clicked == ["text=trigger"]
    assert context.pages[0].waited_for == ["text=table"]


def test_an_already_open_toggle_is_not_clicked_shut(make_browser):
    # The same toggle renders already-open for some products. Clicking an open one closes it, which
    # turns the click meant to reveal the section into the thing that hides it.
    fetcher, context = make_browser(page={"visible": frozenset({"text=trigger", "text=table"})})
    fetcher.fetch(_fetch(click_before="text=trigger", wait_for="text=table"))
    assert context.pages[0].clicked == []
    assert context.pages[0].waited_for == ["text=table"]


def test_a_missing_trigger_is_skipped_so_wait_for_reports_the_real_problem(make_browser):
    fetcher, context = make_browser()
    fetcher.fetch(_fetch(click_before="text=trigger", wait_for="text=table"))
    assert context.pages[0].clicked == []
    assert context.pages[0].waited_for == ["text=table"]


def test_a_fetch_without_click_before_never_goes_looking_for_one(make_browser):
    fetcher, context = make_browser()
    fetcher.fetch(_fetch(wait_for=".product-list"))
    assert context.pages[0].located == []
    assert context.pages[0].clicked == []


def test_every_page_is_closed_even_though_the_context_lives_on(make_browser):
    fetcher, context = make_browser()
    fetcher.fetch(_fetch())
    # `all` over nothing is true. A fetcher that stopped opening pages passes the leak check having
    # examined no pages at all.
    assert context.pages
    assert all(page.closed for page in context.pages)
    assert context.closed is False


def test_a_page_is_closed_even_when_navigation_fails(make_browser):
    # A page leaked per failure only shows up on a bad day, which is the day the collector most
    # needs to keep running.
    fetcher, context = make_browser(page={"goto_error": RuntimeError("net::ERR_ABORTED")})
    with pytest.raises(TransientError):
        fetcher.fetch(_fetch())
    assert context.pages
    assert all(page.closed for page in context.pages)


def test_a_navigation_timeout_is_transient_rather_than_blocked(make_browser):
    fetcher, _ = make_browser(page={"goto_error": TimeoutError()})
    with pytest.raises(TransientError):
        fetcher.fetch(_fetch())


def test_a_challenge_page_rendered_with_a_200_is_still_a_challenge(make_browser):
    fetcher, _ = make_browser(page={"status": 200, "body": "<title>잠시만 기다려 주세요 - 올리브영</title>"})
    with pytest.raises(ChallengeBlocked, match="challenge"):
        fetcher.fetch(_fetch())


def test_the_refusal_names_the_source_whose_profile_needs_a_person(make_browser):
    fetcher, _ = make_browser(page={"status": 401, "body": "<html>login</html>"})
    with pytest.raises(ChallengeBlocked, match="probe"):
        fetcher.fetch(_fetch())


def test_the_browser_refuses_a_post_rather_than_quietly_making_it_a_get(make_browser):
    # page.goto() is a navigation: it carries no method and no body. A POST routed here would go out
    # as a GET and come back 405 -- or worse, 200 with something else the parser finds nothing in.
    fetcher, context = make_browser()
    with pytest.raises(ValueError, match="POST"):
        fetcher.fetch(_fetch(method="POST", body=b"{}"))
    assert context.starts == 0, "refusing a POST must not cost a Chromium"


def test_the_context_is_only_started_when_something_is_actually_fetched(make_browser):
    # Building four sources costs four Chromiums otherwise, including for the ones whose transport
    # is never reached because the run was blocked.
    fetcher, context = make_browser()
    assert context.starts == 0
    fetcher.fetch(_fetch())
    assert context.starts == 1


def test_a_second_fetch_reuses_the_context_rather_than_launching_again(make_browser):
    fetcher, context = make_browser()
    fetcher.fetch(_fetch())
    fetcher.fetch(_fetch(url="https://site.example/2"))
    assert context.starts == 1
    assert len(context.pages) == 2


def test_closing_the_fetcher_closes_the_browser_context(make_browser):
    fetcher, context = make_browser()
    fetcher.fetch(_fetch())
    fetcher.close()
    assert context.closed


def test_closing_a_fetcher_that_never_navigated_starts_nothing(make_browser):
    fetcher, context = make_browser()
    fetcher.close()
    assert context.starts == 0


def test_every_playwright_call_lands_on_one_thread(make_browser):
    """Playwright's sync API binds its dispatcher to the thread that started it, and a lane hands
    this fetcher requests from `policy.concurrency` worker threads. Every call has to arrive on the
    same thread, or the failure is a greenlet error in production, at 03:00, on the only source that
    needs a browser."""
    fetcher, context = make_browser()
    callers = [
        threading.Thread(target=lambda i=index: fetcher.fetch(_fetch(url=f"https://x/{i}")))
        for index in range(4)
    ]
    for thread in callers:
        thread.start()
    for thread in callers:
        thread.join(timeout=10.0)
        assert not thread.is_alive(), "a fetch from a worker thread never returned"

    assert len(context.pages) == 4, "the callers have to have got as far as Playwright"
    assert context.starts == 1, "four threads must not each start their own Playwright"
    assert len(context.threads) == 1, f"Playwright was reached from {len(context.threads)} threads"
    calling = {thread.ident for thread in callers} | {threading.get_ident()}
    assert not (context.threads & calling), "Playwright was reached from a thread the lane owns"


def test_the_profile_directory_is_per_source_and_injected(monkeypatch, tmp_path: Path):
    """The profile is where a person's authorised session lives, and the site refreshes its cookies
    into it -- so it is per source (one site's cookies are not ours to send to another) and its
    location is the deployment's to choose, not a constant in this file."""
    launched: dict[str, object] = {}

    class _Chromium:
        def launch_persistent_context(self, **kwargs: object) -> object:
            launched.update(kwargs)
            return object()

    class _Playwright:
        chromium = _Chromium()

        def stop(self) -> None:
            launched["stopped"] = True

    class _Starter:
        def start(self) -> _Playwright:
            return _Playwright()

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _Starter())
    policy = SourcePolicy(min_interval_s=0.0, concurrency=1, transport=Transport.BROWSER)
    fetcher = BrowserFetcher(policy, source_key="oliveyoung", profile_dir=tmp_path / "profiles")
    try:
        context = fetcher._launch_persistent_context()  # pyright: ignore[reportPrivateUsage]
    finally:
        fetcher.close()

    assert context is not None
    assert launched["user_data_dir"] == str(tmp_path / "profiles" / "oliveyoung")
    profile = tmp_path / "profiles" / "oliveyoung"
    assert profile.is_dir(), "a profile has to outlive the run for an authorised session to be worth having"
    assert launched["headless"] is True
    assert launched["user_agent"] == policy.user_agent


def test_the_default_profile_directory_is_relative_so_a_deployment_can_mount_over_it(
    monkeypatch, tmp_path: Path
):
    assert not DEFAULT_PROFILE_DIR.is_absolute()
    launched: dict[str, object] = {}

    class _Chromium:
        def launch_persistent_context(self, **kwargs: object) -> object:
            launched.update(kwargs)
            return object()

    class _Playwright:
        chromium = _Chromium()

        def stop(self) -> None:
            return None

    class _Starter:
        def start(self) -> _Playwright:
            return _Playwright()

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _Starter())
    monkeypatch.chdir(tmp_path)
    policy = SourcePolicy(min_interval_s=0.0, concurrency=1, transport=Transport.BROWSER)
    fetcher = BrowserFetcher(policy, source_key="oliveyoung")
    try:
        fetcher._launch_persistent_context()  # pyright: ignore[reportPrivateUsage]
    finally:
        fetcher.close()
    # Relative, and resolved against the working directory -- which in the image is the checkout
    # the deployment mounts a volume into.
    assert launched["user_data_dir"] == str(DEFAULT_PROFILE_DIR / "oliveyoung")
    assert (tmp_path / DEFAULT_PROFILE_DIR / "oliveyoung").is_dir()
