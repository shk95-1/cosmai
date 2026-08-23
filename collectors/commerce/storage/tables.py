"""The schema, as SQLAlchemy Core tables -- describes writes only.

origin: service/trend-radar/src/trend_radar/storage/tables.py -- ported for #7. This module never
creates the schema: contracts/ddl/current/app.trend_radar.sql is the one authority for the tables'
actual shape (completion bar: DDL diff = 0), applied verbatim by `tests/conftest.py`'s
`trend_radar_schema` fixture -- the fixture never generates DDL from these Table objects, so the
tables written here matching that file is a constraint on this module, not something re-derived from
it. Natural keys are primary keys on purpose, which is what makes a re-run of the same hour a no-op.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Table
from sqlalchemy.dialects import postgresql

from collectors.commerce.models import (
    NewProductRecord,
    PriceRecord,
    ProductRecord,
    RankRecord,
    Record,
    ReviewAnswerRecord,
    ReviewRecord,
    ReviewStatsRecord,
    ReviewSummaryRecord,
    ReviewTopicRecord,
)

# Unqualified on purpose: `trend_radar` is reached through the connection's search_path (set by
# `storage/db.py`'s runtime_url in production, by `tests/conftest.py`'s per-test schema fixture in
# tests), not hardcoded into every statement -- a hardcoded schema name would make every write target
# the literal "trend_radar" schema even when the test fixture isolated the run under a differently
# named one.
metadata = sa.MetaData()

run = Table(
    "run",
    metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("sources", sa.Text, nullable=False),
    sa.Column("datasets", sa.Text, nullable=False),
    sa.Column("note", sa.Text),
    sa.Column("collector_version", sa.Text),
    sa.Column("schema_revision", sa.Text),
    sa.Index("ix_run_captured_at", "captured_at"),
)

fetch_log = Table(
    "fetch_log",
    metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("run_id", sa.Uuid, sa.ForeignKey("run.id", ondelete="CASCADE"), nullable=False),
    sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("dataset", sa.Text, nullable=False),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("status", sa.Integer),
    sa.Column("attempt", sa.Integer, nullable=False),
    sa.Column("elapsed_ms", sa.Integer),
    sa.Column("bytes", sa.Integer),
    sa.Column("error", sa.Text),
    sa.Index("ix_fetch_log_run_at", "run_id", "at"),
)

run_source = Table(
    "run_source",
    metadata,
    sa.Column("run_id", sa.Uuid, sa.ForeignKey("run.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("source", sa.Text, primary_key=True),
    sa.Column("requests", sa.Integer, nullable=False),
    sa.Column("records", sa.Integer, nullable=False),
    sa.Column("retries", sa.Integer, nullable=False),
    sa.Column("deduped", sa.Integer, nullable=False),
    sa.Column("dropped_over_depth", sa.Integer, nullable=False),
    sa.Column("budget_exhausted", sa.Boolean, nullable=False),
    sa.Column("blocked_reason", sa.Text),
    sa.Column("error_count", sa.Integer, nullable=False),
    sa.Column("errors", sa.Text),
    sa.Column("outcome", sa.Text, nullable=False),
    sa.Column("configured_interval_s", sa.Float),
    sa.Column("configured_concurrency", sa.Integer),
    sa.Column("request_budget", sa.Integer),
    sa.Column("final_interval_s", sa.Float),
    sa.Column("final_concurrency", sa.Integer),
    sa.Column("scope", postgresql.JSONB),
)

rank_snapshot = Table(
    "rank_snapshot",
    metadata,
    sa.Column("source", sa.Text, primary_key=True),
    sa.Column("board", sa.Text, primary_key=True),
    sa.Column("category_key", sa.Text, primary_key=True),
    sa.Column("product_key", sa.Text, primary_key=True),
    sa.Column("captured_at", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("category_name", sa.Text),
    sa.Column("rank", sa.Integer, nullable=False),
    sa.Column("product_name", sa.Text, nullable=False),
    sa.Column("brand", sa.Text),
    sa.Column("price", sa.Integer),
    sa.Column("discount_rate", sa.Integer),
    sa.Column("review_count", sa.Integer),
    sa.Column("review_rating", sa.Float),
    sa.Column("rank_delta", sa.Integer),
    sa.Column("is_new", sa.Boolean),
    sa.Index("ix_rank_snapshot_product_over_time", "source", "product_key", "captured_at"),
)

product = Table(
    "product",
    metadata,
    sa.Column("source", sa.Text, primary_key=True),
    sa.Column("product_key", sa.Text, primary_key=True),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("brand", sa.Text),
    sa.Column("volume", sa.Text),
    sa.Column("url", sa.Text),
    sa.Column("ingredients", sa.Text),
    sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
)

price_point = Table(
    "price_point",
    metadata,
    sa.Column("source", sa.Text, primary_key=True),
    sa.Column("product_key", sa.Text, primary_key=True),
    sa.Column("captured_at", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("price", sa.Integer, nullable=False),
    sa.Column("discount_rate", sa.Integer),
)

review = Table(
    "review",
    metadata,
    sa.Column("source", sa.Text, primary_key=True),
    sa.Column("review_key", sa.Text, primary_key=True),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("product_key", sa.Text, nullable=False),
    sa.Column("rating", sa.Float),
    sa.Column("body", sa.Text),
    sa.Column("author_hash", sa.Text),
    sa.Column("written_at", sa.DateTime(timezone=True)),
    sa.Index("ix_review_product", "source", "product_key"),
)

review_stats = Table(
    "review_stats",
    metadata,
    sa.Column("source", sa.Text, primary_key=True),
    sa.Column("product_key", sa.Text, primary_key=True),
    sa.Column("captured_at", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("review_count", sa.Integer),
    sa.Column("rating_average", sa.Float),
    sa.Column("pct_5", sa.Integer),
    sa.Column("pct_4", sa.Integer),
    sa.Column("pct_3", sa.Integer),
    sa.Column("pct_2", sa.Integer),
    sa.Column("pct_1", sa.Integer),
    sa.Column("positive_pct", sa.Float),
    sa.Column("negative_pct", sa.Float),
)

review_summary = Table(
    "review_summary",
    metadata,
    sa.Column("source", sa.Text, primary_key=True),
    sa.Column("product_key", sa.Text, primary_key=True),
    sa.Column("rank", sa.Integer, primary_key=True),
    sa.Column("captured_at", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("body", sa.Text),
)

review_topic = Table(
    "review_topic",
    metadata,
    sa.Column("source", sa.Text, primary_key=True),
    sa.Column("product_key", sa.Text, primary_key=True),
    sa.Column("topic_key", sa.Text, primary_key=True),
    sa.Column("captured_at", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("topic_name", sa.Text, nullable=False),
    sa.Column("topic_group", sa.Text),
    sa.Column("sentence", sa.Text),
    sa.Column("is_positive", sa.Boolean),
    sa.Column("score", sa.Float),
    sa.Column("share_pct", sa.Integer),
    sa.Column("review_count", sa.Integer),
    sa.Column("rank", sa.Integer),
    sa.Index("ix_review_topic_by_topic", "source", "topic_key", "captured_at"),
)

review_answer = Table(
    "review_answer",
    metadata,
    sa.Column("source", sa.Text, primary_key=True),
    sa.Column("review_key", sa.Text, primary_key=True),
    sa.Column("question_key", sa.Text, primary_key=True),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("product_key", sa.Text, nullable=False),
    sa.Column("question_name", sa.Text),
    sa.Column("answer", sa.Text),
    sa.Index("ix_review_answer_product", "source", "product_key", "question_key"),
)

new_product = Table(
    "new_product",
    metadata,
    sa.Column("source", sa.Text, primary_key=True),
    sa.Column("product_key", sa.Text, primary_key=True),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("brand", sa.Text),
    sa.Column("listed_at", sa.DateTime(timezone=True)),
)

TABLE_FOR: dict[type[Record], Table] = {
    RankRecord: rank_snapshot,
    ProductRecord: product,
    PriceRecord: price_point,
    ReviewRecord: review,
    ReviewTopicRecord: review_topic,
    ReviewStatsRecord: review_stats,
    ReviewSummaryRecord: review_summary,
    ReviewAnswerRecord: review_answer,
    NewProductRecord: new_product,
}
