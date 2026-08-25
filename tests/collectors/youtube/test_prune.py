"""#76: `prune` deleted rows but left the payload files they pointed at, so
`youtube-payloads` only ever grows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from collectors.youtube.cli import run
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage.tables import artifacts

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 8, 25, 3, tzinfo=UTC)
STALE_FETCHED_AT = NOW - timedelta(days=31)
FRESH_FETCHED_AT = NOW - timedelta(days=1)


def _insert_artifact(conn: sa.Connection, *, identifier: str, digest: str, fetched_at: datetime) -> None:
    conn.execute(
        sa.insert(artifacts).values(
            identifier=identifier,
            kind="video",
            target=f"target-{identifier}",
            fingerprint=f"video:target-{identifier}",
            digest=digest,
            byte_count=2,
            fetched_at=fetched_at,
            fresh_until=fetched_at,
            schema_version="1",
        )
    )


def test_prune_deletes_the_payload_file_for_an_expired_artifact_only(tubedepth_schema: str, tmp_path: Path):
    payloads = PayloadStore(tmp_path)
    stale = payloads.put("video", {"n": "stale"})
    fresh = payloads.put("video", {"n": "fresh"})

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        _insert_artifact(conn, identifier="stale1", digest=stale.digest, fetched_at=STALE_FETCHED_AT)
        _insert_artifact(conn, identifier="fresh1", digest=fresh.digest, fetched_at=FRESH_FETCHED_AT)
    engine.dispose()

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    assert not payloads._path_for("video", stale.digest).exists()
    assert payloads._path_for("video", fresh.digest).exists()


def test_prune_keeps_the_file_when_another_row_still_references_the_digest(
    tubedepth_schema: str, tmp_path: Path
):
    payloads = PayloadStore(tmp_path)
    shared = payloads.put("video", {"n": "shared"})

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        # Both stale, same digest -- the digest is only truly orphaned once neither row survives.
        _insert_artifact(conn, identifier="stale1", digest=shared.digest, fetched_at=STALE_FETCHED_AT)
        _insert_artifact(conn, identifier="fresh1", digest=shared.digest, fetched_at=FRESH_FETCHED_AT)
    engine.dispose()

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    assert payloads._path_for("video", shared.digest).exists()


def test_prune_tolerates_a_payload_file_already_gone(tubedepth_schema: str, tmp_path: Path):
    payloads = PayloadStore(tmp_path)
    stale = payloads.put("video", {"n": "stale"})
    payloads._path_for("video", stale.digest).unlink()  # simulate a prior manual/duplicate delete

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        _insert_artifact(conn, identifier="stale1", digest=stale.digest, fetched_at=STALE_FETCHED_AT)
    engine.dispose()

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0
