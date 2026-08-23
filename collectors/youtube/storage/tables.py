"""The tables this collector reads and writes, as SQLAlchemy Core -- describes 8 of the 13 tables
`contracts/ddl/current/app.tubedepth.sql` holds.

origin: service/yt-scrapper/src/tubedepth/models.py -- ported for #8, declarative ORM translated to
Core (matching collectors/commerce's storage/tables.py style) since nothing here needs an identity map.
`alembic_version`, `api_keys`, `lane_health`, `source_health`, `worker_control` are the always-on API
server and rate-lane daemon's tables (#8 is a batch CLI collector, not that daemon) and are deliberately
not declared -- the schema-match test only checks the tables declared here, not every DDL table, so this
is a scope decision, not something the test enforces on its own.

Unqualified on purpose, same reasoning as commerce/storage/tables.py: `tubedepth` is reached through the
connection's search_path, not hardcoded, so `tests/conftest.py`'s `tubedepth_schema` fixture can point
the same tables at a differently-named per-test schema.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Table

metadata = sa.MetaData()

jobs = Table(
    "jobs",
    metadata,
    sa.Column("identifier", sa.String(32), primary_key=True),
    sa.Column("kind", sa.String(64), nullable=False),
    sa.Column("target", sa.String(500), nullable=False, default=""),
    sa.Column("follow_up_kind", sa.String(64)),
    sa.Column("api_key_id", sa.String(32)),
    sa.Column("state", sa.String(9), nullable=False),
    sa.Column("attempt_count", sa.Integer, nullable=False, default=0),
    sa.Column("max_attempts", sa.Integer, nullable=False, default=3),
    sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("claimed_by", sa.String(64)),
    sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    sa.Column("finished_at", sa.DateTime(timezone=True)),
    sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
    sa.Column("webhook_url", sa.String(500)),
    sa.Column("webhook_attempts", sa.Integer, nullable=False, default=0),
    sa.Column("webhook_delivered_at", sa.DateTime(timezone=True)),
    sa.Column("payload_digest", sa.String(64)),
    sa.Column("payload_bytes", sa.Integer),
    sa.Column("error_code", sa.String(64)),
    sa.Column("error_message", sa.Text),
    sa.Column("refresh", sa.Boolean, nullable=False, default=False),
    sa.Index("ix_job_claimable", "state", "scheduled_at", "created_at"),
    sa.Index("ix_job_lease", "state", "lease_expires_at"),
    sa.Index("ix_job_recent", "kind", "created_at"),
)

artifacts = Table(
    "artifacts",
    metadata,
    sa.Column("identifier", sa.String(32), primary_key=True),
    sa.Column("kind", sa.String(64), nullable=False),
    sa.Column("target", sa.String(500), nullable=False),
    sa.Column("fingerprint", sa.String(64), nullable=False),
    sa.Column("digest", sa.String(64), nullable=False),
    sa.Column("byte_count", sa.Integer, nullable=False),
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("fresh_until", sa.DateTime(timezone=True), nullable=False),
    sa.Column("schema_version", sa.String(16)),
    sa.Index("ix_artifact_lookup", "fingerprint", "fresh_until"),
    sa.Index("ix_artifact_recent", "kind", "fetched_at"),
    sa.Index("ix_artifact_target", "target", "fetched_at"),
)

video_snapshots = Table(
    "video_snapshots",
    metadata,
    sa.Column("artifact_id", sa.String(32), primary_key=True),
    sa.Column("video_id", sa.String(500), nullable=False),
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("channel", sa.Text),
    sa.Column("channel_id", sa.String(500)),
    sa.Column("duration_seconds", sa.Integer),
    sa.Column("view_count", sa.BigInteger),
    sa.Column("like_count", sa.BigInteger),
    sa.Column("comment_count", sa.BigInteger),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.Column("published_date", sa.Date),
    sa.Index("ix_video_snapshot_series", "video_id", "fetched_at"),
)

listing_entries = Table(
    "listing_entries",
    metadata,
    sa.Column("artifact_id", sa.String(32), primary_key=True),
    sa.Column("position", sa.Integer, primary_key=True),
    sa.Column("kind", sa.String(64), nullable=False),
    sa.Column("target", sa.String(500), nullable=False),
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("video_id", sa.String(500), nullable=False),
    sa.Column("title", sa.Text),
    sa.Column("view_count", sa.BigInteger),
    sa.Column("duration_seconds", sa.Integer),
    sa.Column("channel", sa.Text),
    sa.Column("channel_id", sa.String(500)),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.Index("ix_listing_entry_series", "target", "fetched_at"),
    sa.Index("ix_listing_entry_video", "video_id"),
)

channel_snapshots = Table(
    "channel_snapshots",
    metadata,
    sa.Column("artifact_id", sa.String(32), primary_key=True),
    sa.Column("channel_id", sa.String(500), nullable=False),
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("name", sa.Text),
    sa.Column("handle", sa.String(500)),
    sa.Column("subscriber_count_approximate", sa.BigInteger),
    sa.Column("view_count", sa.BigInteger),
    sa.Column("video_count", sa.Integer),
    sa.Column("country", sa.String(100)),
    sa.Index("ix_channel_snapshot_series", "channel_id", "fetched_at"),
)

comments = Table(
    "comments",
    metadata,
    sa.Column("video_id", sa.String(500), primary_key=True),
    sa.Column("comment_id", sa.String(200), primary_key=True),
    sa.Column("parent_id", sa.String(200)),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("author", sa.Text),
    sa.Column("author_id", sa.String(500)),
    sa.Column("like_count", sa.BigInteger),
    sa.Column("is_hearted_by_uploader", sa.Boolean, nullable=False, default=False),
    sa.Column("is_pinned", sa.Boolean, nullable=False, default=False),
    sa.Column("published_at", sa.DateTime(timezone=True)),
    sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    # #8 additions (contracts/ddl/tubedepth/001_comments_columns.sql) -- both nullable, additive only.
    sa.Column("published_at_resolution", sa.Text),
    sa.Column("channel_is_brand_owner", sa.Boolean),
    sa.Index("ix_comment_published", "video_id", "published_at"),
)

transcripts = Table(
    "transcripts",
    metadata,
    sa.Column("video_id", sa.String(500), primary_key=True),
    sa.Column("language", sa.String(64), primary_key=True),
    sa.Column("is_automatic", sa.Boolean, nullable=False, default=False),
    sa.Column("full_text", sa.Text, nullable=False),
    sa.Column("segment_count", sa.Integer, nullable=False),
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
)

flatten_progress = Table(
    "flatten_progress",
    metadata,
    sa.Column("identifier", sa.String(32), primary_key=True),
    sa.Column("cursor_fetched_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("cursor_identifier", sa.String(32), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

__all__ = [
    "metadata",
    "jobs",
    "artifacts",
    "video_snapshots",
    "listing_entries",
    "channel_snapshots",
    "comments",
    "transcripts",
    "flatten_progress",
]
