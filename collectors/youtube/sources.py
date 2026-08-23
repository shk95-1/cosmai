"""Turning one yt-dlp/timedtext dump into the plain dict `flatten.py` writes to a row.

origin: service/yt-scrapper/src/tubedepth/sources/{listings,video_metadata,comments,transcript}.py's
pure `normalize`/`parse_json3` functions -- ported for #8 as plain dicts rather than pydantic models
(this package has no cache layer to round-trip a model through, unlike the archived `CollectionService`,
so the extra structure bought nothing here). Every function below takes exactly the shape yt-dlp/timedtext
actually returns and nothing that depends on a live fetch -- the fixtures under
tests/collectors/youtube/fixtures/ are saved copies of that shape, gzip-free (issue #8: bring the
fixtures, not the archived AGENTS.md/docs/hooks alongside them).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

VIDEO_IDENTIFIER_LENGTH = 11


def _published_at(timestamp: int | None) -> datetime | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=UTC)


def _published_date(upload_date: str | None) -> str | None:
    if not upload_date:
        return None
    try:
        return datetime.strptime(upload_date, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def normalize_listing(dump: Mapping[str, Any], *, source_kind: str) -> dict[str, Any]:
    """A channel/search/playlist/trending dump -> `{videos, listing_id, title, skipped_count}`."""
    videos: list[dict[str, Any]] = []
    skipped = 0
    for entry in dump.get("entries") or []:
        identifier = entry.get("id")
        if not identifier or len(identifier) != VIDEO_IDENTIFIER_LENGTH:
            # yt-dlp leaves a placeholder for deleted/private videos; queuing one only ever fails.
            skipped += 1
            continue
        videos.append(
            {
                "video_id": identifier,
                "title": entry.get("title"),
                "duration_seconds": int(entry["duration"]) if entry.get("duration") else None,
                "view_count": entry.get("view_count"),
                "channel": entry.get("channel") or entry.get("uploader"),
                "channel_id": entry.get("channel_id"),
                "published_at": _published_at(entry.get("timestamp")),
            }
        )
    return {
        "source_kind": source_kind,
        "listing_id": dump.get("id"),
        "title": dump.get("title"),
        "videos": videos,
        "skipped_count": skipped,
    }


def normalize_video_metadata(dump: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "video_id": dump["id"],
        "title": dump["title"],
        "channel": dump.get("channel"),
        "channel_id": dump.get("channel_id"),
        "duration_seconds": dump.get("duration"),
        "view_count": dump.get("view_count"),
        "like_count": dump.get("like_count"),
        "comment_count": dump.get("comment_count"),
        "published_at": _published_at(dump.get("timestamp")),
        "published_date": _published_date(dump.get("upload_date")),
    }


_ROOT_SENTINEL = "root"


def _comment(raw: Mapping[str, Any]) -> dict[str, Any]:
    parent = raw.get("parent")
    return {
        "comment_id": raw["id"],
        "parent_id": None if parent in (None, _ROOT_SENTINEL) else parent,
        "text": raw.get("text", ""),
        "author": raw.get("author"),
        "author_id": raw.get("author_id"),
        "like_count": raw.get("like_count"),
        "is_hearted_by_uploader": bool(raw.get("is_favorited")),
        "is_pinned": bool(raw.get("is_pinned")),
        "published_at": _published_at(raw.get("timestamp")),
    }


def normalize_comments(dump: Mapping[str, Any]) -> dict[str, Any]:
    return {"comments": [_comment(raw) for raw in dump.get("comments") or []]}


def parse_json3_transcript(
    payload: Mapping[str, Any], *, language: str, is_automatic: bool
) -> dict[str, Any]:
    """A json3 caption body -> `{language, is_automatic, full_text, segments}`."""
    segments: list[dict[str, Any]] = []
    for event in payload.get("events") or []:
        text = "".join(run.get("utf8", "") for run in event.get("segs") or [])
        if not text.strip():
            continue
        segments.append(
            {
                "start_seconds": event.get("tStartMs", 0) / 1000,
                "duration_seconds": event.get("dDurationMs", 0) / 1000,
                "text": text,
            }
        )
    return {
        "language": language,
        "is_automatic": is_automatic,
        "segments": segments,
        "full_text": "\n".join(s["text"] for s in segments),
    }


__all__ = [
    "normalize_listing",
    "normalize_video_metadata",
    "normalize_comments",
    "parse_json3_transcript",
]
