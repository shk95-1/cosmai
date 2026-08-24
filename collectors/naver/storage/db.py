"""The Postgres sink, and the record of the run itself -- same shape as
`collectors/commerce/storage/db.py`'s `RunLog`/`PostgresJournal`, sized down for one dataset per run
(no per-source fan-out: naver has exactly one source)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert

from collectors.naver.models import BlogPost, DatalabPoint
from collectors.naver.storage.tables import naver_blog_post, naver_datalab_point, naver_fetch_log, naver_run
from db.runtime import runtime_url as _needs_runtime_url

COLLECTOR_VERSION = "naver-0.1"


def runtime_url() -> str:
    """Production connection: needs_runtime's default search_path is already `needs`
    (db/bootstrap.sql), the same role every other needs-schema writer uses -- naver has no schema
    or role of its own (contracts/ddl/needs/004_naver.sql's header)."""
    return _needs_runtime_url()


def create_engine(url: str) -> Engine:
    return sa.create_engine(url, pool_pre_ping=True, connect_args={"application_name": "cosmai-naver"})


class RunLog:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def start(self, dataset: str, captured_at: datetime) -> uuid.UUID:
        run_id = uuid.uuid4()
        with self._engine.begin() as connection:
            connection.execute(
                sa.insert(naver_run).values(
                    id=run_id,
                    dataset=dataset,
                    captured_at=captured_at,
                    started_at=datetime.now(UTC),
                    status="running",
                    collector_version=COLLECTOR_VERSION,
                )
            )
        return run_id

    def finish(self, run_id: uuid.UUID, status: str, note: str | None = None) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                sa.update(naver_run)
                .where(naver_run.c.id == run_id)
                .values(finished_at=datetime.now(UTC), status=status, note=note)
            )


class FetchJournal:
    """One fetch_log row per attempt -- written as the attempt ends, matching
    `collectors.commerce.storage.db.PostgresJournal`."""

    def __init__(self, engine: Engine, run_id: uuid.UUID, dataset: str) -> None:
        self._engine = engine
        self._run_id = run_id
        self._dataset = dataset

    def record(
        self,
        *,
        query: str,
        status: int | None,
        attempt: int,
        elapsed_ms: int | None = None,
        bytes_: int | None = None,
        error: str | None = None,
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                sa.insert(naver_fetch_log).values(
                    run_id=self._run_id,
                    at=datetime.now(UTC),
                    dataset=self._dataset,
                    query=query,
                    status=status,
                    attempt=attempt,
                    elapsed_ms=elapsed_ms,
                    bytes=bytes_,
                    error=error,
                )
            )


def write_datalab_points(connection: sa.Connection, points: Sequence[DatalabPoint]) -> None:
    """Upsert on (category, group_key, month) -- a re-run of the same window overwrites the same
    cells rather than duplicating them."""
    if not points:
        return
    rows = [
        {
            "category": p.category,
            "group_key": p.group_key,
            "month": p.month,
            "ratio": p.ratio,
            "terms": list(p.terms),
            "captured_at": p.captured_at,
        }
        for p in points
    ]
    key = ["category", "group_key", "month"]
    statement = pg_insert(naver_datalab_point)
    statement = statement.on_conflict_do_update(
        index_elements=key,
        set_={c: statement.excluded[c] for c in rows[0] if c not in key},
    )
    connection.execute(statement, rows)


def write_blog_posts(connection: sa.Connection, posts: Sequence[BlogPost]) -> None:
    """Upsert on post_id -- the same post can turn up under more than one query term in one run
    (or a later run), and the newest fetch's title/excerpt/author win."""
    if not posts:
        return
    rows = [
        {
            "post_id": b.post_id,
            "url": b.url,
            "category": b.category,
            "group_key": b.group_key,
            "query": b.query,
            "title": b.title,
            "excerpt": b.excerpt,
            "author": b.author,
            "published_at": b.published_at,
            "observed_at_resolution": b.observed_at_resolution,
            "captured_at": b.captured_at,
        }
        for b in posts
    ]
    key = ["post_id"]
    statement = pg_insert(naver_blog_post)
    statement = statement.on_conflict_do_update(
        index_elements=key,
        set_={c: statement.excluded[c] for c in rows[0] if c not in key},
    )
    connection.execute(statement, rows)


__all__ = [
    "runtime_url",
    "create_engine",
    "RunLog",
    "FetchJournal",
    "write_datalab_points",
    "write_blog_posts",
    "COLLECTOR_VERSION",
]
