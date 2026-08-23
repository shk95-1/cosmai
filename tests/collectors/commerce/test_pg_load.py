"""A ranking run, loaded into a real Postgres schema built from contracts/ddl/current/app.trend_radar.sql
(tests/conftest.py's trend_radar_schema fixture -- table-shape completion bar for #7). Proves the
whole path: engine.collect -> Repository upserts -> run/fetch_log recorded, and that a re-run of the
same hour is a no-op on the natural key."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from collectors.commerce.contract import Payload
from collectors.commerce.engine import collect
from collectors.commerce.models import Dataset
from collectors.commerce.sources.hwahae import Hwahae
from collectors.commerce.storage import db as storage_db
from collectors.commerce.storage.tables import fetch_log, product, rank_snapshot
from collectors.commerce.storage.tables import run as run_table

pytestmark = pytest.mark.postgres

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hwahae"
AT = datetime(2026, 8, 24, 3, tzinfo=UTC)


class _FakeFetcher:
    """No network: hands back the saved ranking-page fixture for every fetch, and records nothing else."""

    def fetch(self, fetch) -> Payload:
        body = (FIXTURES / "ranking/home.html").read_bytes()
        return Payload(fetch=fetch, status=200, body=body, final_url=fetch.url, headers={}, elapsed_ms=3)


class _JournalStub:
    def __init__(self, engine: sa.Engine, run_id: uuid.UUID) -> None:
        self._engine = engine
        self._run_id = run_id

    def record(self, attempt) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.insert(fetch_log).values(
                    run_id=self._run_id,
                    at=AT,
                    source=attempt.source,
                    dataset=str(attempt.dataset),
                    url=attempt.url,
                    status=attempt.status,
                    attempt=attempt.attempt,
                    elapsed_ms=attempt.elapsed_ms,
                    bytes=attempt.bytes,
                    error=attempt.error,
                )
            )


class _EngineSink:
    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine

    def write(self, records) -> None:
        with self._engine.begin() as conn:
            storage_db.write_records(conn, records)


def _run_id_row(engine: sa.Engine) -> uuid.UUID:
    run_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.insert(run_table).values(
                id=run_id,
                captured_at=AT,
                started_at=AT,
                status="running",
                sources="hwahae",
                datasets="ranking",
                collector_version="test",
            )
        )
    return run_id


def test_a_ranking_run_upserts_rank_and_product_rows(trend_radar_schema: str):
    engine = storage_db.create_engine(trend_radar_schema)
    run_id = _run_id_row(engine)
    journal = _JournalStub(engine, run_id)

    report = collect(
        sources=[Hwahae()],
        dataset=Dataset.RANKING,
        sink=_EngineSink(engine),
        captured_at=AT,
        fetcher=_FakeFetcher(),
        journal=journal,
    )
    assert report.ok
    assert report.sources["hwahae"].records > 0

    with engine.connect() as conn:
        ranks = conn.execute(
            sa.select(rank_snapshot.c.product_key).where(rank_snapshot.c.source == "hwahae")
        ).fetchall()
        products = conn.execute(
            sa.select(product.c.product_key).where(product.c.source == "hwahae")
        ).fetchall()
        attempts = conn.execute(sa.select(fetch_log.c.id).where(fetch_log.c.run_id == run_id)).fetchall()
    assert ranks
    assert products
    assert attempts

    # Re-running the same hour is a no-op on the natural key (DO NOTHING for rank_snapshot).
    report2 = collect(
        sources=[Hwahae()],
        dataset=Dataset.RANKING,
        sink=_EngineSink(engine),
        captured_at=AT,
        fetcher=_FakeFetcher(),
        journal=journal,
    )
    assert report2.ok
    with engine.connect() as conn:
        ranks_again = conn.execute(
            sa.select(rank_snapshot.c.product_key).where(rank_snapshot.c.source == "hwahae")
        ).fetchall()
    assert len(ranks_again) == len(ranks)

    engine.dispose()
