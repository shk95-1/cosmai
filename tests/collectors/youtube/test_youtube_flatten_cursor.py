"""#275: the flatten cursor, and the artifacts one pass cannot flatten.

`flatten` keeps a single cursor row (`tubedepth.flatten_progress`) and used to move it only onto an
artifact that had flattened. Two shapes followed:

(a) a batch in which *every* artifact fails leaves the cursor where it was, so the next tick reads
    the same batch and nothing newer is ever reached. Production holds 3,707 old-fleet
    `video.transcript` artifact rows whose payload file is not in the new stack's volume -- seven
    batches of exactly that shape;
(b) a mixed batch moves the cursor past the failed artifacts along with the successes around them,
    so those artifacts are never flattened again and nothing records that they were skipped.

The axes no existing test had: an artifact row whose payload file is not in the store (#274's shape,
met on the flatten side), and a database that refuses one artifact's write in the middle of a pass --
which aborts the pass's transaction for every statement after it, the per-artifact `except` included.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from collectors.youtube.cli import run
from collectors.youtube.flatten import DEFAULT_BATCH_SIZE
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage.tables import artifacts, jobs, transcripts, video_snapshots

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 9, 19, 20, tzinfo=UTC)
#: Nothing is ever written under this digest -- that absence is what the whole file is about.
GONE_DIGEST = "0" * 64
TRANSCRIPT_PAYLOAD: dict[str, Any] = {"language": "en", "full_text": "a transcript", "segments": []}


def _artifact_row(
    *, identifier: str, kind: str, target: str, digest: str, fetched_at: datetime, byte_count: int = 0
) -> dict[str, Any]:
    return {
        "identifier": identifier,
        "kind": kind,
        "target": target,
        "fingerprint": f"{kind}:{target}",
        "digest": digest,
        "byte_count": byte_count,
        "fetched_at": fetched_at,
        "fresh_until": fetched_at + timedelta(days=30),
    }


def _insert_artifacts(schema: str, rows: list[dict[str, Any]]) -> None:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            conn.execute(sa.insert(artifacts), rows)
    finally:
        engine.dispose()


def _stored(payload_root: Path, kind: str, payload: dict[str, Any]) -> tuple[str, int]:
    stored = PayloadStore(payload_root).put(kind, payload)
    return stored.digest, stored.byte_count


def _flatten(schema: str, payload_root: Path, *, at: datetime) -> int:
    """One `cosmai collect youtube --dataset flatten` pass. A pass that dies is a named failure here
    rather than a traceback out of the test: what the pass reports is the subject."""
    try:
        return run("flatten", database_url=schema, payload_root=payload_root, captured_at=at)
    except Exception as error:  # noqa: BLE001 - the RED shape of a pass that raises instead of reporting
        pytest.fail(f"the flatten pass raised {type(error).__name__} instead of reporting: {error}")


def _cursor(schema: str) -> tuple[datetime, str] | None:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            row = conn.execute(
                sa.text("SELECT cursor_fetched_at, cursor_identifier FROM flatten_progress")
            ).first()
    finally:
        engine.dispose()
    return (row[0], row[1]) if row is not None else None


def _records(schema: str) -> Sequence[sa.Row[Any]]:
    """What a later pass or a human finds, asked the way `collector_health`'s youtube arm counts:
    `tubedepth.jobs` by `dataset` (db/views/collector_health.sql)."""
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            return conn.execute(
                sa.select(
                    jobs.c.kind,
                    jobs.c.target,
                    jobs.c.state,
                    jobs.c.attempt_count,
                    jobs.c.max_attempts,
                    jobs.c.error_code,
                    jobs.c.error_message,
                    jobs.c.finished_at,
                )
                .where(jobs.c.dataset == "flatten")
                .order_by(jobs.c.target)
            ).all()
    finally:
        engine.dispose()


def _flattened_video_ids(schema: str) -> list[str]:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            snapshots = conn.execute(sa.select(video_snapshots.c.video_id)).scalars().all()
            texts = conn.execute(sa.select(transcripts.c.video_id)).scalars().all()
    finally:
        engine.dispose()
    return sorted([*snapshots, *texts])


def _refuse_video_snapshot_writes(schema: str) -> None:
    """A database that refuses one artifact's write, the way a `statement_timeout` or a deadlock
    refuses one. The trigger sits on `video_snapshots` alone, so the transcript artifact beside it in
    the same batch is the untouched neighbour."""
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE FUNCTION refuse_one_row() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'the database refused this row'; END $$"
            )
            conn.exec_driver_sql(
                "CREATE TRIGGER refuse_one_row BEFORE INSERT ON video_snapshots "
                "FOR EACH ROW EXECUTE FUNCTION refuse_one_row()"
            )
    finally:
        engine.dispose()


def _allow_video_snapshot_writes(schema: str) -> None:
    engine = sa.create_engine(schema)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP TRIGGER refuse_one_row ON video_snapshots")
    finally:
        engine.dispose()


def test_a_full_batch_of_unflattenable_artifacts_does_not_pin_the_cursor(
    tubedepth_schema: str, tmp_path: Path
):
    """Shape (a), at production's size: one whole batch of artifact rows with no payload file, and a
    newer artifact behind them that a pinned cursor never reaches."""
    payload_root = tmp_path / "payloads"
    unflattenable = [
        _artifact_row(
            identifier=f"old{index:029d}",
            kind="video.transcript",
            target=f"vidold{index}",
            digest=GONE_DIGEST,
            fetched_at=T0 + timedelta(seconds=index),
        )
        for index in range(DEFAULT_BATCH_SIZE)
    ]
    digest, byte_count = _stored(payload_root, "video.metadata", {"video_id": "vidnew", "title": "a title"})
    newer = _artifact_row(
        identifier=f"new{1:029d}",
        kind="video.metadata",
        target="vidnew",
        digest=digest,
        byte_count=byte_count,
        fetched_at=T0 + timedelta(days=1),
    )
    _insert_artifacts(tubedepth_schema, [*unflattenable, newer])

    assert _flatten(tubedepth_schema, payload_root, at=T0 + timedelta(days=2)) == 1
    assert _cursor(tubedepth_schema) == (unflattenable[-1]["fetched_at"], unflattenable[-1]["identifier"])

    assert _flatten(tubedepth_schema, payload_root, at=T0 + timedelta(days=2, minutes=15)) == 0
    assert _flattened_video_ids(tubedepth_schema) == ["vidnew"]
    assert _cursor(tubedepth_schema) == (newer["fetched_at"], newer["identifier"])


def test_a_mixed_batch_records_the_artifact_it_skipped(tubedepth_schema: str, tmp_path: Path):
    """Shape (b): the successes around it still land, and the one that did not is written down with
    its reason and the time, where a human can find and count it."""
    payload_root = tmp_path / "payloads"
    skipped = _artifact_row(
        identifier=f"gone{0:028d}",
        kind="video.transcript",
        target="vidgone",
        digest=GONE_DIGEST,
        fetched_at=T0,
    )
    digest, byte_count = _stored(payload_root, "video.metadata", {"video_id": "vidgood", "title": "a title"})
    good = _artifact_row(
        identifier=f"good{0:028d}",
        kind="video.metadata",
        target="vidgood",
        digest=digest,
        byte_count=byte_count,
        fetched_at=T0 + timedelta(minutes=1),
    )
    _insert_artifacts(tubedepth_schema, [skipped, good])

    at = T0 + timedelta(hours=1)
    assert _flatten(tubedepth_schema, payload_root, at=at) == 1
    assert _flattened_video_ids(tubedepth_schema) == ["vidgood"]

    records = _records(tubedepth_schema)
    assert len(records) == 1
    record = records[0]
    assert record.target == skipped["identifier"]
    assert record.state == "failed"
    assert record.error_code == "payload_unreadable"
    assert "video.transcript" in record.error_message
    assert "vidgone" in record.error_message
    assert record.finished_at == at
    # An artifact whose payload file is gone cannot be flattened by any later pass, so its record is
    # final on its first attempt -- nothing is owed it.
    assert record.attempt_count == record.max_attempts


def test_an_artifact_with_no_payload_file_is_not_attempted_again(tubedepth_schema: str, tmp_path: Path):
    payload_root = tmp_path / "payloads"
    _insert_artifacts(
        tubedepth_schema,
        [
            _artifact_row(
                identifier=f"gone{0:028d}",
                kind="video.transcript",
                target="vidgone",
                digest=GONE_DIGEST,
                fetched_at=T0,
            )
        ],
    )

    first = T0 + timedelta(hours=1)
    assert _flatten(tubedepth_schema, payload_root, at=first) == 1
    assert _flatten(tubedepth_schema, payload_root, at=first + timedelta(minutes=15)) == 0

    records = _records(tubedepth_schema)
    assert len(records) == 1
    assert records[0].attempt_count == 1
    assert records[0].finished_at == first


def test_a_database_error_is_retried_by_the_next_pass(tubedepth_schema: str, tmp_path: Path):
    """A refusal from the database is not the payload's fault, so it is not skipped for good on its
    first failure -- and it takes neither the neighbour in its batch nor the cursor down with it."""
    payload_root = tmp_path / "payloads"
    snapshot_digest, snapshot_bytes = _stored(
        payload_root, "video.metadata", {"video_id": "vidrefused", "title": "a title"}
    )
    refused = _artifact_row(
        identifier=f"refused{0:025d}",
        kind="video.metadata",
        target="vidrefused",
        digest=snapshot_digest,
        byte_count=snapshot_bytes,
        fetched_at=T0,
    )
    transcript_digest, transcript_bytes = _stored(payload_root, "video.transcript", TRANSCRIPT_PAYLOAD)
    neighbour = _artifact_row(
        identifier=f"neighbour{0:023d}",
        kind="video.transcript",
        target="vidneighbour",
        digest=transcript_digest,
        byte_count=transcript_bytes,
        fetched_at=T0 + timedelta(minutes=1),
    )
    _insert_artifacts(tubedepth_schema, [refused, neighbour])
    _refuse_video_snapshot_writes(tubedepth_schema)

    first = T0 + timedelta(hours=1)
    assert _flatten(tubedepth_schema, payload_root, at=first) == 1
    assert _flattened_video_ids(tubedepth_schema) == ["vidneighbour"]
    assert _cursor(tubedepth_schema) == (neighbour["fetched_at"], neighbour["identifier"])
    records = _records(tubedepth_schema)
    assert len(records) == 1
    assert records[0].target == refused["identifier"]
    assert records[0].state == "failed"
    assert records[0].attempt_count < records[0].max_attempts

    _allow_video_snapshot_writes(tubedepth_schema)
    second = first + timedelta(minutes=15)
    assert _flatten(tubedepth_schema, payload_root, at=second) == 0
    assert _flattened_video_ids(tubedepth_schema) == ["vidneighbour", "vidrefused"]
    records = _records(tubedepth_schema)
    assert len(records) == 1
    assert records[0].state == "succeeded"
    assert records[0].finished_at == second


def test_a_database_error_stops_being_retried_once_its_attempts_are_spent(
    tubedepth_schema: str, tmp_path: Path
):
    """The other half of the retry rule: bounded, so a refusal that never clears cannot pin a pass
    either -- the record goes final and later passes leave it alone."""
    payload_root = tmp_path / "payloads"
    digest, byte_count = _stored(
        payload_root, "video.metadata", {"video_id": "vidrefused", "title": "a title"}
    )
    _insert_artifacts(
        tubedepth_schema,
        [
            _artifact_row(
                identifier=f"refused{0:025d}",
                kind="video.metadata",
                target="vidrefused",
                digest=digest,
                byte_count=byte_count,
                fetched_at=T0,
            )
        ],
    )
    _refuse_video_snapshot_writes(tubedepth_schema)

    at = T0 + timedelta(hours=1)
    for _ in range(4):
        _flatten(tubedepth_schema, payload_root, at=at)
        at += timedelta(minutes=15)

    records = _records(tubedepth_schema)
    assert len(records) == 1
    assert records[0].max_attempts > 1
    assert records[0].attempt_count == records[0].max_attempts
    # The pass after the last attempt has nothing left to do and says so.
    assert _flatten(tubedepth_schema, payload_root, at=at) == 0
