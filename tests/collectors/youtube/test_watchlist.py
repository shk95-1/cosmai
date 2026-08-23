from __future__ import annotations

from pathlib import Path

import pytest

from collectors.youtube.models import MAX_FOLLOWUPS_PER_VIDEO
from collectors.youtube.watchlist import DIRECTIVES, WatchlistError, read_watchlist


def test_reads_ordinary_lines(tmp_path: Path):
    path = tmp_path / "watch.txt"
    path.write_text("video dQw4w9WgXcQ\n# a comment\n\nchannel @director_pihyunjung\n")
    directives = read_watchlist(path)
    assert [d.kind for d in directives] == ["video.metadata", "channel.videos"]
    assert directives[1].follow_ups == ("video.metadata", "video.transcript")


def test_channel_plus_comments_names_all_three_follow_ups():
    _, follow_ups = DIRECTIVES["channel+comments"]
    assert follow_ups == ("video.metadata", "video.transcript", "video.comments")


def test_max_followups_per_video_bounds_the_longest_directive():
    # scope.json's cap is meant to cover the richest directive this table can produce -- if a
    # directive ever named a fourth follow-up kind, MAX_FOLLOWUPS_PER_VIDEO would need to move with it.
    longest = max(len(follow_ups) for _, follow_ups in DIRECTIVES.values())
    assert longest == MAX_FOLLOWUPS_PER_VIDEO


def test_unknown_directive_names_the_line(tmp_path: Path):
    path = tmp_path / "watch.txt"
    path.write_text("bogus something\n")
    with pytest.raises(WatchlistError, match="line 1"):
        read_watchlist(path)


def test_empty_target_is_refused(tmp_path: Path):
    path = tmp_path / "watch.txt"
    path.write_text("video   \n")
    with pytest.raises(WatchlistError):
        read_watchlist(path)
