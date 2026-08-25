"""`cosmai login` -- the entrypoint #27 adds so a person can authorise a browser source's profile.

origin: service/trend-radar/src/trend_radar/cli.py:241-280's `login` subcommand, de-asynced to match
this repo's already-sync transport (collectors/commerce/transport/browser.py). The registry, not a
hardcoded name, decides which source key is a browser source -- test_transport_dispatch.py already
pins oliveyoung as the one today, so this file only has to prove `login` asks the registry rather
than assuming.

No real browser and no network here: every case injects `fetcher_factory` so this stays an offline
check that `login` builds with `headless=False` and the right `profile_dir`, never that Chromium
actually opens.
"""

from __future__ import annotations

from collectors.commerce import cli
from collectors.commerce import sources as _sources  # noqa: F401 -- import registers every source
from collectors.commerce.contract import Fetch, Payload
from collectors.commerce.registry import SOURCES


class _FakeFetcher:
    def __init__(self) -> None:
        self.closed = False
        self.fetched: list[Fetch] = []

    def fetch(self, fetch: Fetch) -> Payload:
        self.fetched.append(fetch)
        return Payload(fetch=fetch, status=200, body=b"", final_url=fetch.url, headers={}, elapsed_ms=0)

    def close(self) -> None:
        self.closed = True


def _factory(calls: list[dict]):
    def build(policy, *, source_key, profile_dir, headless):
        calls.append(
            {"policy": policy, "source_key": source_key, "profile_dir": profile_dir, "headless": headless}
        )
        return _FakeFetcher()

    return build


def test_login_opens_headless_false_for_a_registered_browser_source(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr("builtins.input", lambda: "")
    code = cli.login("oliveyoung", fetcher_factory=_factory(calls))
    assert code == 0
    assert len(calls) == 1
    assert calls[0]["headless"] is False
    assert calls[0]["source_key"] == "oliveyoung"


def test_login_uses_the_same_profile_dir_the_collector_would():
    """Same directory as `live_fetchers()`'s default, or a login and a collect run authorise two
    different places and the person's work never reaches the collector."""
    calls: list[dict] = []
    import builtins

    from collectors.commerce.transport.browser import DEFAULT_PROFILE_DIR

    orig_input = builtins.input
    builtins.input = lambda: ""
    try:
        cli.login("oliveyoung", fetcher_factory=_factory(calls))
    finally:
        builtins.input = orig_input
    assert calls[0]["profile_dir"] == DEFAULT_PROFILE_DIR


def test_login_refuses_a_source_that_is_not_a_browser_source():
    # hwahae is HTTP-only (test_transport_dispatch.py pins oliveyoung as the one browser source) --
    # there is no profile to authorise for it, and opening a window would be theatre.
    code = cli.login("hwahae")
    assert code != 0


def test_login_refuses_an_unknown_source():
    code = cli.login("not-a-real-source")
    assert code != 0


def test_login_refuses_when_cwd_is_not_the_repo_root(tmp_path, monkeypatch):
    # #27 round 1: login now runs on the HOST from the repo root (not inside the container), because
    # DEFAULT_PROFILE_DIR is relative to cwd and the bind mount's default host path
    # (stack/docker-compose.yml's COMMERCE_BROWSER_PROFILE_DIR) is the repo root's var/browser-profiles.
    # A different cwd would silently authorise a profile the collector never mounts.
    calls: list[dict] = []
    monkeypatch.chdir(tmp_path)
    code = cli.login("oliveyoung", fetcher_factory=_factory(calls))
    assert code != 0
    assert calls == [], "a wrong-cwd refusal must not open a browser at the wrong profile"


def test_login_refusal_never_builds_a_fetcher():
    calls: list[dict] = []
    code = cli.login("hwahae", fetcher_factory=_factory(calls))
    assert code != 0
    assert calls == [], "a refused source must never reach the point of opening a browser"


def test_every_registered_browser_source_can_be_logged_into_offline():
    browser_sources = {k for k, c in SOURCES.items() if c.policy.transport.value == "browser"}
    assert browser_sources, "this would pass vacuously if the registry declared no browser source"
    for key in browser_sources:
        calls: list[dict] = []

        def _noop_input() -> str:
            return ""

        import builtins

        orig = builtins.input
        builtins.input = _noop_input
        try:
            code = cli.login(key, fetcher_factory=_factory(calls))
        finally:
            builtins.input = orig
        assert code == 0, key
        assert calls[0]["source_key"] == key
