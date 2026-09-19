"""The day's Data API spend, read back from what this collector already wrote down (#259).

`ROUTES.data_api.max_requests_per_run` bounds one `run()`, and `work` is a cron invocation every
five minutes -- so an in-memory counter bounds nothing at all: 288 invocations a day may each spend
the whole per-run budget and no two of them share a process. A bound on a *day* therefore has to be
read out of the database at the start of every run, and the row this collector already writes once
per fetch is `tubedepth.artifacts`: `fetch_route` (DDL 005) names which source answered and
`fetched_at` the instant it did. No new table and no new column -- what a fetch cost is
reconstructed from its kind.

Two reconstructions, because the two Data API kinds cost differently:

  video.metadata   `ceil(rows / PAGE_SIZE)`. Since #259 one `videos.list` call carries up to 50 ids,
                   so up to 50 artifacts share one request. Over a day this undercounts by at most
                   one request per run -- the run whose last chunk was not full -- which is 287 at a
                   five-minute cadence.
  a listing walk   `LISTING_REQUESTS_PER_WALK`: one `channels.list` plus the playlist pages. This one
                   is an estimate and is the known gap in this guard: the walk's real page count is
                   in nothing the row carries, and giving the row a column for it is DDL. A channel
                   with more videos inside `LISTING_WINDOW_START` than the estimate covers is
                   undercharged, up to the walk's own ceiling of `MAX_LISTING_ITEMS / PAGE_SIZE + 1`.
                   `scope.json` says so beside the number.

The unit is a request, not a quota unit, for the reason `scope.json`'s ROUTES_note already gives:
`videos.list`, `playlistItems.list` and `channels.list` all cost one unit a call, so on the routes
this collector actually takes the two coincide. `search.list` is the exception (100 units a page)
and no panel directive takes it.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy import Connection

from collectors.youtube.models import LISTING_REQUESTS_PER_WALK, ROUTES, VIDEO_METADATA_KIND
from collectors.youtube.storage.tables import artifacts
from collectors.youtube.transport import LISTING_KINDS, PAGE_SIZE, Route

#: Google's Data API quota resets at midnight **Pacific Time**, not UTC. A UTC day would put the
#: reset nine hours away from where Google puts it, which on a Korean cadence is most of a working
#: evening on the wrong side of the boundary.
QUOTA_RESET_ZONE = ZoneInfo("America/Los_Angeles")

#: Beside `max_requests_per_run`, never instead of it: that one is the per-invocation circuit
#: breaker and this one is the day's ceiling.
MAX_REQUESTS_PER_DAY = int(ROUTES[Route.DATA_API]["max_requests_per_day"])


def day_start(now: datetime) -> datetime:
    """Midnight of the quota day `now` falls in, as an aware instant."""
    return now.astimezone(QUOTA_RESET_ZONE).replace(hour=0, minute=0, second=0, microsecond=0)


def requests_spent_today(conn: Connection, *, now: datetime) -> int:
    """How many Data API requests the artifacts of this quota day account for."""
    rows = conn.execute(
        sa.select(artifacts.c.kind, sa.func.count())
        .where(
            artifacts.c.fetch_route == Route.DATA_API.value,
            artifacts.c.fetched_at >= day_start(now),
        )
        .group_by(artifacts.c.kind)
    ).all()
    spent = 0
    for kind, count in rows:
        if kind == VIDEO_METADATA_KIND:
            spent += -(-count // PAGE_SIZE)
        elif kind in LISTING_KINDS:
            spent += count * LISTING_REQUESTS_PER_WALK
        else:
            # An unknown kind that still answered over this route cost at least one request, and
            # charging it one is the reading that cannot pretend a spend away.
            spent += count
    return spent


def remaining_requests(conn: Connection, *, now: datetime) -> int:
    """What is left of the day, floored at zero -- never a negative budget."""
    return max(MAX_REQUESTS_PER_DAY - requests_spent_today(conn, now=now), 0)


__all__ = [
    "MAX_REQUESTS_PER_DAY",
    "QUOTA_RESET_ZONE",
    "day_start",
    "remaining_requests",
    "requests_spent_today",
]
