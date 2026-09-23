"""#280: a prune that dies every night and a prune that succeeds every night read the same.

`collector_health`'s youtube arm is built from `tubedepth.jobs`, and `prune` writes no job row at
all -- so no health row ever carried dataset `prune`, `pipeline_health` kept `youtube:prune` at
`never`, and #279's defect (a pass unable to finish past ~1,200 candidates) was found by reading
code rather than by the ops screen. `flatten` had the mirror problem: since #275 only its *failures*
reach `jobs`, so one failure in five hundred reads as 0 percent ok -- a count with no denominator.

What this file holds is the run row itself: that a pass writes one, that its counts are the
denominator, and that a capped or partly failed pass says `partial` and exits 1 instead of printing
a line only the cron log holds and exiting 0.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import exc as sa_exc

from collectors.youtube import cli, flatten
from collectors.youtube.cli import run
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage.tables import artifacts, collector_runs

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 9, 20, 3, tzinfo=UTC)
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


def _stale_artifacts(url: str, payloads: PayloadStore, *, count: int, tag: str) -> None:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        for index in range(count):
            stored = payloads.put("video", {"n": f"{tag}-{index}"})
            _insert_artifact(
                conn, identifier=f"{tag}{index}", digest=stored.digest, fetched_at=STALE_FETCHED_AT
            )
    engine.dispose()


def _runs(url: str) -> list[Any]:
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            return list(conn.execute(sa.select(collector_runs).order_by(collector_runs.c.started_at)).all())
    finally:
        engine.dispose()


def _one_run(url: str) -> Any:
    rows = _runs(url)
    assert len(rows) == 1, f"one pass must leave one run row, not {len(rows)}"
    return rows[0]


# --- prune ------------------------------------------------------------------------------------


def test_a_healthy_prune_pass_records_a_run_that_succeeded(tubedepth_schema: str, tmp_path: Path):
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(tubedepth_schema, payloads, count=3, tag="old")

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    row = _one_run(tubedepth_schema)
    assert (row.dataset, row.status) == ("prune", "ok")
    assert (row.started_at, row.finished_at) == (NOW, NOW)
    assert row.attempted == row.succeeded > 0, "a pass that drained the backlog ran every batch it began"
    assert (row.failed, row.skipped) == (0, 0)
    assert "pruned 3 artifact(s)" in row.note


def test_a_capped_pass_reports_partial_and_exits_1(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """#279 left the per-pass cap printing a line only the cron log holds, and still exiting 0. A
    capped pass has work it did not do, which is what `partial` means."""
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(tubedepth_schema, payloads, count=6, tag="lots")
    monkeypatch.setattr(cli, "PRUNE_BATCH_SIZE", 2)
    monkeypatch.setattr(cli, "PRUNE_MAX_BATCHES", 2)

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 1

    row = _one_run(tubedepth_schema)
    assert (row.dataset, row.status) == ("prune", "partial")
    assert (row.attempted, row.succeeded, row.failed) == (2, 2, 0)
    assert "cap" in row.note, "the row has to say why the pass stopped short"


def test_a_pass_whose_batch_cannot_finish_reports_partial_rather_than_a_traceback(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The batch that raises is the one #279's shape makes possible: a lock timeout, a deadlock, a
    connection lost. The pass kept the batches it committed, so it is partial, not failed."""
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(tubedepth_schema, payloads, count=6, tag="boom")
    monkeypatch.setattr(cli, "PRUNE_BATCH_SIZE", 2)
    real_batch = cli._prune_one_batch
    calls: list[int] = []

    def fails_on_the_second(conn: sa.Connection, *, cutoff: datetime) -> Any:
        calls.append(1)
        if len(calls) == 2:
            raise sa_exc.OperationalError("DELETE", {}, Exception("canceling statement: lock timeout"))
        return real_batch(conn, cutoff=cutoff)

    monkeypatch.setattr(cli, "_prune_one_batch", fails_on_the_second)

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 1

    row = _one_run(tubedepth_schema)
    assert (row.dataset, row.status) == ("prune", "partial")
    assert (row.attempted, row.succeeded, row.failed) == (2, 1, 1)


def test_a_pass_that_lands_no_batch_at_all_reports_failed(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(tubedepth_schema, payloads, count=2, tag="dead")

    def always_fails(conn: sa.Connection, *, cutoff: datetime) -> Any:
        raise sa_exc.OperationalError("DELETE", {}, Exception("canceling statement: lock timeout"))

    monkeypatch.setattr(cli, "_prune_one_batch", always_fails)

    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 1

    row = _one_run(tubedepth_schema)
    assert (row.dataset, row.status) == ("prune", "failed")
    assert (row.attempted, row.succeeded, row.failed) == (1, 0, 1)


# --- flatten ----------------------------------------------------------------------------------


def _artifact_with_payload(url: str, payloads: PayloadStore, *, identifier: str, readable: bool) -> None:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        stored = payloads.put("video.metadata", {"id": identifier, "title": f"title {identifier}"})
        _insert_artifact(
            conn,
            identifier=identifier,
            digest=stored.digest,
            fetched_at=FRESH_FETCHED_AT,
            kind="video.metadata",
        )
        if not readable:
            payloads._path_for("video.metadata", stored.digest).unlink()
    engine.dispose()


def test_a_flatten_pass_records_the_denominator_its_failure_count_needs(
    tubedepth_schema: str, tmp_path: Path
):
    """N1 (#275 review): only failures reach `jobs`, so one failure in five hundred reads as 0
    percent ok. The run row is the denominator -- what the pass examined, beside what it flattened."""
    payloads = PayloadStore(tmp_path)
    for index in range(2):
        _artifact_with_payload(tubedepth_schema, payloads, identifier=f"good{index}", readable=True)
    _artifact_with_payload(tubedepth_schema, payloads, identifier="gone0", readable=False)

    assert run("flatten", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 1

    row = _one_run(tubedepth_schema)
    assert (row.dataset, row.status) == ("flatten", "partial")
    assert (row.attempted, row.succeeded, row.failed, row.skipped) == (3, 2, 0, 1)
    assert (row.started_at, row.finished_at) == (NOW, NOW)


def test_a_flatten_pass_with_nothing_to_do_still_records_that_it_ran(tubedepth_schema: str, tmp_path: Path):
    assert run("flatten", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    row = _one_run(tubedepth_schema)
    assert (row.dataset, row.status) == ("flatten", "ok")
    assert (row.attempted, row.succeeded, row.failed, row.skipped) == (0, 0, 0, 0)


def test_a_flatten_pass_the_server_killed_still_leaves_a_failed_run_row(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The pass's own transaction is where the artifacts are written, so a transaction the server
    ends takes the whole pass with it. The run row is written in a transaction of its own, which is
    what lets the stage read `failed` rather than simply going quiet."""
    payloads = PayloadStore(tmp_path)
    _artifact_with_payload(tubedepth_schema, payloads, identifier="good0", readable=True)

    def dies(conn: sa.Connection, payload_store: PayloadStore, **kwargs: Any) -> Any:
        raise sa_exc.OperationalError("SELECT", {}, Exception("terminating: transaction timeout"))

    monkeypatch.setattr(flatten, "run", dies)

    assert run("flatten", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 1

    row = _one_run(tubedepth_schema)
    assert (row.dataset, row.status) == ("flatten", "failed")
    assert (row.attempted, row.succeeded) == (0, 0)


# --- the rows nothing else may read as work ------------------------------------------------------


def test_a_run_row_is_not_a_job_and_never_touches_the_queue(tubedepth_schema: str, tmp_path: Path):
    """The #275 precedent put flatten's failure records in `tubedepth.jobs`; a *run* could not go
    there, because a run has a `partial` and `jobs.state` has not. This holds the other half of that
    decision: the run rows live in a table of their own, so the queue-depth breaker, the per-video
    follow-up cap, the watch dedupe and prune's own finished-job delete cannot see them."""
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(tubedepth_schema, payloads, count=1, tag="old")
    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    engine = sa.create_engine(tubedepth_schema)
    try:
        with engine.begin() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM jobs")).scalar_one() == 0
    finally:
        engine.dispose()


def test_every_run_row_carries_an_identifier_of_its_own(tubedepth_schema: str, tmp_path: Path):
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(tubedepth_schema, payloads, count=1, tag="old")
    assert run("prune", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0
    assert run("flatten", database_url=tubedepth_schema, payload_root=tmp_path, captured_at=NOW) == 0

    rows = _runs(tubedepth_schema)
    assert len({row.identifier for row in rows}) == len(rows) == 2
    for row in rows:
        assert uuid.UUID(hex=row.identifier), row.identifier


# --- work -------------------------------------------------------------------------------------


class _UnusedFetcher:
    def fetch(self, spec: cli.FetchSpec) -> dict[str, Any]:
        raise AssertionError(f"an empty queue must not fetch {spec}")


def test_an_empty_work_queue_records_a_healthy_pass(tubedepth_schema: str, tmp_path: Path):
    assert (
        run(
            "work",
            database_url=tubedepth_schema,
            fetcher=_UnusedFetcher(),
            payload_root=tmp_path,
            captured_at=NOW,
        )
        == 0
    )

    row = _one_run(tubedepth_schema)
    assert (row.dataset, row.status) == ("work", "ok")
    assert (row.attempted, row.succeeded, row.failed, row.skipped) == (0, 0, 0, 0)
    assert "no queued jobs" in row.note


def test_a_work_pass_that_cannot_claim_leaves_a_failed_row(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def cannot_claim(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("claim stopped")

    monkeypatch.setattr(cli, "_claim", cannot_claim)
    assert (
        run(
            "work",
            database_url=tubedepth_schema,
            fetcher=_UnusedFetcher(),
            payload_root=tmp_path,
            captured_at=NOW,
        )
        == 1
    )

    row = _one_run(tubedepth_schema)
    assert (row.dataset, row.status) == ("work", "failed")
    assert "claim stopped" in row.note


def test_an_all_failed_work_batch_is_not_reported_as_partial(tubedepth_schema: str, tmp_path: Path):
    class _FailingFetcher:
        def fetch(self, spec: cli.FetchSpec) -> dict[str, Any]:
            raise RuntimeError(f"no result for {spec.kind}")

    watchlist = tmp_path / "watch.txt"
    watchlist.write_text("video dQw4w9WgXcQ\n")
    assert (
        run(
            "watch",
            database_url=tubedepth_schema,
            watchlist_path=watchlist,
            read_roster=False,
            captured_at=NOW,
        )
        == 0
    )
    assert (
        run(
            "work",
            database_url=tubedepth_schema,
            fetcher=_FailingFetcher(),
            payload_root=tmp_path / "payloads",
            captured_at=NOW,
        )
        == 1
    )

    row = _one_run(tubedepth_schema)
    assert (row.dataset, row.status) == ("work", "failed")
    assert (row.attempted, row.succeeded, row.failed) == (1, 0, 1)


def test_work_completion_is_recorded_after_a_long_pass(
    tubedepth_schema: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    completed = NOW + timedelta(minutes=6)
    monkeypatch.setattr(cli, "_work_finished_at", lambda _injected_at: completed)
    assert (
        run(
            "work",
            database_url=tubedepth_schema,
            fetcher=_UnusedFetcher(),
            payload_root=tmp_path,
            captured_at=NOW,
        )
        == 0
    )

    row = _one_run(tubedepth_schema)
    assert (row.started_at, row.finished_at) == (NOW, completed)
