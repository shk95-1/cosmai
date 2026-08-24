"""Plain HTTP, one attempt per call.

origin: service/trend-radar/src/trend_radar/transport/http.py -- de-asynced for #10 (this repo's
engine runs threads, not tasks). `httpx.Client` is thread-safe for requests, which is what lets one
of these serve hwahae's two workers.

The interesting part is not the request -- it is the classification of what comes back, because the
difference between "this page is gone" and "this site is refusing us" decides whether the run drops
one URL or stops a source and says why. The failure this is shaped to prevent: a challenge page
arriving with a 200 and a parser dutifully finding no products in it, which afterwards is
indistinguishable from a quiet hour.
"""

from __future__ import annotations

import time
from types import TracebackType

import httpx

from collectors.commerce.contract import Fetch, Payload, SourcePolicy
from collectors.commerce.engine import TransientError
from collectors.commerce.transport.challenge import raise_for_refusal


class HttpFetcher:
    def __init__(self, policy: SourcePolicy, transport: httpx.BaseTransport | None = None) -> None:
        self._policy = policy
        self._client = httpx.Client(
            timeout=policy.timeout_s,
            follow_redirects=True,
            transport=transport,
            headers={
                # No User-Agent here. `engine._with_user_agent` stamps the policy's onto every
                # `Fetch` before it reaches a transport, so a second one set at this level would be
                # a place for the two to disagree -- and the one a site sees would depend on which
                # of them httpx merged last.
                #
                # These are Korean sites; asking for anything else invites a different page than the
                # one the fixtures were captured from.
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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

    def fetch(self, fetch: Fetch) -> Payload:
        started = time.monotonic()
        try:
            response = self._client.request(
                fetch.method,
                fetch.url,
                headers=dict(fetch.headers),
                content=fetch.body,
            )
        except httpx.TimeoutException as exc:
            raise TransientError(f"timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TransientError(f"request failed: {exc}") from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)
        raise_for_refusal(
            status=response.status_code,
            headers=response.headers,
            body=response.content,
            retry_after=_retry_after(response),
        )
        return Payload(
            fetch=fetch,
            status=response.status_code,
            body=response.content,
            final_url=str(response.url),
            headers=dict(response.headers),
            elapsed_ms=elapsed_ms,
        )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        # The header is allowed to be an HTTP date. We have no clock to compare it against here, and
        # guessing would be worse than letting the gate's own doubling decide.
        return None
