"""Korean prose is not a challenge, and the classifier has to know that without guessing.

Review round 1 (#10, Important 1): `challenge_reason` scanned the whole body for `"잠시만 기다려"` --
a phrase a shopping review or a seller reply says in passing -- with nothing but a 200,000-byte size
cut in front of it. A hit there ends the source: `ChallengeBlocked` -> `engine._halt()` ->
`blocked_reason` -> exit 2 -> `fetch_log` blocked 1, for a site that answered normally. That falsifies
cutover condition 3 ("blocked 0") and, because the source collects nothing that run, the row-count
check with it. The size cut made it worse than a plain bug: two of the captured fixtures sit within
3% of the cut, so the same content would be scanned or skipped depending on how many reviews the site
happened to be showing that hour.

The bodies here are the real captures in tests/collectors/commerce/fixtures/, which is what makes
this a statement about the sites we actually walk rather than about a string someone invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from collectors.commerce.engine import ChallengeBlocked
from collectors.commerce.transport.challenge import (
    MAX_SCANNED_BYTES,
    challenge_reason,
    raise_for_refusal,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# What the phrase looks like when a person writes it: the interstitial's own title says
# "잠시만 기다려 주세요", and a seller reply or a review says the same words about a restock.
PHRASE = "잠시만 기다려주세요"

# The content-type each capture arrives with. The JSON endpoints are XHR APIs and the pages are
# documents; nothing here is served as the other.
CAPTURES = sorted(FIXTURES.rglob("*.json")) + sorted(FIXTURES.rglob("*.html"))


def _content_type(path: Path) -> str:
    return "application/json; charset=utf-8" if path.suffix == ".json" else "text/html; charset=utf-8"


def _headers(path: Path) -> dict[str, str]:
    return {"content-type": _content_type(path), "server": "nginx"}


def _review_json_saying(phrase: str) -> bytes:
    """daisomall's ranking-review capture with `phrase` inside one review's text.

    The capture already carries free Korean prose about waiting for a restock ("...재입고 소식이 없어
    기다리고 있다가..."); this puts the exact words a customer-service reply uses into that same field
    rather than inventing a body.
    """
    path = FIXTURES / "daisomall" / "ranking" / "review.json"
    text = path.read_text(encoding="utf-8")
    marker = '"revwCn":"'
    at = text.index(marker) + len(marker)
    edited = text[:at] + f"품절이라 재입고까지 {phrase} " + text[at:]
    json.loads(edited)  # still the API's own shape, not a string with a phrase glued on
    return edited.encode("utf-8")


def _product_html_saying(phrase: str) -> bytes:
    """oliveyoung's product page with `phrase` in the body but not in the title.

    This is the case a content-type gate alone does not close: a real HTML document, in Korean, from
    a source we walk with the browser transport.
    """
    path = FIXTURES / "oliveyoung" / "product" / "detail-collapsed.html"
    text = path.read_text(encoding="utf-8")
    at = text.index("</head>") + len("</head>")
    notice = f'<div class="notice">주문이 몰려 배송이 늦습니다. {phrase}</div>'
    return (text[:at] + notice + text[at:]).encode("utf-8")


@pytest.mark.parametrize("path", CAPTURES, ids=lambda p: str(p.relative_to(FIXTURES)))
def test_no_captured_response_is_read_as_a_challenge(path: Path):
    # The floor: every page these four sources actually answered with must reach its parser.
    assert challenge_reason(_headers(path), path.read_bytes()) is None


def test_a_review_that_says_the_challenge_phrase_is_still_a_review():
    body = _review_json_saying(PHRASE)
    assert PHRASE in body.decode("utf-8"), "the fixture edit did not take; this check would be vacuous"
    assert len(body) < MAX_SCANNED_BYTES, "the size cut, not the fix, would be what passes this"
    path = FIXTURES / "daisomall" / "ranking" / "review.json"
    assert challenge_reason(_headers(path), body) is None


def test_a_korean_page_that_says_the_challenge_phrase_outside_its_title_is_still_a_page():
    # HTML, so a content-type gate lets it through to the scan; the phrase is in the document body,
    # where a notice or a seller's answer puts it, and not in the interstitial's title.
    body = _product_html_saying(PHRASE)
    assert PHRASE in body.decode("utf-8")
    path = FIXTURES / "oliveyoung" / "product" / "detail-collapsed.html"
    assert challenge_reason(_headers(path), body) is None


def test_the_engine_facing_call_lets_such_a_page_through_rather_than_halting_the_source():
    # challenge_reason is the classifier, but `_halt()` reacts to the exception -- so the exit code
    # this is really about hangs off raise_for_refusal, not off the return value above.
    raised: str | None = None
    try:
        raise_for_refusal(
            200,
            {"content-type": "application/json"},
            _review_json_saying(PHRASE),
            source_key="daisomall",
        )
    except ChallengeBlocked as exc:
        raised = str(exc)
    assert raised is None, f"a review halted the source: {raised}"


@pytest.mark.parametrize("padding", [0, MAX_SCANNED_BYTES])
def test_the_size_cut_no_longer_decides_what_a_body_is(padding: int):
    """Two captures sit within 3% of MAX_SCANNED_BYTES, so before the fix a run's classification of
    the same content moved with how many reviews were on the page that hour. Whatever the verdict is,
    it must now be the same on both sides of the cut."""
    body = _review_json_saying(PHRASE) + b" " * padding
    assert (len(body) >= MAX_SCANNED_BYTES) == bool(padding), "the padding must actually cross the cut"
    assert challenge_reason({"content-type": "application/json"}, body) is None
