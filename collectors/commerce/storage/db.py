"""The Postgres sink, and the record of the run itself.

origin: service/trend-radar/src/trend_radar/storage/postgres.py + storage/db.py -- ported for #7,
de-async'd (sync SQLAlchemy + psycopg, matching this repo's stack) and pointed at
`collectors.commerce`'s own `trend_radar` schema via `db.secrets` instead of trend-radar's own
Settings/pydantic-settings object.

Connection credentials: `TREND_RADAR_DB_RUNTIME`, its own key (contracts/secrets.md) -- not the repo's
general `COSMA_DB_RUNTIME`. The old stack still runs `trend_radar_runtime` with its own password from
its own `.env`, and that password differs from `COSMA_DB_RUNTIME`'s, so reading the shared key here
connected as `trend_radar_runtime` with the wrong password (#29). Role names follow the schema's
existing `trend_radar_owner`/`trend_radar_runtime` convention (storage/schema.py).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import make_url

from collectors.commerce.engine import FetchAttempt, SourceReport
from collectors.commerce.storage.repository import Repository
from collectors.commerce.storage.schema import SERVICE_SCHEMA
from collectors.commerce.storage.tables import fetch_log as fetch_log_table
from collectors.commerce.storage.tables import run as run_table
from collectors.commerce.storage.tables import run_source as run_source_table
from db import secrets
from db.runtime import host_and_port

COLLECTOR_VERSION = "commerce-0.1"

# #29: this role's production password is its own, distinct from COSMA_DB_RUNTIME and from
# tubedepth_runtime's password -- no fallback to COSMA_DB_RUNTIME, or a missing key here would
# silently connect with the wrong role's secret instead of failing by name.
RUNTIME_SECRET_KEY = "TREND_RADAR_DB_RUNTIME"


def runtime_url(host: str | None = None, port: int | str | None = None, database: str = "app") -> str:
    """The production runtime connection: `db/secrets.py`'s `RUNTIME_SECRET_KEY` password, the schema's
    own runtime role, search_path pointed at `trend_radar` -- `storage/tables.py`'s Table objects are
    schema-unqualified so the same tables module works against a per-test schema too (tests/conftest.py's
    `trend_radar_schema` fixture sets its own search_path the same way).

    Only the host and the port move, and they move through `db.runtime`'s COSMAI_DB_HOST/COSMAI_DB_PORT
    -- the role, the database and the secret key stay this schema's own (contracts/entrypoints.md
    §DB 접속 노브)."""
    password = secrets.require([RUNTIME_SECRET_KEY])[RUNTIME_SECRET_KEY]
    host, port = host_and_port(host, port)
    url = make_url(f"postgresql+psycopg://{SERVICE_SCHEMA}_runtime:{password}@{host}:{port}/{database}")
    return url.update_query_dict({"options": f"-csearch_path={SERVICE_SCHEMA},pg_catalog"}).render_as_string(
        hide_password=False
    )


def create_engine(url: str) -> Engine:
    return sa.create_engine(url, pool_pre_ping=True, connect_args={"application_name": "cosmai-commerce"})


def write_records(connection: sa.Connection, records: Sequence) -> None:
    """Commits happen per batch by the caller, not per write: an hourly collection is minutes long,
    and the natural-key upserts already make re-running a partial hour harmless.

    `connection` belongs to the caller for the duration of this call and must not be one that another
    thread is also using -- `engine.Sink` is called concurrently and a `sqlalchemy.Connection` is not
    thread-safe. Hand it a fresh one per call (`cli._EngineSink` takes one out of the pool), not a
    long-lived connection opened once and shared."""
    if not records:
        return
    Repository(connection).write(records)


class RunLog:
    """One row per collection attempt -- an event, not a record: two attempts at the same hour are
    two rows, because the second attempt genuinely happened."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def start(self, captured_at: datetime, sources: Sequence[str], datasets: Sequence[str]) -> uuid.UUID:
        run_id = uuid.uuid4()
        with self._engine.begin() as connection:
            connection.execute(
                sa.insert(run_table).values(
                    id=run_id,
                    captured_at=captured_at,
                    started_at=datetime.now(UTC),
                    collector_version=COLLECTOR_VERSION,
                    schema_revision=None,
                    # Left "running" with a null finished_at on purpose: a killed run must not look
                    # like one that completed.
                    status="running",
                    sources=",".join(sources),
                    datasets=",".join(datasets),
                )
            )
        return run_id

    def record_sources(self, run_id: uuid.UUID, reports: Mapping[str, SourceReport]) -> None:
        if not reports:
            return
        rows = [
            {
                "run_id": run_id,
                "source": key,
                "requests": r.requests,
                "records": r.records,
                "retries": r.retries,
                "deduped": r.deduped,
                "dropped_over_depth": r.dropped_over_depth,
                "budget_exhausted": r.budget_exhausted,
                "blocked_reason": r.blocked_reason,
                "error_count": len(r.errors),
                # A skip is not an error, so it does not move `error_count` -- but its reason has to
                # land somewhere a reader of `outcome = 'skipped'` can find it, and this is the
                # column that already holds free text about why a source produced nothing. Hence rows
                # with `error_count = 0 AND errors IS NOT NULL`: that pair is a skip, not a lost count.
                "errors": "\n".join([*r.errors, *([r.skipped_reason] if r.skipped_reason else [])]) or None,
                "outcome": outcome_of(r),
                "configured_interval_s": r.configured_interval_s,
                "configured_concurrency": r.configured_concurrency,
                "request_budget": r.request_budget,
                "final_interval_s": r.final_interval_s,
                "final_concurrency": r.final_concurrency,
                "scope": dict(r.scope) if r.scope else None,
            }
            for key, r in reports.items()
        ]
        statement = pg_insert(run_source_table)
        statement = statement.on_conflict_do_update(
            index_elements=["run_id", "source"],
            set_={c: statement.excluded[c] for c in rows[0] if c not in ("run_id", "source")},
        )
        with self._engine.begin() as connection:
            connection.execute(statement, rows)

    def finish(self, run_id: uuid.UUID, status: str, note: str | None = None) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                sa.update(run_table)
                .where(run_table.c.id == run_id)
                .values(finished_at=datetime.now(UTC), status=status, note=note)
            )


def outcome_of(report: SourceReport) -> str:
    """The one word the report prints for this source, computed once and stored so a dashboard and
    this table cannot drift into two different definitions of "went well"."""
    if report.skipped_reason is not None:
        # Before "blocked": a skipped source never went out, so it can have neither.
        return "skipped"
    if report.blocked_reason is not None:
        return "blocked"
    if report.errors:
        return "error"
    if report.stopped_short:
        return "truncated"
    return "ok"


class PostgresJournal:
    """One fetch_log row per attempt, written as the attempt ends -- no buffering, so a run in
    progress can be watched rather than only examined afterwards."""

    def __init__(self, engine: Engine, run_id: uuid.UUID) -> None:
        self._engine = engine
        self._run_id = run_id

    def record(self, attempt: FetchAttempt) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                sa.insert(fetch_log_table).values(
                    run_id=self._run_id,
                    at=datetime.now(UTC),
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


__all__ = [
    "create_engine",
    "runtime_url",
    "write_records",
    "RunLog",
    "PostgresJournal",
    "outcome_of",
    "COLLECTOR_VERSION",
    "make_url",
]
