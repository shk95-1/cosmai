"""두 analyze 실행이 서로를 알아보는 한 자리 — 겹치면 뒤에 온 쪽이 양보한다 (#16).

`collectors/commerce/storage/locks.py` 가 소스별로 세운 것과 같은 관용구다: Postgres 세션 스코프
어드바이저리 락, `pg_try_advisory_lock` 이라 기다리지 않고, 프로세스가 죽으면 락도 같이 간다.
셋이 다르다.

  - **입도가 하나다.** 소스 락은 소스마다지만 analyze 는 전역 하나다. polarity 는 달마다 DELETE 를
    커밋한 뒤 페이지별로 다시 쓰고(analysis/polarity/pipeline.py `replace_stale`), aggregate 는
    `extractor_version` 하나만 걸고 need_mention 전량을 여러 트랜잭션에 나눠 읽는다
    (analysis/aggregate/pipeline.py `load_needs`). 한쪽이 쓰는 자리와 다른 쪽이 읽는 자리를 가르는
    분할이 없다 — scope 별로 잘라도 aggregate 는 여전히 모든 scope 를 읽고, 단계별로 잘라도
    `polarity --scope 선블록` 과 `aggregate` 는 서로 다른 단계다. 어느 쪽으로 좁혀도 리뷰가 찾은 그
    끼어들기가 그대로 남는다. 비용은 유계다: analyze 는 외부 fetch 가 없는 DB 전용 작업이고 크론은
    하루 한 줄이다 (contracts/entrypoints.md §스케줄).
  - **The working connection holds the lock.** A collector borrows a connection per worker thread, so it had
    to open a dedicated lock connection, and then had to keep that connection from sitting idle for the whole
    walk by using AUTOCOMMIT. analyze is a batch that runs on one connection from the start, so that
    connection is the session — a session-scoped lock survives a commit, so it need not be retaken on every
    batch commit, and idle_in_transaction does not reach it either (the transaction that took the lock is
    closed right away). It does not eat further into needs_runtime's CONNECTION LIMIT.
  - **There is one more reason not to wait.** A gemma4 pass a person runs by hand takes 2.5 to 4 hours. The
    05:00 queued behind it is still in the queue when the next 05:00 comes, and it takes the lock at exactly
    the moment a person is about to start the next pass. If it cannot take it, it skips, leaves a reason and
    ends partial (exit code 1) — every step is a natural-key upsert, so the night it skipped is taken as it is
    by the next run (contracts/entrypoints.md §Analysis).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, LiteralString

import psycopg

__all__ = ["ANALYZE", "LOCK_CLASS", "advisory_key", "analyze_lock"]

# The issue number that brought the lock in is the namespace -- the same convention as the collectors' 10
# (#10), and since the classid differs the two locks cannot collide by any means. It is also a different
# space from the one-argument form of pricing.py (6).
LOCK_CLASS = 16
ANALYZE = "analyze"

TAKE: LiteralString = "SELECT pg_try_advisory_lock(%s, %s)"
GIVE_BACK: LiteralString = "SELECT pg_advisory_unlock(%s, %s)"


def advisory_key(name: str) -> tuple[int, int]:
    """The (classid, objid) this lock lives at. It is blake2b because `hash()` is salted per process -- the
    party to coordinate with is another process, and if 05:00 and 08:00 lock different numbers nothing is
    coordinated at all. objid is int4, so four bytes."""
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=4).digest()
    return LOCK_CLASS, int.from_bytes(digest, "big", signed=True)


@contextmanager
def analyze_lock(conn: psycopg.Connection[Any], name: str = ANALYZE) -> Iterator[bool]:
    """Gives True when it took the lock and returns it at the end of the block. False when it did not -- the
    caller yields."""
    classid, objid = advisory_key(name)
    with conn.cursor() as cur:
        cur.execute(TAKE, (classid, objid))
        row = cur.fetchone()
    held = bool(row and row[0])
    # The lock belongs to the session and outlives this commit -- left open, idle_in_transaction 15s cuts the
    # session.
    conn.commit()
    try:
        yield held
    finally:
        if held:
            try:
                conn.rollback()  # with a failed transaction left by the stage, no statement can go out here.
                with conn.cursor() as cur:
                    cur.execute(GIVE_BACK, (classid, objid))
                    row = cur.fetchone()
                conn.commit()
                # The same one line as the collectors: on a 2.5-4 hour run this is the only evidence after
                # the fact of "the two may have overlapped" -- throw the return value away and nobody knows.
                if not (row and row[0]):
                    print(
                        f"{name} lock: pg_advisory_unlock says this session did not hold it, so the "
                        "lock went sometime during the run and the run was not told"
                    )
            # If the session is already gone, so is the lock -- one line in the cron mail instead of a
            # traceback.
            except psycopg.Error as unreachable:
                print(f"analyze lock: not given back -- {str(unreachable).splitlines()[0]}")
