"""`engine.Sink` is called from several threads at once, and nothing was holding that.

#24: a lane runs `policy.concurrency` workers over one sink and `_Lane._work` calls `sink.write`
outside `_lock`. Today's only production implementation (`cli._EngineSink`) is safe because it takes
a connection out of the pool per call -- but that was a property of the implementation, not a rule
anyone had written down, and `storage.db.write_records(connection, ...)` invites a caller to open one
connection and share it. This pins the rule to the production class rather than to a copy of it: the
duplicate `_EngineSink` in test_pg_load.py is what drift looks like.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from collectors.commerce.cli import _EngineSink
from collectors.commerce.models import ProductRecord
from collectors.commerce.storage import db as storage_db
from collectors.commerce.storage.tables import product

pytestmark = pytest.mark.postgres

AT = datetime(2026, 8, 24, 3, tzinfo=UTC)

# Two, and it is a budget rather than a taste: `trend_radar_schema` hands back the migrator URL and
# `db/bootstrap.sql:12` gives that role CONNECTION LIMIT 2. A pooled sink checks out one connection
# per concurrent write, so three writers here do not exercise more concurrency -- they get
# `FATAL: too many connections for role`, which then breaks the fixture teardown and errors whatever
# test runs next. Two is also the real number: hwahae is the only source shipping concurrency > 1,
# and it ships 2 (`sources/hwahae.py`).
WRITERS = 2
PER_WRITER = 25
# Failure budget, not a pace: nothing here sleeps, so reaching either of these means a thread is
# stuck rather than slow.
BARRIER_TIMEOUT_S = 10.0
JOIN_TIMEOUT_S = 30.0


def _batch(writer: int) -> list[ProductRecord]:
    # Distinct natural keys per writer, so a lost batch shows up as missing rows rather than being
    # absorbed by the upsert.
    return [
        ProductRecord(
            source=f"writer-{writer}",
            captured_at=AT,
            product_key=f"p-{writer}-{i}",
            name=f"product {writer}-{i}",
        )
        for i in range(PER_WRITER)
    ]


def _run_writers(write: object, *, count: int = WRITERS) -> tuple[list[BaseException], bool, list[str]]:
    """Fire `count` threads into `write` at once and report what came back.

    The rendezvous is inside the thread, immediately before the call, so the batches genuinely
    overlap -- threads that merely start together can still finish one after another, and a sink
    sharing a single connection would pass that.
    """
    ready = threading.Barrier(count, timeout=BARRIER_TIMEOUT_S)
    failures: list[BaseException] = []
    stalled = threading.Event()

    def writer(n: int) -> None:
        records = _batch(n)
        try:
            ready.wait()
        except threading.BrokenBarrierError:
            # Not swallowed: a timeout here is its own diagnosis. Letting it fall through would
            # surface later as "rows missing" and send the next reader after the wrong bug.
            stalled.set()
            return
        try:
            write(records)  # pyright: ignore[reportCallIssue]
        except BaseException as exc:  # noqa: BLE001 -- the point is that nothing may escape
            failures.append(exc)

    threads = [threading.Thread(target=writer, args=(n,), name=f"sink-writer-{n}") for n in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT_S)
    return failures, stalled.is_set(), [t.name for t in threads if t.is_alive()]


def _rows(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(product)).scalar_one()


def test_the_production_sink_survives_concurrent_writers(trend_radar_schema: str):
    engine = storage_db.create_engine(trend_radar_schema)
    try:
        failures, stalled, alive = _run_writers(_EngineSink(engine).write)

        assert not alive, f"writers never returned: {alive}"
        assert not stalled, f"the writers never overlapped: rendezvous timed out after {BARRIER_TIMEOUT_S}s"
        assert not failures, f"concurrent sink.write raised: {failures!r}"
        assert _rows(engine) == WRITERS * PER_WRITER
        with engine.connect() as conn:
            sources = conn.execute(sa.select(sa.distinct(product.c.source))).scalars().all()
        assert sorted(sources) == [f"writer-{n}" for n in range(WRITERS)]
    finally:
        # In `finally` because a leaked pool outlives the failure: the fixture's teardown opens its
        # own connection to drop the schema, and against CONNECTION LIMIT 2 it would fail too and
        # error the next test instead of just failing this one.
        engine.dispose()


def test_a_shared_connection_puts_every_writer_in_one_transaction(trend_radar_schema: str):
    """The rule earns its place only if breaking it breaks something -- and this is the break that
    can actually be shown.

    A first draft asserted that concurrent writes on one shared `Connection` fail outright. They did
    not: at two workers and short statements psycopg's own lock serialises them and everything lands.
    So that draft was claiming more than it could demonstrate, and it is gone. What a shared
    connection really costs is the transaction boundary `_EngineSink` exists to give each batch:
    every writer is inside one transaction, so one rollback takes all of their rows -- rows the run
    has already counted in `SourceReport.records` and will never write again, because the natural-key
    upsert makes a re-run of the same hour a no-op.
    """
    engine = storage_db.create_engine(trend_radar_schema)
    try:
        with engine.connect() as shared:
            with shared.begin():
                storage_db.write_records(shared, _batch(0))
            # Committed, so this batch is the control: it must survive what happens to the next one.
            with shared.begin():
                storage_db.write_records(shared, _batch(1))
                shared.rollback()
        assert _rows(engine) == PER_WRITER, "the control batch did not survive; this test is set up wrong"

        # Same two batches through the production sink, with a rollback attempted on a connection of
        # its own afterwards: it cannot reach either batch, because neither is still open.
        with engine.begin() as conn:
            conn.execute(sa.delete(product))
        sink = _EngineSink(engine)
        sink.write(_batch(0))
        sink.write(_batch(1))
        with engine.connect() as other:
            other.rollback()
        assert _rows(engine) == 2 * PER_WRITER
    finally:
        engine.dispose()
