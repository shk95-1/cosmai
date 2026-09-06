"""The live transport (#182): the DataLab search trend and blog search endpoints over httpx.

Shaped after `collectors/commerce/transport/http.py` -- one httpx client, an injectable
`httpx.BaseTransport` so tests drive whole runs without a socket, and the classification of what
comes back as the interesting part. What differs is the vocabulary: NAVER's API Hub answers with a
status and a `errorCode`, so the three things a run has to tell apart are

  a credential the gateway refuses (401/403)   -- every later request would be refused the same way
  a rate limit (429)                           -- more requests now would only deepen it
  one request that failed (another 4xx, or a 5xx that survived its retries)

which `collectors/naver/cli.py` maps onto the exit codes of contracts/entrypoints.md (0 ok · 1
partial · 2 blocked). Only the vendor's error *code* ever travels into a message: `errorMessage`
echoes the request back, and a note ends up in `needs.naver_run` and in an operator's terminal.

The credentials are the pair `contracts/secrets.md` names, sent as the two headers the API Hub
documents. They are set on the client once, never logged, and never interpolated into an error.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING, Any

import httpx

from collectors.naver.scope import (
    BLOG_START_MAX,
    MAX_REQUESTS_PER_RUN,
    REQUEST_TIMEOUT_S,
    RETRY_BACKOFF_S,
    RETRY_MAX_ATTEMPTS,
)

if TYPE_CHECKING:  # cli imports this module; the spec type comes back the other way for typing only.
    from collectors.naver.cli import FetchSpec

API_HOST = "https://openapi.naver.com"
DATALAB_PATH = "/v1/datalab/search"
BLOG_PATH = "/v1/search/blog.json"

#: We identify ourselves rather than imitate a browser (STATE.md §3, the same rule
#: `collectors/commerce/contract.py`'s DEFAULT_UA states for the commerce sources).
USER_AGENT = "cosmai-naver/0.1"

#: A code is `024`-shaped; anything else in that field is somebody else's error page and is not
#: repeated into a note verbatim.
_CODE = re.compile(r"[^0-9A-Za-z_-]")
_UNKNOWN_CODE = "?"


class TransportError(Exception):
    """`status` is what `needs.naver_fetch_log` records, which is what the collector_health view
    buckets on (contracts/entrypoints.md: ok / 403·429 / error)."""

    def __init__(self, message: str, *, status: int | None = None, error_code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.error_code = error_code


class AuthBlocked(TransportError):
    """401/403 -- the run is blocked (exit 2)."""


class RateLimited(TransportError):
    """429 -- the run stops and keeps what it collected (exit 1)."""


class BudgetExhausted(TransportError):
    """The run has spent `scope.MAX_REQUESTS_PER_RUN`; it stops the same way a 429 does."""


class RequestFailed(TransportError):
    """One request failed. The run goes on and ends partial if anything was collected."""


class HttpFetcher:
    """One per run: the budget below counts that run's requests, so a shared instance would let one
    run spend another's."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        budget: int | None = None,
    ) -> None:
        self.budget = MAX_REQUESTS_PER_RUN if budget is None else budget
        self.spent = 0
        self._sleep = sleep
        self._client = httpx.Client(
            base_url=API_HOST,
            timeout=REQUEST_TIMEOUT_S,
            transport=transport,
            headers={
                "X-Naver-Client-Id": client_id,
                "X-Naver-Client-Secret": client_secret,
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )

    def __enter__(self) -> HttpFetcher:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        params = dict(spec.params)
        if spec.kind == "datalab":
            # The body is `spec.params` unchanged: `parsing.datalab_request_key` hashes what is sent,
            # so a field added on the way out would leave the row's boundary key describing a
            # request nobody made (#44).
            return self._send("POST", DATALAB_PATH, json=params)
        if spec.kind == "blog":
            start = int(params.get("start", 1))
            if start > BLOG_START_MAX:
                # #110: the ceiling is the vendor's, independent of `total` -- a request past it can
                # only come back as an error, so it is never sent.
                raise RequestFailed(f"blog start {start} is past the vendor ceiling of {BLOG_START_MAX}")
            return self._send("GET", BLOG_PATH, params=params)
        raise ValueError(f"unknown fetch kind {spec.kind!r}")

    def _send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last: RequestFailed | None = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            self._charge()
            try:
                response = self._client.request(method, path, json=json, params=params)
            except httpx.TimeoutException:
                # The exception's own text carries the URL and nothing else useful; the timeout is
                # ours, so the message says what we set rather than what httpx formatted.
                last = RequestFailed(f"timed out after {REQUEST_TIMEOUT_S}s")
            except httpx.HTTPError as error:
                last = RequestFailed(f"request failed: {type(error).__name__}")
            else:
                status = response.status_code
                if status < 400:
                    return _body_of(response)
                code = _error_code(response)
                message = f"HTTP {status} (errorCode={code})"
                if status in (401, 403):
                    raise AuthBlocked(message, status=status, error_code=code)
                if status == 429:
                    raise RateLimited(message, status=status, error_code=code)
                if status < 500:
                    raise RequestFailed(message, status=status, error_code=code)
                last = RequestFailed(message, status=status, error_code=code)
            if attempt < RETRY_MAX_ATTEMPTS:
                self._sleep(RETRY_BACKOFF_S * 2 ** (attempt - 1))
        if last is None:
            # Unreachable: the loop runs at least once and every path through it either returns or
            # sets `last`. Explicit rather than an assert, which -O would strip out of the run.
            raise RequestFailed("the retry loop ended without an answer")
        raise last

    def _charge(self) -> None:
        # A retry is another request NAVER serves, so it is charged like any other (the rule
        # `collectors/commerce/engine.py` states for its own per-run budget).
        if self.spent >= self.budget:
            raise BudgetExhausted(f"this run has spent its budget of {self.budget} request(s)")
        self.spent += 1


def _body_of(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        # A 200 that is not JSON is a gateway's own page, not an answer -- treated as a failed
        # request rather than handed to a parser that would find nothing in it and say so quietly.
        raise RequestFailed(f"HTTP {response.status_code} with a body that is not JSON") from None
    if not isinstance(body, dict):
        raise RequestFailed(f"HTTP {response.status_code} with a JSON body that is not an object")
    return body


def _error_code(response: httpx.Response) -> str:
    """The vendor's `errorCode` alone. `errorMessage` is never read: it repeats the request, and this
    string ends up in a run note and a fetch_log row."""
    try:
        body = response.json()
    except ValueError:
        return _UNKNOWN_CODE
    code = body.get("errorCode") if isinstance(body, dict) else None
    if not isinstance(code, str) or not code:
        return _UNKNOWN_CODE
    return _CODE.sub("", code)[:16] or _UNKNOWN_CODE


__all__ = [
    "API_HOST",
    "DATALAB_PATH",
    "BLOG_PATH",
    "USER_AGENT",
    "TransportError",
    "AuthBlocked",
    "RateLimited",
    "BudgetExhausted",
    "RequestFailed",
    "HttpFetcher",
]
