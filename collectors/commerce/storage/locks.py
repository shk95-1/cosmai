"""One source, one walker -- across processes, not just across threads.

`collect()` builds a `Gate` per lane, so a source's rate policy is only ever enforced inside one
process. Two cron lines that overlap (`0 * * * * ranking` runs every hour; a daily walk takes 3.4 to
29 minutes) therefore hit the same site at twice its declared rate, and oliveyoung -- the browser
transport -- is on both lines. This is the coordination above the gate (#10 §A-8-1).

Three properties are why it is a Postgres *session*-scope advisory lock and not something else:

  - it is not waited for. `pg_try_advisory_lock` answers now; a run that queued behind an hourly
    ranking walk would still be queued when the next hour's cron line started.
  - it outlives a transaction. A walk is many transactions and long stretches of neither, so
    `pg_advisory_xact_lock` (what analysis/polarity/pricing.py uses) would end the moment the first
    batch committed, not when the walk did.
  - it dies with the process. A run killed mid-walk leaves no row to clean up: Postgres drops the
    lock when the connection goes. That is the whole reason the connection is held open here for the
    length of the walk rather than borrowed per statement.

The last of those is also the trap. `db/bootstrap.sql` gives the runtime role
`idle_in_transaction_session_timeout = 15s` and `transaction_timeout = 60s`, so a lock taken inside
an open transaction is a lock Postgres takes back a few seconds into the walk -- silently, while the
run keeps walking. The connection is put in AUTOCOMMIT for exactly that reason: it sits `idle`, never
`idle in transaction`, and neither limit can reach it. `statement_timeout = 30s` is no threat either;
every statement sent here returns immediately.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError

# The namespace half of the key, following analysis/polarity/pricing.py's convention of naming the
# issue that introduced the lock. `pg_try_advisory_lock(classid, objid)` and pricing's one-argument
# `pg_advisory_xact_lock(6)` cannot collide whatever the numbers are -- Postgres keeps the two forms
# in separate spaces (pg_locks.objsubid is 1 for the one-argument form and 2 for ours) -- but a
# namespace of our own also keeps the next two-argument user of this database out of the sources' keys.
LOCK_CLASS = 10

TAKE = sa.text("SELECT pg_try_advisory_lock(:classid, :objid)")
GIVE_BACK = sa.text("SELECT pg_advisory_unlock(:classid, :objid)")


def advisory_key(source_key: str) -> tuple[int, int]:
    """The (classid, objid) pair one source's lock lives at.

    blake2b rather than `hash()`: the whole point is that two *processes* agree, and Python salts
    `hash()` per process (PYTHONHASHSEED), so a run started at 04:15 would lock a different number
    from the one started at 04:00 and coordinate nothing. Four bytes because objid is an int4.

    Two of the four registered sources colliding at 32 bits is about 1.4e-9, and a collision would
    cost throughput (one source needlessly yielding to another), never correctness. It is not left to
    that probability anyway: the four keys are asserted distinct in
    tests/collectors/commerce/test_source_lock.py.
    """
    digest = hashlib.blake2b(source_key.encode("utf-8"), digest_size=4).digest()
    return LOCK_CLASS, int.from_bytes(digest, "big", signed=True)


class PostgresSourceLock:
    """`SourceLock` over a real database. One connection per source, held for that source's walk."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def __call__(self, source_key: str) -> Iterator[bool]:
        classid, objid = advisory_key(source_key)
        keys = {"classid": classid, "objid": objid}
        # AUTOCOMMIT is load-bearing, not tidiness -- see the module docstring's third paragraph.
        connection = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        held = False
        try:
            held = bool(connection.execute(TAKE, keys).scalar_one())
            yield held
        finally:
            try:
                if held:
                    connection.execute(GIVE_BACK, keys)
            except DBAPIError:
                # A session that has gone away released this lock when it went; failing to say so
                # again must not bury whatever actually ended the walk.
                pass
            finally:
                connection.close()


__all__ = ["LOCK_CLASS", "advisory_key", "PostgresSourceLock"]
