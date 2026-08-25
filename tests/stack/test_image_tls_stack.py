"""The base image's TLS stack is a collection input, not a packaging detail.

oliveyoung's edge fingerprints the TLS ClientHello (JA3/JA4), so the library that opens the socket
decides whether a request is answered or challenged -- and that library comes from whatever Debian
suite the base image happens to be. The port to this repo pinned `bookworm-slim` while the origin
had been riding `python:3.12-slim`, a floating tag that moved on to trixie; the pin quietly took the
image back an OpenSSL major, the build stayed green, the suite stayed green, and only the 03:00 run
against the live site failed. Nothing in this repo was watching the one thing that changed.

This file watches it, without a socket: the suite named in the FROM has to be one whose OpenSSL is
recorded here as good enough, and stack/Dockerfile has to re-assert that floor *inside* the image at
build time -- because the tag names a suite while the handshake is decided by the library behind it,
and a registry tag can move under a name that did not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STACK = REPO_ROOT / "stack"
DOCKERFILE = STACK / "Dockerfile"

# The OpenSSL major.minor each Debian suite ships, as measured -- not as documented. Extending this
# table is the deliberate act that adopting a newer base should cost.
SUITE_OPENSSL = {
    "bullseye": (1, 1),
    "bookworm": (3, 0),
    "trixie": (3, 5),
}

# 2026-08-25: the two stacks oliveyoung answers (old image 3.5.6, host 3.5.7) sit at 3.5; the one it
# challenges (3.0.18) sits below. The floor is the major.minor, since that is the granularity at
# which OpenSSL changes the cipher/extension order a fingerprint is computed from.
OPENSSL_FLOOR = (3, 5)


def _text(relative: str) -> str:
    path = REPO_ROOT / relative
    assert path.is_file(), f"{relative} is missing; nothing defines the image"
    return path.read_text(encoding="utf-8")


def _base_tag() -> str:
    tags = re.findall(r"^FROM\s+(\S+)", _text("stack/Dockerfile"), flags=re.MULTILINE)
    assert len(tags) == 1, f"stack/Dockerfile has {len(tags)} FROM lines; this file reads one base"
    return tags[0]


def test_the_base_image_names_a_suite_whose_tls_stack_has_been_measured():
    tag = _base_tag()
    named = [suite for suite in SUITE_OPENSSL if suite in tag]
    assert len(named) == 1, (
        f"{tag} names {named or 'no'} Debian suite this file has measured. A base whose OpenSSL "
        "nobody has looked at is a base whose fingerprint nobody has looked at: measure it, add it "
        "to SUITE_OPENSSL, and let this test decide."
    )


def test_the_suite_the_image_is_pinned_to_still_clears_the_floor():
    suite = next(s for s in SUITE_OPENSSL if s in _base_tag())
    assert SUITE_OPENSSL[suite] >= OPENSSL_FLOOR, (
        f"the image is pinned to {suite}, whose OpenSSL {SUITE_OPENSSL[suite]} is below "
        f"{OPENSSL_FLOOR}. That build is green and that collection is blocked."
    )


def test_the_build_asserts_the_interpreters_openssl_instead_of_trusting_the_tag():
    # A tag is a name a registry can move; ssl.OPENSSL_VERSION_INFO is what actually opens the
    # socket. Failing this in a RUN puts the discovery in the build rather than in a 03:00 run.
    lines = [line for line in _text("stack/Dockerfile").splitlines() if "OPENSSL_VERSION_INFO" in line]
    assert lines, (
        "stack/Dockerfile never reads ssl.OPENSSL_VERSION_INFO, so a moved base tag ships an "
        "unmeasured TLS stack and the build says nothing"
    )
    floors = {
        tuple(int(n) for n in m) for line in lines for m in re.findall(r">=\s*\((\d+),\s*(\d+)\)", line)
    }
    assert floors == {OPENSSL_FLOOR}, (
        f"stack/Dockerfile asserts {floors or 'no'} OpenSSL floor; this file holds {OPENSSL_FLOOR}. "
        "Two floors that disagree are one floor nobody is enforcing."
    )


@pytest.mark.parametrize(
    "relative",
    sorted(str(p.relative_to(REPO_ROOT)) for p in STACK.rglob("*") if p.is_file()),
    ids=lambda s: s,
)
def test_no_stack_file_instructs_a_build_to_use_a_suite_below_the_floor(relative: str):
    # Comment lines are skipped on purpose -- naming the retired base is how a comment explains why
    # the current one was chosen, and a `FROM` is not the only line that can pull an image in.
    instructions = "\n".join(
        line for line in _text(relative).splitlines() if not line.lstrip().startswith("#")
    )
    stale = sorted(s for s, v in SUITE_OPENSSL.items() if v < OPENSSL_FLOOR and s in instructions)
    assert not stale, f"{relative} builds on {stale}, a base below the OpenSSL floor"
