"""The HTTP transport: one attempt, and what it does with the answer.

origin: service/trend-radar/tests/transport/test_http.py -- de-asynced for #10 and re-pointed at
`httpx.MockTransport` instead of respx, so the suite gains no dependency for the mocking and the
real `httpx.Client` (its timeout, its redirect following, its default headers) is the thing under
test rather than a stand-in for it.

Offline by construction: `tests/conftest.py` refuses every socket, and MockTransport opens none.
The status table itself lives in test_transport_status_mapping.py, which asserts it for both
transports at once.
"""

from __future__ import annotations

import httpx
import pytest

from collectors.commerce.contract import DEFAULT_UA, Fetch, SourcePolicy
from collectors.commerce.engine import RateLimited, TransientError
from collectors.commerce.models import Dataset
from collectors.commerce.transport.http import HttpFetcher

URL = "https://site.example/rank"
POLICY = SourcePolicy(min_interval_s=0.0, concurrency=1, timeout_s=1.0)


def _fetch(**kw: object) -> Fetch:
    kwargs: dict[str, object] = {"url": URL, "dataset": Dataset.RANKING}
    kwargs.update(kw)
    return Fetch(**kwargs)  # pyright: ignore[reportArgumentType]


class _Recorder:
    """Answers with `response` and keeps every request, so a test can assert on what went out."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses) or [httpx.Response(200, text="hello")]
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses[min(len(self.requests) - 1, len(self._responses) - 1)]

    def fetcher(self) -> HttpFetcher:
        return HttpFetcher(POLICY, transport=httpx.MockTransport(self))


def test_a_200_becomes_a_payload_the_parser_can_read():
    recorder = _Recorder(httpx.Response(200, text="hello"))
    with recorder.fetcher() as fetcher:
        payload = fetcher.fetch(_fetch())
    assert payload.status == 200
    assert payload.text() == "hello"
    assert payload.final_url == URL
    assert payload.elapsed_ms >= 0


def test_the_headers_the_engine_stamped_are_the_ones_that_go_out():
    # The user agent is applied by `engine._with_user_agent`, not here, precisely so it cannot be
    # forgotten in one transport and not the other. A transport that dropped `fetch.headers` would
    # send httpx's own `python-httpx/...` to a Korean retailer instead of a name it can look up.
    recorder = _Recorder()
    with recorder.fetcher() as fetcher:
        fetcher.fetch(_fetch(headers=(("User-Agent", DEFAULT_UA), ("X-Trace", "1"))))
    sent = recorder.requests[-1].headers
    assert sent["user-agent"] == DEFAULT_UA
    assert sent["x-trace"] == "1"


def test_default_ua_is_pinned_to_the_string_oliveyoung_let_through():
    # 2026-08-25 A/B on the review-cursor endpoint: this exact string ran 51 requests clean where
    # cosmai-commerce/... drew a Cloudflare challenge on the 2nd. Importing DEFAULT_UA and comparing
    # it to itself (as the tests above do) would pass no matter what the constant said, so this
    # pins the literal -- a future edit to the string must edit this assertion too.
    assert DEFAULT_UA == "trend-radar/0.1 (+https://github.com/slopindustries/trend-radar)"


def test_the_transport_names_no_user_agent_of_its_own():
    # Two places stamping a UA is how they disagree. The engine owns it; this owns the headers that
    # describe what a Korean site should answer with.
    recorder = _Recorder()
    with recorder.fetcher() as fetcher:
        fetcher.fetch(_fetch(headers=(("User-Agent", "engine-said-this"),)))
    assert recorder.requests[-1].headers["user-agent"] == "engine-said-this"


def test_korean_is_asked_for_because_these_are_korean_sites():
    recorder = _Recorder()
    with recorder.fetcher() as fetcher:
        fetcher.fetch(_fetch())
    assert recorder.requests[-1].headers["accept-language"].startswith("ko")


@pytest.mark.parametrize(
    "header,expected",
    [
        ({"Retry-After": "12"}, 12.0),
        ({}, None),
        # Servers are allowed to send a date here. We have no clock to compare it against, and
        # guessing would be worse than letting the gate's own doubling decide -- but a TypeError in
        # a worker is worse than both.
        ({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, None),
    ],
    ids=["seconds", "absent", "http-date"],
)
def test_retry_after_reaches_the_error_the_gate_reads(header: dict[str, str], expected: float | None):
    recorder = _Recorder(httpx.Response(429, headers=header))
    with recorder.fetcher() as fetcher, pytest.raises(RateLimited) as exc:
        fetcher.fetch(_fetch())
    assert exc.value.retry_after == expected


def test_a_timeout_is_transient_rather_than_a_dropped_page():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with HttpFetcher(POLICY, transport=httpx.MockTransport(handler)) as fetcher:
        with pytest.raises(TransientError):
            fetcher.fetch(_fetch())


def test_a_connection_failure_is_transient():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with HttpFetcher(POLICY, transport=httpx.MockTransport(handler)) as fetcher:
        with pytest.raises(TransientError):
            fetcher.fetch(_fetch())


def test_the_fetcher_makes_exactly_one_attempt():
    # Retries belong to the lane, which owns the gate. A fetcher retrying inside this call would
    # send a second request without paying the source's interval for it -- which is the behaviour
    # that gets a crawler blocked.
    recorder = _Recorder(httpx.Response(500))
    with recorder.fetcher() as fetcher, pytest.raises(TransientError):
        fetcher.fetch(_fetch())
    assert len(recorder.requests) == 1


def test_a_redirect_is_followed_and_the_final_url_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/final":
            return httpx.Response(200, text="ok")
        return httpx.Response(302, headers={"Location": "https://site.example/final"})

    with HttpFetcher(POLICY, transport=httpx.MockTransport(handler)) as fetcher:
        payload = fetcher.fetch(_fetch())
    assert payload.final_url == "https://site.example/final"
    assert payload.text() == "ok"


def test_a_post_body_is_sent_when_a_source_asks_for_one():
    # oliveyoung's review API is POST-only, and it is the reason `Fetch.transport` exists at all.
    recorder = _Recorder()
    with recorder.fetcher() as fetcher:
        fetcher.fetch(_fetch(method="POST", body=b'{"q":1}'))
    request = recorder.requests[-1]
    assert request.method == "POST"
    assert request.content == b'{"q":1}'


def test_the_response_headers_reach_the_parser():
    recorder = _Recorder(httpx.Response(200, headers={"Content-Type": "application/json"}, text="{}"))
    with recorder.fetcher() as fetcher:
        payload = fetcher.fetch(_fetch())
    assert payload.headers["content-type"] == "application/json"


def test_closing_the_fetcher_closes_the_client():
    recorder = _Recorder()
    fetcher = recorder.fetcher()
    fetcher.fetch(_fetch())
    fetcher.close()
    with pytest.raises(RuntimeError):
        fetcher.fetch(_fetch())
