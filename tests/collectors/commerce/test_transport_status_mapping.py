"""One status means one thing, whichever transport saw it.

origin: service/trend-radar/tests/transport/test_transports_agree.py, widened for #10 so the table is
asserted through the two transports as well as through the classifier they share. The two drifted
once in the original -- the HTTP fetcher treated a bare 403 as a refusal while the browser handed the
same page to the parser as content -- and the symptom was a blocked source reporting "no records",
which reads as a broken parser and exits 1 instead of 2.

The table is the whole point. `engine._fetch_with_retries` branches on these five types and nothing
else: `ChallengeBlocked` halts the source and sets `blocked_reason` (exit 2), `PermanentError` drops
one URL, `RateLimited`/`TransientError` retry, and `Gate.observe` widens the interval for 403/429/503.
A status routed to the wrong type makes the exit code lie about what happened.
"""

from __future__ import annotations

import httpx
import pytest

from collectors.commerce.contract import Fetch, SourcePolicy, Transport
from collectors.commerce.engine import (
    ChallengeBlocked,
    PermanentError,
    RateLimited,
    TransientError,
    TransportError,
)
from collectors.commerce.models import Dataset
from collectors.commerce.transport.challenge import MAX_SCANNED_BYTES, raise_for_refusal
from collectors.commerce.transport.http import HttpFetcher

URL = "https://site.example/rank"
REDIRECTED_TO = "https://site.example/final"

# Read this as "what the engine is told", not "what the site said". 401/403 are about *us* and stop
# the source; 404/410 are about one page; 429/503 are the site asking for a slower pace, which is
# also what `gate._BACKOFF_STATUSES` widens on.
CASES: tuple[tuple[int, type[TransportError] | None], ...] = (
    (200, None),
    (301, None),
    (401, ChallengeBlocked),
    (403, ChallengeBlocked),
    (404, PermanentError),
    (410, PermanentError),
    (429, RateLimited),
    (500, TransientError),
    (502, TransientError),
    (503, RateLimited),
)
IDS = [f"{status}-{(kind.__name__ if kind else 'payload')}" for status, kind in CASES]

POLICY = SourcePolicy(min_interval_s=0.0, concurrency=1, timeout_s=1.0)


def _fetch(**kw: object) -> Fetch:
    kwargs: dict[str, object] = {"url": URL, "dataset": Dataset.RANKING}
    kwargs.update(kw)
    return Fetch(**kwargs)  # pyright: ignore[reportArgumentType]


def _http(status: int) -> HttpFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == REDIRECTED_TO:
            return httpx.Response(200, text="followed")
        if 300 <= status < 400:
            return httpx.Response(status, headers={"Location": REDIRECTED_TO})
        return httpx.Response(status, text="<html>body</html>")

    return HttpFetcher(POLICY, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("status,expected", CASES, ids=IDS)
def test_the_shared_classifier_maps_each_status(status: int, expected: type[TransportError] | None):
    if expected is None:
        raise_for_refusal(status, {}, b"<html>ok</html>")
        return
    with pytest.raises(expected):
        raise_for_refusal(status, {}, b"<html>body</html>")


@pytest.mark.parametrize("status,expected", CASES, ids=IDS)
def test_the_http_transport_maps_each_status(status: int, expected: type[TransportError] | None):
    with _http(status) as fetcher:
        if expected is None:
            payload = fetcher.fetch(_fetch())
            assert payload.status == 200 if status == 301 else payload.status == status
            return
        with pytest.raises(expected) as exc:
            fetcher.fetch(_fetch())
    assert exc.value.status == status, "the gate reacts to this number; it has to survive the raise"


@pytest.mark.parametrize("status,expected", CASES, ids=IDS)
def test_the_browser_transport_maps_each_status(
    status: int, expected: type[TransportError] | None, make_browser
):
    fetcher, context = make_browser(page={"status": status, "body": "<html>body</html>"})
    if expected is None:
        payload = fetcher.fetch(_fetch())
        assert payload.status == status
        return
    with pytest.raises(expected) as exc:
        fetcher.fetch(_fetch())
    assert exc.value.status == status, "the gate reacts to this number; it has to survive the raise"
    assert context.pages, "a fetcher that never opened a page would pass every case above vacuously"


def test_a_challenge_header_outranks_a_healthy_status():
    # The case that makes the classifier necessary: an interstitial served with a 200. Nothing about
    # the status says anything is wrong, and a parser handed that page reports no products -- which
    # afterwards is indistinguishable from a quiet hour.
    with pytest.raises(ChallengeBlocked):
        raise_for_refusal(200, {"cf-mitigated": "challenge"}, b"<html/>")


def test_the_challenge_header_is_matched_whatever_case_the_edge_sent_it_in():
    with pytest.raises(ChallengeBlocked):
        raise_for_refusal(200, {"CF-Mitigated": "challenge"}, b"<html/>")


# Each interstitial written the way it actually arrives. The Korean one is the capture in the
# original repo's docs/sources/oliveyoung.md -- the phrase is the page's *title*, which is why round
# 1 stopped reading it anywhere else: outside a title those four words are a shopping review.
INTERSTITIALS = {
    "oliveyoung title": "<html><head><title>잠시만 기다려 주세요 - 올리브영</title></head></html>",
    "cf-browser-verification": "<html><body><div id='cf-browser-verification'></div></body></html>",
    "challenge-platform": "<html><body><script src='/cdn-cgi/challenge-platform/h/b'></script></body></html>",
}


@pytest.mark.parametrize("body", list(INTERSTITIALS.values()), ids=list(INTERSTITIALS))
def test_an_interstitial_body_served_with_a_200_is_still_a_challenge(body: str):
    with pytest.raises(ChallengeBlocked, match="challenge"):
        raise_for_refusal(200, {}, body.encode())


@pytest.mark.parametrize("body", list(INTERSTITIALS.values()), ids=list(INTERSTITIALS))
def test_an_interstitial_is_caught_when_the_edge_labels_it_html(body: str):
    # The scan is gated on content-type now; a challenge page is served as a document, so the gate
    # must not be what lets one through.
    with pytest.raises(ChallengeBlocked, match="challenge"):
        raise_for_refusal(200, {"Content-Type": "text/html; charset=utf-8"}, body.encode())


def test_a_page_too_big_to_be_a_wall_is_not_scanned():
    # A 700 KB ranking page is not a challenge, and decoding one on every fetch is pure cost. The
    # marker is really in there, so this fails the moment the size guard stops applying.
    body = ("<title>잠시만 기다려 주세요</title>" + "x" * MAX_SCANNED_BYTES).encode()
    raise_for_refusal(200, {}, body)


def test_an_api_answering_json_is_never_read_for_prose():
    # The daisomall and oliveyoung review endpoints answer JSON full of free Korean text. Scanning it
    # is how a review halts a source the site never refused -- see
    # test_challenge_never_fires_on_content.py, which drives this from the real captures.
    body = '{"revwCn":"품절이라 재입고까지 잠시만 기다려주세요"}'.encode()
    raise_for_refusal(200, {"content-type": "application/json; charset=utf-8"}, body)


def test_a_challenge_header_on_a_json_response_is_still_a_challenge():
    # The content-type gate covers the body scan only. An edge that mitigates an XHR says so in the
    # header, and that has to keep halting the source.
    with pytest.raises(ChallengeBlocked):
        raise_for_refusal(200, {"content-type": "application/json", "cf-mitigated": "challenge"}, b"{}")


def test_a_body_just_under_the_scan_limit_is_still_read():
    body = ("<title>잠시만 기다려 주세요</title>".encode()).ljust(MAX_SCANNED_BYTES - 1, b"x")
    with pytest.raises(ChallengeBlocked):
        raise_for_refusal(200, {}, body)


def test_the_refusal_names_the_source_so_an_operator_knows_whose_profile_to_fix():
    with pytest.raises(ChallengeBlocked, match="oliveyoung"):
        raise_for_refusal(403, {}, b"<html/>", source_key="oliveyoung")


def test_a_refusal_with_no_source_still_says_a_profile_may_be_needed():
    with pytest.raises(ChallengeBlocked, match="profile"):
        raise_for_refusal(403, {}, b"<html/>")


def test_a_rate_limit_carries_the_servers_own_answer_to_how_long():
    # `Gate._back_off` prefers this number over its own doubling, so it has to survive the raise.
    with pytest.raises(RateLimited) as exc:
        raise_for_refusal(429, {}, b"", retry_after=12.0)
    assert exc.value.retry_after == 12.0


def test_every_error_the_engine_branches_on_is_reachable_from_this_table():
    # A table that quietly stopped covering one of the five types would still be green. The engine
    # treats each of these differently, so each has to appear here.
    assert {kind for _, kind in CASES if kind is not None} == {
        ChallengeBlocked,
        PermanentError,
        RateLimited,
        TransientError,
    }


def test_the_transport_enum_the_dispatcher_routes_on_has_exactly_two_members():
    assert set(Transport) == {Transport.HTTP, Transport.BROWSER}
