"""Turning what a site says about a person into something this project may keep.

origin: service/trend-radar/src/trend_radar/scrub.py -- ported for #7 verbatim. One implementation,
shared by every source, so the way this drifts -- one source truncating a hash differently, or
forgetting entirely -- cannot happen quietly.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# Long enough that two authors will not collide in any corpus this project will hold, short enough to
# be obviously not a reversible encoding of anything.
_HASH_CHARS = 16


def author_hash(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return hashlib.sha256(value.strip().encode()).hexdigest()[:_HASH_CHARS]


def kst_date(value: object, *formats: str) -> datetime | None:
    """Read a bare date written in KST and return the UTC instant it began -- treating it as already
    UTC would move every review nine hours."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in formats or ("%Y-%m-%d",):
        try:
            day = datetime.strptime(text[: len(datetime(2026, 1, 1).strftime(fmt))], fmt)
        except ValueError:
            continue
        return day.replace(tzinfo=KST).astimezone(UTC)
    return None


_BR = re.compile(r"(?:<|&lt;)\s*br\s*/?\s*(?:>|&gt;)", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def review_text(value: object) -> str | None:
    """A review body as text: markup gone, entities decoded, reviewer intact.

    Order matters: both sites send bodies HTML-escaped, so a line break arrives as `&lt;br&gt;` -- and
    so do angle brackets the reviewer typed. Decode <br> by name first, strip genuine tags, then
    unescape everything else; an angle bracket surviving to the end is one the reviewer meant.
    """
    if not isinstance(value, str):
        return None
    text = _BR.sub("\n", value)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip() or None
