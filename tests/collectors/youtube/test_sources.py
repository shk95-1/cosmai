"""Offline: raw fixtures (saved yt-dlp/timedtext shapes) -> the plain dicts flatten.py writes."""

from __future__ import annotations

import json
from pathlib import Path

from collectors.youtube import sources

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(*parts: str) -> dict:
    return json.loads((FIXTURES / Path(*parts)).read_text(encoding="utf-8"))


def test_normalize_listing_skips_unavailable_entries():
    dump = _load("listing", "channel-videos.json")
    listing = sources.normalize_listing(dump, source_kind="channel.videos")
    assert [v["video_id"] for v in listing["videos"]] == ["dQw4w9WgXcQ", "nfgdJyL-Jmg"]
    assert listing["skipped_count"] == 1


def test_normalize_listing_video_fields():
    dump = _load("listing", "channel-videos.json")
    listing = sources.normalize_listing(dump, source_kind="channel.videos")
    first = listing["videos"][0]
    assert first["title"] == "First video"
    assert first["duration_seconds"] == 212
    assert first["channel_id"] == "UUsome_channel_id"


def test_normalize_video_metadata():
    dump = _load("video_metadata", "dQw4w9WgXcQ.json")
    meta = sources.normalize_video_metadata(dump)
    assert meta["video_id"] == "dQw4w9WgXcQ"
    assert meta["view_count"] == 1000
    assert meta["published_date"] == "2025-08-19"


def test_normalize_comments_maps_root_parent_to_none():
    dump = _load("comments", "dQw4w9WgXcQ-top40.json")
    harvest = sources.normalize_comments(dump)
    top, reply = harvest["comments"]
    assert top["parent_id"] is None
    assert reply["parent_id"] == "UgzTop1"
    assert reply["is_hearted_by_uploader"] is True


def test_parse_json3_transcript_joins_segments_and_drops_empty_ones():
    payload = _load("transcript", "dQw4w9WgXcQ-en-json3.json")
    transcript = sources.parse_json3_transcript(payload, language="en", is_automatic=True)
    assert len(transcript["segments"]) == 2
    assert transcript["full_text"] == "Hello world\nsecond line"
