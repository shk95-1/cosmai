"""What a refusal looks like, in one place.

origin: service/trend-radar/src/trend_radar/transport/challenge.py -- ported for #10, with the error
types taken from collectors/commerce/engine.py rather than re-declared (#7 put them there so the
retry loop and the gate could branch on them before either transport existed).

Shared by both transports on purpose, and the original file exists because they drifted once: the
HTTP fetcher treated a bare 403 as a refusal while the browser fetcher handed the same page to the
parser as content. That source then reported "no records" -- which reads as a broken parser and
exits 1 -- for a source that was simply being refused, which should exit 2. Whether a run is worth
investigating must not depend on which transport happened to run.
"""

from __future__ import annotations

from collections.abc import Mapping

from collectors.commerce.engine import (
    ChallengeBlocked,
    PermanentError,
    RateLimited,
    TransientError,
)

# `cf-mitigated` is what oliveyoung's edge sends. The Korean string is the interstitial's own title,
# which is what a challenge served with a 200 looks like -- and a 200 is the case worth catching,
# because nothing else would.
CHALLENGE_HEADERS = ("cf-mitigated",)
CHALLENGE_MARKERS = (
    "잠시만 기다려",
    "cf-browser-verification",
    "/cdn-cgi/challenge-platform",
)

# Only worth scanning the small pages a wall produces. A 700 KB ranking page is not a challenge, and
# decoding it on every fetch is pure cost.
MAX_SCANNED_BYTES = 200_000

# Statuses that say something about *us* rather than about the page. 403 belongs here and not with
# 404: an edge can answer 403 to every path, and calling that a per-page error would leave a fully
# blocked source looking like a run with some broken links.
REFUSAL_STATUSES = frozenset({401, 403})


def challenge_reason(headers: Mapping[str, str], body: bytes | str) -> str | None:
    lowered = {k.lower(): v for k, v in headers.items()}
    for header in CHALLENGE_HEADERS:
        if header in lowered:
            return f"bot challenge: {header}={lowered[header]!r}"

    if len(body) >= MAX_SCANNED_BYTES:
        return None
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body

    for marker in CHALLENGE_MARKERS:
        if marker in text:
            return f"bot challenge: response contains {marker!r}"
    return None


def raise_for_refusal(
    status: int,
    headers: Mapping[str, str],
    body: bytes | str,
    source_key: str | None = None,
    retry_after: float | None = None,
) -> None:
    """Turn a response into the right exception, or return so the parser gets it.

    Called by both transports so a status means the same thing either way.
    """
    reason = challenge_reason(headers, body)
    # Named rather than generic: the fix is a person opening that one source's profile directory,
    # and a message that does not say which source sends them looking through four.
    hint = f"; a person may need to authorise the browser profile for {source_key}" if source_key else ""
    if reason is not None:
        raise ChallengeBlocked(reason + hint, status=status)
    if status in REFUSAL_STATUSES:
        raise ChallengeBlocked(
            f"the site refused this client with {status}" + (hint or "; a browser profile may be needed"),
            status=status,
        )
    if status in (429, 503):
        raise RateLimited(f"rate limited with {status}", status=status, retry_after=retry_after)
    if 400 <= status < 500:
        raise PermanentError(f"{status} for the requested page", status=status)
    if status >= 500:
        raise TransientError(f"{status} from the site", status=status)
