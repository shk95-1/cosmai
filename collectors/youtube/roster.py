"""Where `watch` gets the panel channels: `needs.panel_channel WHERE active`, read at boot.

The database is canonical for the 43 panel channels and the file is not (the `Autonomous decision`
of 2026-09-13 on #183). The rejected option was a command that regenerates
`collectors/youtube/watchlist.txt` from the roster: it makes the file canonical in practice and
drifts from the roster the moment somebody forgets to regenerate it, and it puts a generated file
under version control for a value that already has a versioned home with a single-active-version
guard. `watchlist.txt` stays as the operator override for a one-off target -- a single video, a
search -- and names no panel channel.

`WHERE active` is unambiguous by construction rather than by convention: DDL 027's constraint
trigger `panel_channel_one_active_version` refuses a second active version at commit, so "the active
roster" is one version and the query needs no version argument.

**The connection is not this collector's own.** `collectors/youtube/storage/db.py` connects as
`tubedepth_runtime`, which has no grant on `needs.panel_channel` -- the grant in fork DDL 022 names
`needs_runtime` alone. So the roster is read over `db.runtime`'s `needs_runtime` URL, the same role
and secret (`NEEDS_DB_RUNTIME`, contracts/secrets.md) every analysis stage already uses, on its own
short-lived engine. The alternative was a new GRANT on a table the fork owns, which upstream may not
write (contracts/ownership.md rule 2).

The target written per directive is `channel_id` (UC…), not `handle`: handles are renameable,
channel ids are not, and the roster's primary key is (version, channel_id).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

from collectors.youtube.watchlist import DIRECTIVES, Directive

#: Comments for every panel video, no dictionary filter in the collector (the fork's D4, recorded on
#: #183 on 2026-09-12). `watchlist.py` already maps this name to `channel.videos` plus the three
#: follow-ups video.metadata · video.transcript · video.comments.
PANEL_DIRECTIVE = "channel+comments"

panel_channel = sa.table(
    "panel_channel",
    sa.column("channel_id", sa.Text),
    sa.column("version", sa.Integer),
    sa.column("panel_role", sa.Text),
    sa.column("active", sa.Boolean),
    schema=None,
)


class RosterError(RuntimeError):
    """The roster could not be read. `watch` treats this as blocked rather than as an empty roster:
    watching nothing because a query failed is the #39 failure -- a profile-gated `watch` behind a
    0-line list -- reappearing with a different cause and the same silence."""


def read_panel_channels(conn: Connection) -> list[Directive]:
    """Every active panel channel as a `channel+comments` directive, in channel_id order.

    Ordered so that two `watch` passes over an unchanged roster enqueue in the same order, which is
    what makes a capped pass (MAX_QUEUE_DEPTH) resume from where it stopped instead of re-walking a
    different arbitrary prefix of the panel.
    """
    kind, follow_ups = DIRECTIVES[PANEL_DIRECTIVE]
    try:
        rows = conn.execute(
            sa.select(panel_channel.c.channel_id)
            .where(panel_channel.c.active.is_(True))
            .order_by(panel_channel.c.channel_id)
        ).all()
    except SQLAlchemyError as error:
        raise RosterError(f"cannot read the panel roster: {type(error).__name__}") from error
    return [
        # `line=0`: these directives come from no file, and every message that quotes a line number
        # names its source alongside it, so 0 reads as "not a line" rather than as line one.
        Directive(kind=kind, target=str(channel_id), follow_ups=follow_ups, line=0)
        for (channel_id,) in rows
    ]


def merge(roster: Sequence[Directive], file_directives: Sequence[Directive]) -> list[Directive]:
    """The roster first, then whatever the operator file adds that the roster does not already name.

    A duplicate is dropped here rather than left to `queue.enqueue`'s natural-key dedupe: dedupe
    there is per (kind, target, follow_up_kind) against rows that are still *active*, so a panel
    channel typed into the file by hand would enqueue a second time the moment the first pass
    finished, forever, and nothing would say why.
    """
    seen = {(directive.kind, directive.target) for directive in roster}
    merged = list(roster)
    for directive in file_directives:
        if (directive.kind, directive.target) in seen:
            continue
        seen.add((directive.kind, directive.target))
        merged.append(directive)
    return merged


__all__ = ["PANEL_DIRECTIVE", "RosterError", "read_panel_channels", "merge"]
