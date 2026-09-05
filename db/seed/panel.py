"""The panel roster's 43 channels -> `needs.panel_roster` + `needs.panel_channel` (fork #31).

Every ratio ydc produces uses this roster as its denominator. The source is
`eval/panel/channels_v1.csv`'s 11 columns, and only six of them go into the table -- what gets dropped
and why is carried by `contracts/formats.md` §Panel roster CSV.

A version has the same shape as the lexicons (`entity_lexicon`/`aspect_lexicon`): loading assigns a
`version` and swaps it in through `active` (contracts/versioning.md). But unlike a lexicon, this table
is **a denominator**, so if two versions were ever active a query hitting the
`(version, panel_role) WHERE active` partial index would count 86 instead of 43. That invariant is
carried by this file, not the DDL (#3 review L6): "the active rows have exactly one distinct version" is
a sentence a partial unique index cannot express, and the single statement
`SET active = (version = %s)` makes it atomic.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, LiteralString

import psycopg

from db.seed._common import counts, opt, read_csv

TABLES = ("panel_roster", "panel_channel")

PANEL_VERSION = 1
PANEL_NOTE = "seed:channels_v1"
PANEL_CSV = Path("panel") / "channels_v1.csv"
# The order rows() emits. Must match CHANNEL_SQL's placeholder order.
COLUMNS = ("channel_id", "panel_role", "handle", "channel_title", "role_basis", "source_list")

ROSTER_SQL: LiteralString = """
INSERT INTO panel_roster (version, note) VALUES (%s, %s)
ON CONFLICT (version) DO NOTHING
"""
# The same DO NOTHING as a lexicon: the roster's content changes through the next version, not a
# re-load (formats.md).
CHANNEL_SQL: LiteralString = """
INSERT INTO panel_channel
  (version, channel_id, panel_role, handle, channel_title, role_basis, source_list)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (version, channel_id) DO NOTHING
"""
# The WHERE is what keeps a re-run from rewriting a row that already has the same value -- rowcount
# reads as "0 changed".
ACTIVATE_SQL: LiteralString = """
UPDATE panel_channel SET active = (version = %s)
WHERE active IS DISTINCT FROM (version = %s)
"""
VERSION_COUNT_SQL: LiteralString = "SELECT count(*) FROM panel_channel WHERE version = %s"
ACTIVE_SQL: LiteralString = "SELECT DISTINCT version FROM panel_channel WHERE active"


def rows(source_dir: Path) -> list[tuple[Any, ...]]:
    """One CSV line -> one row in COLUMNS order. The other five columns are dropped right here."""
    return [
        (
            r["channel_id"],
            r["panel_role"],
            opt(r["handle"]),
            r["channel_title"],
            r["role_basis"],
            r["source_list"],
        )
        for r in read_csv(source_dir / PANEL_CSV)
    ]


def insert(cur: psycopg.Cursor[Any], panel_rows: Sequence[Sequence[Any]], version: int, note: str) -> int:
    """Inserts one version row and its roster. The parent a roster row's FK points at must exist
    first."""
    cur.execute(ROSTER_SQL, (version, note))
    if not panel_rows:
        return 0
    cur.executemany(CHANNEL_SQL, [(version, *row) for row in panel_rows])
    return max(cur.rowcount, 0)


def activate(cur: psycopg.Cursor[Any], version: int) -> int:
    """Turns on only this version. Activating an empty version has `SET active = (version = n)` turn
    the whole roster off, and a query left with a denominator of 0 cannot produce a ratio with no error
    raised -- so this is rejected when there are no rows (the same as db/lexicon.activate)."""
    cur.execute(VERSION_COUNT_SQL, (version,))
    row = cur.fetchone()
    if not (row and row[0]):
        raise LookupError(f"panel_channel has no rows at version {version}; nothing to activate")
    cur.execute(ACTIVATE_SQL, (version, version))
    return max(cur.rowcount, 0)


def active_version(cur: psycopg.Cursor[Any]) -> int | None:
    """The active version. With two, this stops rather than pick an answer -- picking one there would
    produce 43+43, a denominator counted twice.

    027's deferred constraint trigger now refuses this state at commit; this Python check is the
    read-side guard for a transaction still in flight, where the trigger has not fired yet.
    """
    cur.execute(ACTIVE_SQL)
    versions = sorted(int(v) for (v,) in cur.fetchall())
    if len(versions) > 1:
        raise ValueError(f"panel_channel has {len(versions)} active versions: {versions}")
    return versions[0] if versions else None


def load(cur: psycopg.Cursor[Any], source_dir: Path) -> dict[str, int]:
    insert(cur, rows(source_dir), PANEL_VERSION, PANEL_NOTE)
    activate(cur, PANEL_VERSION)
    return counts(cur, TABLES)
