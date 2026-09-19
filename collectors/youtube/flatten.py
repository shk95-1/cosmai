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

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import date as date_type
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy import exc as sa_exc
from sqlalchemy.dialects.postgresql import insert as pg_insert

from collectors.youtube.models import JobState
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.sources import hashed_author_id
from collectors.youtube.storage.tables import (
    artifacts,
    comments,
    jobs,
    listing_entries,
    transcripts,
    video_snapshots,
)

FLATTEN_PROGRESS_ID = "flatten"
DEFAULT_BATCH_SIZE = 500

#: The `jobs.dataset` of a flatten record, in contracts/entrypoints.md's dataset vocabulary
#: (watch|work|flatten|prune) -- the column `collector_health`'s youtube arm groups by.
FLATTEN_DATASET = "flatten"

#: The `jobs.kind` of a flatten failure record (#275): one row per artifact a pass could not
#: flatten, saying which artifact, why and when. `jobs` is the only table that already carries
#: `error_code`·`error_message`·`attempt_count` beside a target, and the only one the health view
#: reads. Not a collection kind on purpose -- `target` here is an artifact identifier rather than a
#: video, nothing enqueues or claims these rows (`_claim` reads `queued` alone and they never are),
#: and a reader counting the `video.metadata` family by kind never meets one.
FLATTEN_JOB_KIND = "flatten.artifact"

#: How many passes may attempt an artifact whose failure could clear by itself (#275). The bound is
#: what stops a transient failure becoming a second way to pin the pass; when the attempts run out
#: nothing is lost, the record simply stays `failed` and countable with its last reason on it.
FLATTEN_MAX_ATTEMPTS = 3

#: One attempt and no more -- what a failure the payload's own immutable bytes decide is allowed.
FINAL_ON_FIRST_FAILURE = 1


#: The nine keys `video_snapshots.source_metadata` carries, in the archive's order. All nine are
#: present on every row; a value nobody could tell us is jsonb null, never an absent key and never
#: the string "None" (measured on the archive's 13,979 rows: like_count null on 876 of them,
#: duration_seconds on 6, comment_count on 5, key set complete on all of them).
SOURCE_METADATA_KEYS = (
    "tags",
    "has_paid_product_placement",
    "category_id",
    "caption_available",
    "duration_seconds",
    "view_count",
    "like_count",
    "comment_count",
    "collected_at",
)


def _repr_str(value: Any) -> str | None:
    """The archive's spelling: a Python `repr`, not JSON. Booleans are "True"/"False" capitalised
    and numbers are "1234" rather than 1234; `None` stays jsonb null.

    Not a style choice, and it must not be tidied. `analysis/sensitivity/pipeline.py` reads
    `source_metadata ->> 'has_paid_product_placement'` and compares it against its `DECLARED`
    constant, which is the string "True" -- so JSON's `true` here makes that comparison false for
    every live row and the declared half of ad marking silently becomes zero, with no error and the
    rows still returned. The numbers are harmless under `->>` (jsonb 42 and "42" both read back as
    "42") and are written this way for parity, so that a projected corpus row and an archive row are
    the same document. contracts/ddl/tubedepth/004 carries the same warning at the column.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in ("true", "false"):
            return "True" if stripped.lower() == "true" else "False"
        return stripped
    return str(value)


#: The archive spells an instant `2026-08-19T05:30:57Z` -- UTC, second resolution, a `Z` rather than
#: `+00:00` (`tests/fixtures/yt_handoff/document.csv`). `datetime.isoformat()` writes `+00:00` and
#: keeps microseconds, so both parse and neither is the archive's. Spelled out here because DDL 004
#: claims this column is the archive's spelling verbatim, and a claim like that has to hold for all
#: nine keys or say which ones it does not cover.
_ARCHIVE_INSTANT = "%Y-%m-%dT%H:%M:%SZ"


def source_metadata(fetched_at: datetime, payload: Mapping[str, Any]) -> dict[str, Any]:
    """The nine keys, archive-spelled. `collected_at` is the artifact's own `fetched_at` rather than
    the flatten pass's clock -- flatten runs on its own cadence and can be days behind the fetch."""
    return {
        "tags": list(payload.get("tags") or []),
        "has_paid_product_placement": _repr_str(payload.get("has_paid_product_placement")),
        "category_id": _repr_str(payload.get("category_id")),
        "caption_available": _repr_str(payload.get("caption_available")),
        "duration_seconds": _repr_str(payload.get("duration_seconds")),
        "view_count": _repr_str(payload.get("view_count")),
        "like_count": _repr_str(payload.get("like_count")),
        "comment_count": _repr_str(payload.get("comment_count")),
        "collected_at": fetched_at.astimezone(UTC).strftime(_ARCHIVE_INSTANT),
    }


def video_snapshot_row(
    artifact_id: str, target: str, fetched_at: datetime, payload: Mapping[str, Any]
) -> dict:
    published_date = payload.get("published_date")
    return {
        "artifact_id": artifact_id,
        "video_id": payload.get("video_id") or target,
        "fetched_at": fetched_at,
        "title": payload["title"],
        # #264 (DDL 006). `.get` with no `or ""`, so a payload stored before this issue -- which has
        # no description key -- lands as NULL rather than claiming the uploader wrote none; the
        # normalizer already turns a route's absent description into "".
        "description": payload.get("description"),
        "channel": payload.get("channel"),
        "channel_id": payload.get("channel_id"),
        "duration_seconds": payload.get("duration_seconds"),
        "view_count": payload.get("view_count"),
        "like_count": payload.get("like_count"),
        "comment_count": payload.get("comment_count"),
        "published_at": payload.get("published_at"),
        "published_date": date_type.fromisoformat(published_date) if published_date else None,
        "source_metadata": source_metadata(fetched_at, payload),
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
            # #267: the payload is not trusted for either of these. `sources` already drops the name
            # and hashes the id, but a payload stored before that change is still on disk and is
            # flattened after it, so this is the last place a raw value can be stopped. The name is
            # written as NULL rather than left out of the row, so that the on-conflict update of a
            # pre-rule row clears it too -- a row this collector touches carries no display name.
            "author": None,
            "author_id": hashed_author_id(comment.get("author_id")),
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


class PayloadUnreadable(Exception):
    """An artifact's stored payload could not be read back. #274 made this a cache miss on the `work`
    side; on this side there is nothing to miss -- flatten has no way of producing the bytes, so the
    artifact is one this collector can never turn into rows and is named as such rather than left to
    look like a database that might answer differently next time."""


@dataclass
class FlattenReport:
    flattened: int = 0
    #: Recorded as unflattenable: this collector will not attempt the artifact again.
    skipped: int = 0
    #: Recorded with attempts left: the next pass retries the artifact.
    deferred: int = 0

    @property
    def errors(self) -> int:
        return self.skipped + self.deferred

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
    try:
        payload = payloads.get(kind, digest)
    except (OSError, ValueError) as error:
        # The same pair #274 catches, and for the same reasons: a file that is not there, one that
        # cannot be read, and JSON or UTF-8 that will not parse. Named here so that the caller can
        # tell "the payload is gone" from "the payload is there and flatten could not shape it".
        raise PayloadUnreadable(f"{kind} {target!r}: the stored payload could not be read") from error
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


def _allowed_attempts(error: BaseException) -> tuple[str, int]:
    """The record's `error_code` and how many attempts the artifact is allowed (#275).

    One line divides the two: did the database refuse, or did the artifact's own bytes? A payload
    file is content-addressed and never rewritten, so a payload that is gone stays gone and a
    payload flatten cannot shape into rows shapes the same way on every later pass -- attempting it
    again only spends the pass. A refusal from the database (`statement_timeout`, a deadlock, a
    connection lost) says nothing about the artifact, so it is worth `FLATTEN_MAX_ATTEMPTS` passes.

    A constraint violation is a DBAPI error decided by the payload after all, and so is retried a
    few times before it goes final. That is the safe way round: the cost is two more attempts, where
    reading a transient refusal as final costs the artifact.
    """
    if isinstance(error, PayloadUnreadable):
        return "payload_unreadable", FINAL_ON_FIRST_FAILURE
    if isinstance(error, sa_exc.DBAPIError):
        return "internal", FLATTEN_MAX_ATTEMPTS
    return "internal", FINAL_ON_FIRST_FAILURE


def _reason(error: BaseException) -> str:
    """One line for the record, with no SQL and no payload in it: `str()` of a SQLAlchemy DBAPIError
    appends the statement and its bound parameters, which for a comments insert is the comment text
    itself. The driver's own message is the first line of `orig`."""
    original = getattr(error, "orig", None)
    text = str(original if original is not None else error).strip()
    first_line = text.splitlines()[0] if text else ""
    return f"{type(error).__name__}: {first_line}"[:300]


def _record_failure(
    conn: Connection,
    *,
    record_id: str | None,
    artifact_id: str,
    kind: str,
    target: str,
    attempt: int,
    error: BaseException,
    now: datetime,
) -> bool:
    """Write down that this artifact could not be flattened, and say whether a later pass retries it."""
    code, allowed = _allowed_attempts(error)
    values: dict[str, Any] = {
        "state": JobState.FAILED.value,
        "attempt_count": attempt,
        # The latest attempt's classification decides: a retry that meets a gone payload rather than
        # the refusal that deferred it is final from then on.
        "max_attempts": allowed,
        "error_code": code,
        "error_message": f"{kind} {target!r}: {_reason(error)}",
        # Both stamped on every attempt, so the health view's hour bucket is the pass that last
        # tried rather than the one that first failed.
        "started_at": now,
        "finished_at": now,
    }
    if record_id is None:
        conn.execute(
            sa.insert(jobs).values(
                identifier=uuid.uuid4().hex,
                kind=FLATTEN_JOB_KIND,
                target=artifact_id,
                dataset=FLATTEN_DATASET,
                scheduled_at=now,
                created_at=now,
                webhook_attempts=0,
                refresh=False,
                **values,
            )
        )
    else:
        conn.execute(sa.update(jobs).where(jobs.c.identifier == record_id).values(**values))
    return attempt < allowed


def _close_record(conn: Connection, record_id: str, *, attempt: int, now: datetime) -> None:
    """The retry landed: the record keeps its attempt count and loses the reason it no longer has."""
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.identifier == record_id)
        .values(
            state=JobState.SUCCEEDED.value,
            attempt_count=attempt,
            error_code=None,
            error_message=None,
            started_at=now,
            finished_at=now,
        )
    )


def _artifact_columns() -> sa.Select[tuple[str, str, str, datetime, str]]:
    return sa.select(
        artifacts.c.identifier,
        artifacts.c.kind,
        artifacts.c.target,
        artifacts.c.fetched_at,
        artifacts.c.digest,
    )


def _retries(conn: Connection, *, limit: int) -> list[tuple[Any, str | None, int]]:
    """The artifacts an earlier pass deferred, with the record that is waiting for them.

    A record whose artifact is gone (pruned) simply does not come back from the join and is never
    attempted again -- the row it was about no longer exists, so there is nothing left to flatten.
    """
    if limit <= 0:
        return []
    waiting = conn.execute(
        sa.select(jobs.c.identifier, jobs.c.target, jobs.c.attempt_count)
        .where(
            jobs.c.kind == FLATTEN_JOB_KIND,
            jobs.c.state == JobState.FAILED.value,
            jobs.c.attempt_count < jobs.c.max_attempts,
        )
        .order_by(jobs.c.created_at, jobs.c.identifier)
        .limit(limit)
    ).all()
    if not waiting:
        return []
    attempts = {row.target: (row.identifier, row.attempt_count) for row in waiting}
    rows = conn.execute(_artifact_columns().where(artifacts.c.identifier.in_(list(attempts)))).all()
    return [(row, *attempts[row.identifier]) for row in rows]


def _next_batch(
    conn: Connection, cursor: tuple[datetime, str] | None, *, limit: int
) -> list[tuple[Any, str | None, int]]:
    if limit <= 0:
        return []
    where = sa.true()
    if cursor is not None:
        fetched_at, identifier = cursor
        where = sa.or_(
            artifacts.c.fetched_at > fetched_at,
            sa.and_(artifacts.c.fetched_at == fetched_at, artifacts.c.identifier > identifier),
        )
    rows = conn.execute(
        _artifact_columns().where(where).order_by(artifacts.c.fetched_at, artifacts.c.identifier).limit(limit)
    ).all()
    return [(row, None, 0) for row in rows]


def _attempt(conn: Connection, payloads: PayloadStore, row: Any) -> BaseException | None:
    """One artifact inside its own SAVEPOINT, which is what makes the `except` below mean anything.

    A DBAPI error leaves the pass's transaction aborted, and every statement after it -- the next
    artifact, the failure record, the cursor write -- is refused; before the savepoint, one refused
    artifact therefore cost all 500 of them, silently, with the per-artifact `except` counting each
    `InFailedSqlTransaction` as one more bad artifact. #274 met the same trap on the `work` side.
    """
    try:
        with conn.begin_nested():
            flatten_one(
                conn,
                payloads,
                artifact_id=row.identifier,
                kind=row.kind,
                target=row.target,
                fetched_at=row.fetched_at,
                digest=row.digest,
            )
    except Exception as error:  # noqa: BLE001 - one bad artifact costs one row, not the whole pass
        return error
    return None


def run(
    conn: Connection, payloads: PayloadStore, *, batch_size: int = DEFAULT_BATCH_SIZE, now: datetime
) -> FlattenReport:
    """Flatten every artifact newer than the stored cursor, oldest first, and the artifacts an
    earlier pass deferred before them.

    The cursor advances over an artifact that failed (#275). It used to stop on the last success,
    which had two shapes: a batch in which everything failed left it where it was, so the next tick
    read the same batch forever and nothing newer was ever reached; and a mixed batch carried it
    past the failures with the successes around them, so those artifacts were never flattened again
    and nothing said so. What replaces the cursor as the memory of a failure is one record per
    artifact -- which is also what a retry reads, so advancing loses nothing.
    """
    retries = _retries(conn, limit=batch_size)
    cursor = _stored_cursor(conn)
    # The retries count against the batch rather than being added to it: the pass's size is what the
    # role's transaction_timeout was measured against, and it must not double because a bad night
    # left records behind.
    batch = _next_batch(conn, cursor, limit=batch_size - len(retries))

    report = FlattenReport()
    last_cursor: tuple[datetime, str] | None = None
    for row, record_id, attempts in [*retries, *batch]:
        from_cursor = record_id is None
        error = _attempt(conn, payloads, row)
        if error is None:
            report.flattened += 1
            if record_id is not None:
                _close_record(conn, record_id, attempt=attempts + 1, now=now)
        elif _record_failure(
            conn,
            record_id=record_id,
            artifact_id=row.identifier,
            kind=row.kind,
            target=row.target,
            attempt=attempts + 1,
            error=error,
            now=now,
        ):
            report.deferred += 1
        else:
            report.skipped += 1
        if from_cursor:
            last_cursor = (row.fetched_at, row.identifier)
    if last_cursor is not None:
        _write_cursor(conn, last_cursor, now=now)
    return report


__all__ = [
    "FlattenReport",
    "PayloadUnreadable",
    "flatten_one",
    "run",
    "video_snapshot_row",
    "listing_entry_rows",
    "comment_rows",
    "transcript_row",
]
