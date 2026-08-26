"""The tables this collector writes, as SQLAlchemy Core -- describes the 4 tables
`contracts/ddl/needs/004_naver.sql` adds. That file is the one authority for their actual shape
(#7's completion bar: DDL diff = 0), so `tests/collectors/naver/test_tables_match_ddl.py` reflects
the applied DDL and diffs it against `metadata` here, the same shape as
`collectors/commerce/storage/tables.py`.

Unqualified on purpose: `needs` is reached through the connection's search_path (bootstrap.sql sets
it for `needs_runtime`; `tests/conftest.py`'s `needs_schema`/`needs_runtime_url` fixtures point it at
a per-test schema instead), not hardcoded into every statement.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Table
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()

naver_run = Table(
    "naver_run",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("dataset", sa.Text, nullable=False),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("note", sa.Text),
    sa.Column("collector_version", sa.Text),
)

naver_fetch_log = Table(
    "naver_fetch_log",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("run_id", sa.Uuid, sa.ForeignKey("naver_run.id", ondelete="CASCADE"), nullable=False),
    sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("dataset", sa.Text, nullable=False),
    sa.Column("query", sa.Text, nullable=False),
    sa.Column("status", sa.Integer),
    sa.Column("attempt", sa.Integer, nullable=False),
    sa.Column("elapsed_ms", sa.Integer),
    sa.Column("bytes", sa.Integer),
    sa.Column("error", sa.Text),
    sa.Index("ix_naver_fetch_log_run_at", "run_id", "at"),
)

naver_datalab_point = Table(
    "naver_datalab_point",
    metadata,
    sa.Column("category", sa.Text, primary_key=True),
    sa.Column("group_key", sa.Text, primary_key=True),
    sa.Column("month", sa.Text, primary_key=True),
    sa.Column("ratio", sa.Numeric),
    sa.Column("terms", postgresql.JSONB, nullable=False),
    sa.Column("request_key", sa.Text, nullable=False),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
)

naver_blog_post = Table(
    "naver_blog_post",
    metadata,
    sa.Column("post_id", sa.Text, primary_key=True),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("category", sa.Text),
    sa.Column("group_key", sa.Text),
    sa.Column("query", sa.Text),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("excerpt", sa.Text, nullable=False),
    sa.Column("author", sa.Text),
    sa.Column("published_at", sa.Date),
    sa.Column("observed_at_resolution", sa.Text, nullable=False),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
)

__all__ = [
    "metadata",
    "naver_run",
    "naver_fetch_log",
    "naver_datalab_point",
    "naver_blog_post",
]
