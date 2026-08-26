"""F-2 (issue #8 수정 라운드 1): two concurrent claimants must not land on the same job -- the
archived worker.py held `FOR UPDATE SKIP LOCKED` for exactly this, and `_claim`'s original
select-then-update in this port did not. Overlapping crons (a slow `work` pass still running when
the next tick fires) or a live worker daemon started alongside the batch CLI (#10) are the real
paths that turn "single consumer" from an assumption into a race."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from collectors.youtube import queue
from collectors.youtube.cli import _claim

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 8, 24, 3, tzinfo=UTC)


def test_two_concurrent_claims_land_on_different_jobs(tubedepth_schema: str):
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as seed_conn:
        queue.enqueue(seed_conn, kind="video.metadata", target="a", now=NOW)
        queue.enqueue(seed_conn, kind="video.metadata", target="b", now=NOW)

    # Two live transactions at once, neither committed until both have claimed -- what a single
    # `with engine.begin() as conn:` per `run()` call can never exercise, since it commits before
    # returning. `SKIP LOCKED` is exactly what makes the second claim skip the first's locked row
    # instead of blocking on it or, worse, reading it as still queued.
    conn1 = engine.connect()
    conn2 = engine.connect()
    try:
        txn1 = conn1.begin()
        txn2 = conn2.begin()
        claimed1 = _claim(conn1, limit=1, now=NOW)
        claimed2 = _claim(conn2, limit=1, now=NOW)

        assert len(claimed1) == 1
        assert len(claimed2) == 1
        assert claimed1[0].identifier != claimed2[0].identifier

        txn1.commit()
        txn2.commit()
    finally:
        conn1.close()
        conn2.close()
        engine.dispose()


def test_claim_marks_rows_running_so_a_third_claim_gets_nothing(tubedepth_schema: str):
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        queue.enqueue(conn, kind="video.metadata", target="only-one", now=NOW)
    with engine.begin() as conn:
        first = _claim(conn, limit=10, now=NOW)
        second = _claim(conn, limit=10, now=NOW)
    assert len(first) == 1
    assert second == []
    engine.dispose()
