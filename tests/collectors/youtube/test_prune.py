"""#76: `prune` deleted rows but left the payload files they pointed at, so
`youtube-payloads` only ever grows.

#279: and the lookup that decided which file to unlink ran once per candidate, so the pass's cost
grew with the backlog until it could no longer finish inside the role's `transaction_timeout`. The
shape assertion below is the one that holds it: the failure is invisible at any row count a test can
afford, so what is asserted is that the statement count does not move when the candidate count does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, event

from collectors.youtube import cli
from collectors.youtube.cli import run
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage.tables import artifacts, jobs

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 8, 25, 3, tzinfo=UTC)
STALE_FETCHED_AT = NOW - timedelta(days=31)
FRESH_FETCHED_AT = NOW - timedelta(days=1)


def _insert_artifact(
    conn: sa.Connection, *, identifier: str, digest: str, fetched_at: datetime, kind: str = "video"
) -> None:
    conn.execute(
        sa.insert(artifacts).values(
            identifier=identifier,
            kind=kind,
            target=f"target-{identifier}",
            fingerprint=f"{kind}:target-{identifier}",
            digest=digest,
            byte_count=2,
            fetched_at=fetched_at,
            fresh_until=fetched_at,
            schema_version="1",
        )
    )


def _insert_job(
    conn: sa.Connection,
    *,
    identifier: str,
    digest: str | None,
    finished_at: datetime,
    kind: str = "video",
    state: str = "succeeded",
) -> None:
    conn.execute(
        sa.insert(jobs).values(
            identifier=identifier,
            kind=kind,
            target=f"target-{identifier}",
            state=state,
            attempt_count=1,
            max_attempts=3,
            scheduled_at=finished_at,
            created_at=finished_at,
            finished_at=finished_at,
            payload_digest=digest,
            webhook_attempts=0,
            refresh=False,
        )
    )


@contextmanager
def _statements() -> Iterator[list[str]]:
    """Every statement any engine sends while the pass runs -- `run` builds its own engine, so the
    listener goes on the Engine class rather than on an instance the test owns."""
    seen: list[str] = []

    def record(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def _stale_artifacts(tubedepth_schema: str, payloads: PayloadStore, *, count: int, tag: str) -> None:
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        for index in range(count):
            stored = payloads.put("video", {"n": f"{tag}-{index}"})
            _insert_artifact(
                conn, identifier=f"{tag}{index}", digest=stored.digest, fetched_at=STALE_FETCHED_AT
            )
    engine.dispose()


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


def test_a_pass_issues_the_same_statements_whatever_the_candidate_count(
    tubedepth_schema: str, tmp_path: Path
):
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(tubedepth_schema, payloads, count=3, tag="few")
    with _statements() as few:
        assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    _stale_artifacts(tubedepth_schema, payloads, count=30, tag="many")
    with _statements() as many:
        assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    # Ten times the candidates, the same work: what makes the pass's cost a property of the backlog's
    # existence rather than of its size.
    assert len(many) == len(few), f"3 candidates: {len(few)} statements, 30: {len(many)}"


def test_a_backlog_larger_than_one_batch_drains_batch_by_batch(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(tubedepth_schema, payloads, count=5, tag="one")
    with _statements() as whole:
        assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    monkeypatch.setattr(cli, "PRUNE_BATCH_SIZE", 2)
    _stale_artifacts(tubedepth_schema, payloads, count=5, tag="split")
    with _statements() as split:
        assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(artifacts)).scalar_one() == 0
    engine.dispose()
    assert not list(tmp_path.rglob("*.json"))
    # 5 rows at 2 a batch is three batches where one sufficed, and each batch costs the same two
    # statements -- the pass grows with the number of batches, never with the candidates in one.
    assert len(split) - len(whole) == 4, f"one batch: {len(whole)} statements, three: {len(split)}"


def test_a_fresh_artifact_and_a_fresh_job_are_never_candidates(
    tubedepth_schema: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    payloads = PayloadStore(tmp_path)
    fresh = payloads.put("video", {"n": "fresh"})

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        _insert_artifact(conn, identifier="fresh1", digest=fresh.digest, fetched_at=FRESH_FETCHED_AT)
        _insert_job(conn, identifier="freshjob1", digest=fresh.digest, finished_at=FRESH_FETCHED_AT)
    engine.dispose()

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    assert "pruned 0 artifact(s), 0 finished job(s), 0 payload file(s)" in capsys.readouterr().out
    assert payloads._path_for("video", fresh.digest).exists()
    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(artifacts)).scalar_one() == 1
        assert conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar_one() == 1
    engine.dispose()


def test_prune_keeps_the_file_a_surviving_job_still_names(tubedepth_schema: str, tmp_path: Path):
    payloads = PayloadStore(tmp_path)
    shared = payloads.put("video", {"n": "shared"})

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        _insert_artifact(conn, identifier="stale1", digest=shared.digest, fetched_at=STALE_FETCHED_AT)
        # The job outlives the artifact row: `work` reuses a cached artifact's digest as its own
        # payload_digest, so a file is orphaned only once neither table names it any more.
        _insert_job(conn, identifier="job1", digest=shared.digest, finished_at=FRESH_FETCHED_AT)
    engine.dispose()

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    assert payloads._path_for("video", shared.digest).exists()


def test_a_digest_two_kinds_share_is_pruned_for_the_stale_kind_alone(tubedepth_schema: str, tmp_path: Path):
    payloads = PayloadStore(tmp_path)
    # One body stored under two kinds is one digest and two files, and "still referenced" has always
    # been keyed on the pair, not on the digest alone.
    video = payloads.put("video", {"n": "same"})
    comments = payloads.put("comments", {"n": "same"})
    assert video.digest == comments.digest

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        _insert_artifact(
            conn, identifier="stale1", digest=video.digest, fetched_at=STALE_FETCHED_AT, kind="video"
        )
        _insert_artifact(
            conn, identifier="fresh1", digest=video.digest, fetched_at=FRESH_FETCHED_AT, kind="comments"
        )
    engine.dispose()

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    assert not payloads._path_for("video", video.digest).exists()
    assert payloads._path_for("comments", comments.digest).exists()


def test_the_unlink_happens_only_after_the_deleting_transaction_commits(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    payloads = PayloadStore(tmp_path)
    stale = payloads.put("video", {"n": "stale"})

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        _insert_artifact(conn, identifier="stale1", digest=stale.digest, fetched_at=STALE_FETCHED_AT)
    engine.dispose()

    rows_a_bystander_sees: list[int] = []
    real_delete = PayloadStore.delete

    def watched_delete(self: PayloadStore, kind: str, digest: str) -> bool:
        # A second connection is on its own snapshot, so it counts the row as gone only once the
        # transaction that deleted it has committed. A file delete cannot roll back.
        probe = sa.create_engine(tubedepth_schema)
        with probe.connect() as conn:
            rows_a_bystander_sees.append(
                conn.execute(sa.select(sa.func.count()).select_from(artifacts)).scalar_one()
            )
        probe.dispose()
        return real_delete(self, kind, digest)

    monkeypatch.setattr(PayloadStore, "delete", watched_delete)

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    assert rows_a_bystander_sees == [0]
