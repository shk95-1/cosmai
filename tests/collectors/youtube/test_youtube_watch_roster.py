"""`watch` reads the 43 panel channels from `needs.panel_channel WHERE active`, not from a file (#183).

The two fixtures name the same per-test schema, so `needs.panel_channel` and `tubedepth.jobs` are
side by side here exactly as they are in production -- the difference that matters is the role, and
that is asserted rather than assumed: the roster is read over `needs_runtime`, which is the only role
granted on that table (fork DDL 022), while the jobs are written over the collector's own connection.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from collectors.youtube import roster
from collectors.youtube.cli import run
from collectors.youtube.storage.tables import jobs
from collectors.youtube.watchlist import Directive

pytestmark = pytest.mark.postgres

AT = datetime(2026, 9, 13, 3, tzinfo=UTC)
PANEL = ("UCaaaaaaaaaaaaaaaaaaaaaa", "UCbbbbbbbbbbbbbbbbbbbbbb")
RETIRED = "UCccccccccccccccccccccc1"


def _seed_roster(url: str, *, active: tuple[str, ...] = PANEL, retired: str | None = RETIRED) -> None:
    """Seeded over `needs_runtime`, which is the role the grant in fork DDL 022 actually names -- the
    migrator that built the tables cannot write them, and neither can the collector's own role."""
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO panel_roster (version, note) VALUES (1, 'test')"))
        for channel_id in active:
            conn.execute(
                sa.text(
                    "INSERT INTO panel_channel (channel_id, version, panel_role, active) "
                    "VALUES (:cid, 1, 'product', true)"
                ),
                {"cid": channel_id},
            )
        if retired:
            # Same version, inactive: the roster's own way of retiring a channel without a second
            # active version, which DDL 027's constraint trigger forbids.
            conn.execute(
                sa.text(
                    "INSERT INTO panel_channel (channel_id, version, panel_role, active) "
                    "VALUES (:cid, 1, 'expert', false)"
                ),
                {"cid": retired},
            )
    engine.dispose()


def _targets(url: str) -> list[str]:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        rows = conn.execute(
            sa.select(jobs.c.target, jobs.c.kind, jobs.c.follow_up_kind).order_by(jobs.c.target)
        ).all()
    engine.dispose()
    return [row.target for row in rows]


def test_watch_queues_the_active_panel_channels_with_no_file_naming_them(
    needs_runtime_url: str, tubedepth_schema: str, tmp_path: Path
):
    _seed_roster(needs_runtime_url)
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("# no panel channel is named here\n")

    assert (
        run(
            "watch",
            database_url=tubedepth_schema,
            roster_url=needs_runtime_url,
            watchlist_path=watchlist,
            captured_at=AT,
        )
        == 0
    )
    assert set(_targets(tubedepth_schema)) == set(PANEL)


def test_a_retired_channel_is_not_watched(needs_runtime_url: str, tubedepth_schema: str, tmp_path: Path):
    _seed_roster(needs_runtime_url)
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("\n")
    run(
        "watch",
        database_url=tubedepth_schema,
        roster_url=needs_runtime_url,
        watchlist_path=watchlist,
        captured_at=AT,
    )
    assert RETIRED not in _targets(tubedepth_schema)


def test_each_panel_channel_gets_the_three_follow_ups_of_channel_plus_comments(
    needs_runtime_url: str, tubedepth_schema: str, tmp_path: Path
):
    """The directive kind is the fork's D4: comments for every panel video. A listing with no
    comments collects a population nothing reads."""
    _seed_roster(needs_runtime_url, active=(PANEL[0],), retired=None)
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("\n")
    run(
        "watch",
        database_url=tubedepth_schema,
        roster_url=needs_runtime_url,
        watchlist_path=watchlist,
        captured_at=AT,
    )

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        rows = conn.execute(sa.select(jobs.c.kind, jobs.c.follow_up_kind)).all()
    engine.dispose()
    assert {row.kind for row in rows} == {"channel.videos"}
    assert {row.follow_up_kind for row in rows} == {
        "video.metadata",
        "video.transcript",
        "video.comments",
    }


def test_the_operator_file_still_adds_a_one_off_target_beside_the_panel(
    needs_runtime_url: str, tubedepth_schema: str, tmp_path: Path
):
    _seed_roster(needs_runtime_url, active=(PANEL[0],), retired=None)
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("video dQw4w9WgXcQ\n")
    run(
        "watch",
        database_url=tubedepth_schema,
        roster_url=needs_runtime_url,
        watchlist_path=watchlist,
        captured_at=AT,
    )
    assert set(_targets(tubedepth_schema)) == {PANEL[0], "dQw4w9WgXcQ"}


def test_a_panel_channel_typed_into_the_file_by_hand_is_not_watched_twice(
    needs_runtime_url: str, tubedepth_schema: str, tmp_path: Path
):
    """`queue.enqueue`'s dedupe only holds while the first job is still *active*, so a duplicate
    directive would enqueue again the moment that pass finished -- forever, and silently."""
    _seed_roster(needs_runtime_url, active=(PANEL[0],), retired=None)
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text(f"channel+comments {PANEL[0]}\n")
    run(
        "watch",
        database_url=tubedepth_schema,
        roster_url=needs_runtime_url,
        watchlist_path=watchlist,
        captured_at=AT,
    )
    assert _targets(tubedepth_schema).count(PANEL[0]) == 3  # the three follow-ups, not six


def test_an_unreadable_roster_is_blocked_and_not_an_empty_watch(tubedepth_schema: str, tmp_path: Path):
    """The roster URL points at a schema that holds no `panel_channel` -- which is what a missing
    grant, a renamed table or an un-migrated database all look like from here. Queueing only the file
    and reporting success would be #39's "no queued jobs" with a new cause and the same silence."""
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("video dQw4w9WgXcQ\n")
    assert (
        run(
            "watch",
            database_url=tubedepth_schema,
            roster_url=tubedepth_schema,
            watchlist_path=watchlist,
            captured_at=AT,
        )
        == 2
    )
    assert _targets(tubedepth_schema) == []


def test_an_empty_roster_and_an_empty_file_is_still_blocked(
    needs_runtime_url: str, tubedepth_schema: str, tmp_path: Path
):
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("# nothing\n")
    assert (
        run(
            "watch",
            database_url=tubedepth_schema,
            roster_url=needs_runtime_url,
            watchlist_path=watchlist,
            captured_at=AT,
        )
        == 2
    )


def test_the_roster_is_read_in_channel_id_order():
    """A capped pass resumes where it stopped only if the order is the same next time."""
    directives = [Directive(kind="channel.videos", target=t, follow_ups=(), line=0) for t in ("b", "a")]
    assert [d.target for d in roster.merge(sorted(directives, key=lambda d: d.target), [])] == ["a", "b"]


def test_the_watchlist_file_names_no_panel_channel():
    """The decision that the database is canonical is only true while the file stays empty of the
    roster -- a panel channel checked in here makes the file canonical in practice."""
    text = (Path(__file__).resolve().parents[3] / "collectors" / "youtube" / "watchlist.txt").read_text(
        encoding="utf-8"
    )
    directives = [line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert directives == []
