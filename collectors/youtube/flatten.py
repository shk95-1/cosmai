"""Turning an artifact's stored payload into the queryable snapshot tables -- `cosmai collect youtube
--dataset flatten`.

origin: service/yt-scrapper/src/tubedepth/flatten.py -- ported for #8: the row-builder functions
(`*_row`/`*_rows`) are close translations of the archived ones, restricted to the four kinds this
package's sources actually produce (`channel.about`/`channel_snapshot_row` stayed behind -- no channel
source is ported in #8, so there is nothing that would ever call it). `_HANDLERS` already routed
`video.transcript` in the archived file; the bug `watchlist.py`'s docstring explains was that nothing
ever queued a `video.transcript` job, not that flatten couldn't handle one once it arrived -- so this
module needed no transcript-specific fix, only a source of transcript artifacts to read (`watchlist.py`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert as pg_insert

from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage.tables import (
    artifacts,
    comments,
    listing_entries,
    transcripts,
    video_snapshots,
)

FLATTEN_PROGRESS_ID = "flatten"
DEFAULT_BATCH_SIZE = 500


def video_snapshot_row(
    artifact_id: str, target: str, fetched_at: datetime, payload: Mapping[str, Any]
) -> dict:
    published_date = payload.get("published_date")
    return {
        "artifact_id": artifact_id,
        "video_id": payload.get("video_id") or target,
        "fetched_at": fetched_at,
        "title": payload["title"],
        "channel": payload.get("channel"),
        "channel_id": payload.get("channel_id"),
        "duration_seconds": payload.get("duration_seconds"),
        "view_count": payload.get("view_count"),
        "like_count": payload.get("like_count"),
        "comment_count": payload.get("comment_count"),
        "published_at": payload.get("published_at"),
        "published_date": date_type.fromisoformat(published_date) if published_date else None,
    }


def listing_entry_rows(
    artifact_id: str, kind: str, target: str, fetched_at: datetime, payload: Mapping[str, Any]
) -> list[dict]:
    rows = []
    for position, entry in enumerate(payload.get("videos") or []):
        video_id = entry.get("video_id")
        if not video_id:
            continue
        rows.append(
            {
                "artifact_id": artifact_id,
                "position": position,
                "kind": kind,
                "target": target,
                "fetched_at": fetched_at,
                "video_id": video_id,
                "title": entry.get("title"),
                "view_count": entry.get("view_count"),
                "duration_seconds": entry.get("duration_seconds"),
                "channel": entry.get("channel"),
                "channel_id": entry.get("channel_id"),
                "published_at": entry.get("published_at"),
            }
        )
    return rows


def comment_rows(target: str, fetched_at: datetime, payload: Mapping[str, Any]) -> list[dict]:
    rows: dict[str, dict] = {}
    for comment in payload.get("comments") or []:
        comment_id = comment.get("comment_id")
        if not comment_id:
            continue
        rows[comment_id] = {
            "video_id": target,
            "comment_id": comment_id,
            "parent_id": comment.get("parent_id"),
            "text": comment.get("text") or "",
            "author": comment.get("author"),
            "author_id": comment.get("author_id"),
            "like_count": comment.get("like_count"),
            "is_hearted_by_uploader": bool(comment.get("is_hearted_by_uploader", False)),
            "is_pinned": bool(comment.get("is_pinned", False)),
            "published_at": comment.get("published_at"),
            "first_seen_at": fetched_at,
            "last_seen_at": fetched_at,
            # #8 DDL additions: no writer for either yet (channel_is_brand_owner needs a
            # brand<->channel lexicon mapping that does not exist -- scope.json/report). Left NULL.
            "published_at_resolution": None,
            "channel_is_brand_owner": None,
        }
    return list(rows.values())


def transcript_row(target: str, fetched_at: datetime, payload: Mapping[str, Any]) -> dict:
    segments = payload.get("segments")
    return {
        "video_id": target,
        "language": payload["language"],
        "is_automatic": bool(payload.get("is_automatic", False)),
        "full_text": payload.get("full_text") or "",
        "segment_count": len(segments) if isinstance(segments, list) else 0,
        "fetched_at": fetched_at,
    }


def _upsert_video_snapshot(conn: Connection, row: dict) -> None:
    conn.execute(
        pg_insert(video_snapshots).values(**row).on_conflict_do_nothing(index_elements=["artifact_id"])
    )


def _upsert_listing_entries(conn: Connection, rows: Sequence[dict]) -> None:
    if not rows:
        return
    stmt = pg_insert(listing_entries)
    conn.execute(stmt.on_conflict_do_nothing(index_elements=["artifact_id", "position"]), rows)


def _upsert_comments(conn: Connection, rows: Sequence[dict]) -> None:
    if not rows:
        return
    stmt = pg_insert(comments)
    update_cols = [c for c in rows[0] if c not in ("video_id", "comment_id", "first_seen_at")]
    stmt = stmt.on_conflict_do_update(
        index_elements=["video_id", "comment_id"], set_={c: stmt.excluded[c] for c in update_cols}
    )
    conn.execute(stmt, rows)


def _upsert_transcript(conn: Connection, row: dict) -> None:
    stmt = pg_insert(transcripts).values(**row)
    update_cols = [c for c in row if c not in ("video_id", "language")]
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["video_id", "language"], set_={c: stmt.excluded[c] for c in update_cols}
        )
    )


@dataclass
class FlattenReport:
    flattened: int = 0
    errors: int = 0

    @property
    def ok(self) -> bool:
        return self.errors == 0


def flatten_one(
    conn: Connection,
    payloads: PayloadStore,
    *,
    artifact_id: str,
    kind: str,
    target: str,
    fetched_at: datetime,
    digest: str,
) -> None:
    """Route one artifact's stored payload to its table(s). The one place `kind` is switched on."""
    payload = payloads.get(kind, digest)
    if kind in ("channel.videos", "search.videos", "playlist.items", "trending.videos"):
        _upsert_listing_entries(conn, listing_entry_rows(artifact_id, kind, target, fetched_at, payload))
    elif kind == "video.metadata":
        _upsert_video_snapshot(conn, video_snapshot_row(artifact_id, target, fetched_at, payload))
    elif kind == "video.comments":
        _upsert_comments(conn, comment_rows(target, fetched_at, payload))
    elif kind == "video.transcript":
        _upsert_transcript(conn, transcript_row(target, fetched_at, payload))
    else:
        raise ValueError(f"flatten has no handler for artifact kind {kind!r}")


def _stored_cursor(conn: Connection) -> tuple[datetime, str] | None:
    row = conn.execute(
        sa.text("SELECT cursor_fetched_at, cursor_identifier FROM flatten_progress WHERE identifier = :id"),
        {"id": FLATTEN_PROGRESS_ID},
    ).first()
    return (row[0], row[1]) if row is not None else None


def _write_cursor(conn: Connection, cursor: tuple[datetime, str], *, now: datetime) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO flatten_progress (identifier, cursor_fetched_at, cursor_identifier, updated_at) "
            "VALUES (:id, :fa, :ci, :ua) "
            "ON CONFLICT (identifier) DO UPDATE SET "
            "cursor_fetched_at = :fa, cursor_identifier = :ci, updated_at = :ua"
        ),
        {"id": FLATTEN_PROGRESS_ID, "fa": cursor[0], "ci": cursor[1], "ua": now},
    )


def run(
    conn: Connection, payloads: PayloadStore, *, batch_size: int = DEFAULT_BATCH_SIZE, now: datetime
) -> FlattenReport:
    """Flatten every artifact newer than the stored cursor, oldest first, advancing the cursor as it goes."""
    cursor = _stored_cursor(conn)
    where = sa.true()
    if cursor is not None:
        fetched_at, identifier = cursor
        where = sa.or_(
            artifacts.c.fetched_at > fetched_at,
            sa.and_(artifacts.c.fetched_at == fetched_at, artifacts.c.identifier > identifier),
        )
    rows = conn.execute(
        sa.select(
            artifacts.c.identifier,
            artifacts.c.kind,
            artifacts.c.target,
            artifacts.c.fetched_at,
            artifacts.c.digest,
        )
        .where(where)
        .order_by(artifacts.c.fetched_at, artifacts.c.identifier)
        .limit(batch_size)
    ).all()

    report = FlattenReport()
    last_cursor: tuple[datetime, str] | None = None
    for identifier, kind, target, fetched_at, digest in rows:
        try:
            flatten_one(
                conn,
                payloads,
                artifact_id=identifier,
                kind=kind,
                target=target,
                fetched_at=fetched_at,
                digest=digest,
            )
            report.flattened += 1
            last_cursor = (fetched_at, identifier)
        except Exception:  # noqa: BLE001 - one bad artifact costs one row, not the whole pass
            report.errors += 1
    if last_cursor is not None:
        _write_cursor(conn, last_cursor, now=now)
    return report


__all__ = [
    "FlattenReport",
    "flatten_one",
    "run",
    "video_snapshot_row",
    "listing_entry_rows",
    "comment_rows",
    "transcript_row",
]
