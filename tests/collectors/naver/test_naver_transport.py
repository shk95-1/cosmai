"""What actually goes out on the wire, and how a status becomes the run's outcome (#182).

Every request here is served by an `httpx.MockTransport`: the suite is offline by construction
(tests/conftest.py), and the one test that really calls NAVER is `live`-marked at the bottom --
excluded by the default `-m 'not live and not local_llm'`.

The credentials in this file are dummy strings. A real one never reaches a test, a log or an error
message, which the last test of the status block asserts rather than assumes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from collectors.naver import parsing, scope
from collectors.naver.cli import SECRET_KEYS, FetchSpec
from collectors.naver.transport import (
    BLOG_PATH,
    DATALAB_PATH,
    AuthBlocked,
    BudgetExhausted,
    HttpFetcher,
    RateLimited,
    RequestFailed,
)
from db import secrets

CLIENT_ID = "dummy-client-id"
CLIENT_SECRET = "dummy-client-secret"

# The search terms and the blog title are the Korean a real request and a real response carry, which
# is why they sit in a fixture file rather than in this module (tool/checks/lang).
FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "naver_transport.json").read_text(encoding="utf-8")
)
DATALAB_BODY = FIXTURE["datalab_body"]
BLOG_BODY = FIXTURE["blog_body"]

DATALAB_SPEC = FetchSpec(kind="datalab", query="suncare", params=FIXTURE["datalab_params"])
BLOG_SPEC = FetchSpec(kind="blog", query=FIXTURE["blog_params"]["query"], params=FIXTURE["blog_params"])


def _fetcher(handler, sleeps: list[float] | None = None, budget: int | None = None) -> HttpFetcher:
    return HttpFetcher(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else lambda _seconds: None),
        budget=budget,
    )


def _serving(status: int, body: Any, seen: list[httpx.Request] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    return handler


# --- what goes out ---------------------------------------------------------------------------


def test_a_datalab_fetch_posts_the_request_body_to_the_datalab_endpoint():
    seen: list[httpx.Request] = []
    fetcher = _fetcher(_serving(200, DATALAB_BODY, seen))
    body = fetcher.fetch(DATALAB_SPEC)

    assert body == DATALAB_BODY
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"https://openapi.naver.com{DATALAB_PATH}"
    # The params `datalab_request_key` hashes are the params that go out -- one boundary, not two (#44).
    assert json.loads(request.content) == DATALAB_SPEC.params
    assert request.headers["Content-Type"].startswith("application/json")


def test_a_blog_fetch_gets_the_search_endpoint_with_the_scope_query_parameters():
    seen: list[httpx.Request] = []
    fetcher = _fetcher(_serving(200, BLOG_BODY, seen))
    body = fetcher.fetch(BLOG_SPEC)

    assert body == BLOG_BODY
    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == BLOG_PATH
    assert dict(request.url.params) == {key: str(value) for key, value in BLOG_SPEC.params.items()}
    # The values the walk sends are scope.py's, not this test's own idea of a page.
    assert BLOG_SPEC.params["display"] == scope.BLOG_DISPLAY
    assert BLOG_SPEC.params["sort"] == scope.BLOG_SORT


def test_both_endpoints_carry_the_two_credential_headers():
    seen: list[httpx.Request] = []
    fetcher = _fetcher(_serving(200, BLOG_BODY, seen))
    fetcher.fetch(BLOG_SPEC)
    fetcher.fetch(DATALAB_SPEC)

    assert len(seen) == 2, "an empty list would make the loop below assert nothing"
    for request in seen:
        assert request.headers["X-Naver-Client-Id"] == CLIENT_ID
        assert request.headers["X-Naver-Client-Secret"] == CLIENT_SECRET


def test_a_blog_start_past_the_vendor_ceiling_is_never_requested():
    """#110: `start` 1000 is a vendor hard limit, and a request past it is spent budget that can only
    come back as an error -- the CLI's walk stops there, and so does this, one layer lower."""
    seen: list[httpx.Request] = []
    fetcher = _fetcher(_serving(200, BLOG_BODY, seen))
    past_params = {**BLOG_SPEC.params, "start": scope.BLOG_START_MAX + 1}
    past = FetchSpec(kind="blog", query=BLOG_SPEC.query, params=past_params)

    with pytest.raises(RequestFailed) as raised:
        fetcher.fetch(past)
    assert str(scope.BLOG_START_MAX) in str(raised.value)
    assert seen == []


# --- what comes back -------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_credential_blocks_the_run_and_carries_the_vendor_error_code(status: int):
    error_body = {"errorCode": "024", "errorMessage": "Authentication failed"}
    fetcher = _fetcher(_serving(status, error_body))

    with pytest.raises(AuthBlocked) as raised:
        fetcher.fetch(BLOG_SPEC)
    assert raised.value.status == status
    assert raised.value.error_code == "024"
    assert str(status) in str(raised.value) and "024" in str(raised.value)
    # The note this message becomes is read by whoever is on call; the vendor's message echoes the
    # request back, so only the code travels.
    assert "Authentication failed" not in str(raised.value)


def test_a_429_stops_the_run_rather_than_failing_one_request():
    fetcher = _fetcher(_serving(429, {"errorCode": "012", "errorMessage": "rate limited"}))

    with pytest.raises(RateLimited) as raised:
        fetcher.fetch(BLOG_SPEC)
    assert raised.value.status == 429
    assert raised.value.error_code == "012"


def test_another_4xx_fails_that_one_request_and_is_not_retried():
    seen: list[httpx.Request] = []
    fetcher = _fetcher(_serving(400, {"errorCode": "101", "errorMessage": "bad request"}, seen))

    with pytest.raises(RequestFailed) as raised:
        fetcher.fetch(BLOG_SPEC)
    assert raised.value.status == 400
    assert len(seen) == 1


def test_a_5xx_is_retried_to_the_attempt_cap_and_then_fails():
    seen: list[httpx.Request] = []
    fetcher = _fetcher(_serving(503, {"errorCode": "500"}, seen))

    with pytest.raises(RequestFailed) as raised:
        fetcher.fetch(BLOG_SPEC)
    assert raised.value.status == 503
    assert len(seen) == scope.RETRY_MAX_ATTEMPTS


def test_a_timeout_is_retried_to_the_attempt_cap_and_then_fails():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    fetcher = _fetcher(handler)
    with pytest.raises(RequestFailed) as raised:
        fetcher.fetch(BLOG_SPEC)
    assert raised.value.status is None
    assert len(attempts) == scope.RETRY_MAX_ATTEMPTS


def test_a_retry_waits_longer_each_time():
    sleeps: list[float] = []
    fetcher = _fetcher(_serving(500, {"errorCode": "500"}), sleeps=sleeps)

    with pytest.raises(RequestFailed):
        fetcher.fetch(BLOG_SPEC)
    assert sleeps == [scope.RETRY_BACKOFF_S * 2**i for i in range(scope.RETRY_MAX_ATTEMPTS - 1)]


def test_a_success_after_one_5xx_is_the_response_not_the_error():
    answers = [httpx.Response(500, json={"errorCode": "500"}), httpx.Response(200, json=BLOG_BODY)]

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return answers.pop(0)

    assert _fetcher(handler).fetch(BLOG_SPEC) == BLOG_BODY


def test_a_body_that_is_not_json_fails_that_request():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, text="<html>a gateway's own page</html>")

    with pytest.raises(RequestFailed):
        _fetcher(handler).fetch(BLOG_SPEC)


def test_no_credential_value_reaches_an_error_message():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"errorCode": "024", "errorMessage": CLIENT_SECRET})

    with pytest.raises(AuthBlocked) as raised:
        _fetcher(handler).fetch(BLOG_SPEC)
    assert CLIENT_SECRET not in str(raised.value)
    assert CLIENT_ID not in str(raised.value)


# --- what one run may spend ------------------------------------------------------------------


def test_the_default_request_budget_is_the_scope_constant():
    assert _fetcher(_serving(200, BLOG_BODY)).budget == scope.MAX_REQUESTS_PER_RUN


def test_the_budget_bounds_one_run_and_a_retry_is_charged_to_it():
    seen: list[httpx.Request] = []
    fetcher = _fetcher(_serving(500, {"errorCode": "500"}, seen), budget=2)

    with pytest.raises(BudgetExhausted):
        fetcher.fetch(BLOG_SPEC)
    # A retry is another request NAVER serves, so the third attempt is what the budget refuses.
    assert len(seen) == 2
    assert fetcher.spent == 2


def test_a_spent_budget_refuses_the_next_fetch_without_a_request():
    seen: list[httpx.Request] = []
    fetcher = _fetcher(_serving(200, BLOG_BODY, seen), budget=1)

    fetcher.fetch(BLOG_SPEC)
    with pytest.raises(BudgetExhausted):
        fetcher.fetch(BLOG_SPEC)
    assert len(seen) == 1


# --- the one real request ----------------------------------------------------------------------


@pytest.mark.live
def test_one_live_blog_request_is_accepted_by_the_parser():
    """`-m live`, so it runs only when a person asks for it. What it proves is the join the fixtures
    cannot: that these headers open the real endpoint and that `parse_blog_response` finds posts in
    what NAVER actually answers with. One request, ten items."""
    keys = secrets.load()
    if not all(keys.get(name) for name in SECRET_KEYS):
        pytest.skip("no NAVER credentials on this host")

    fetcher = HttpFetcher(client_id=keys[SECRET_KEYS[0]], client_secret=keys[SECRET_KEYS[1]])
    try:
        term = FIXTURE["live_query"]
        body = fetcher.fetch(
            FetchSpec(
                kind="blog",
                query=term,
                params={"query": term, "display": 10, "start": 1, "sort": scope.BLOG_SORT},
            )
        )
    finally:
        fetcher.close()

    posts = parsing.parse_blog_response(
        body, category=None, group_key=None, query=term, captured_at=datetime.now(UTC)
    )
    assert posts, "the live endpoint answered, but the parser found no post in it"
    assert posts[0].url.startswith("http")
