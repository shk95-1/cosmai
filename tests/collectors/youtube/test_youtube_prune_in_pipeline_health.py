"""#280 end to end: a real `prune` pass, the real views, `needs.pipeline_health`.

The issue's own sentence is the bar -- "a prune that dies every night and a prune that succeeds
every night read the same" -- and nothing short of the deployed views answers it: the run row could
be written perfectly and still never reach the screen, which is exactly what happened to the
`flatten` records #275 added (`collector_health` counted them, `pipeline_health` never saw a
denominator). So the three real files are applied here -- analysis_health, collector_health,
pipeline_health -- over a real `tubedepth` schema that a real pass writes into.

The stage row is `db/seed/pipeline.py`'s own declaration rather than a literal: `youtube:prune` is
declared enabled on a one-day cadence there, and a test that invented its own interval would be
green against a stage production does not have.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy import exc as sa_exc
from sqlalchemy.engine import make_url

from collectors.youtube import cli
from collectors.youtube.cli import run
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage.tables import artifacts
from db.seed.pipeline import STAGES

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[3]
VIEWS = ("analysis_health", "collector_health", "pipeline_health")
STAGE_KEY = "youtube:prune"
COLUMNS = "stage_key, last_success_at, last_run_at, last_run_status, freshness, requests, ok, failed"


def _prune_stage() -> Any:
    return next(stage for stage in STAGES if stage.stage_key == STAGE_KEY)


def _td_url(url: str, td_schema: str) -> str:
    """The same server, with the collector's own schema on the search_path -- what `cli.run` is
    handed in production, where `tubedepth` is the role's only schema."""
    return (
        make_url(url)
        .update_query_dict({"options": f"-csearch_path={td_schema},pg_catalog"})
        .render_as_string(hide_password=False)
    )


def _stand_up(url: str, schema: str, td_schema: str) -> None:
    stage = _prune_stage()
    engine = create_engine(url)
    with engine.begin() as conn:
        # In production the needs_owner block of db/grants/needs_runtime_reader.sql opens these.
        # The commerce arm is stood up too, empty: collector_health is one view over three arms and
        # a role that cannot read one of them cannot read the youtube rows either.
        conn.exec_driver_sql(
            f'GRANT SELECT ON "{schema}".run, "{schema}".fetch_log, "{schema}".run_source TO needs_owner'
        )
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{td_schema}" TO needs_owner')
        conn.exec_driver_sql(f'GRANT SELECT ON "{td_schema}".jobs TO needs_owner')
        conn.exec_driver_sql(f'GRANT SELECT ON "{td_schema}".collector_runs TO needs_owner')
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".pipeline_stage'
            " (stage_key, arm, dataset, expected_interval, enabled, note)"
            " VALUES (%s, %s, %s, %s::interval, %s, %s)",
            (stage.stage_key, stage.arm, stage.dataset, stage.expected_interval, stage.enabled, ""),
        )
        for name in VIEWS:
            sql = (REPO_ROOT / "db" / "views" / f"{name}.sql").read_text(encoding="utf-8")
            conn.exec_driver_sql(
                sql.replace("needs.", f'"{schema}".')
                .replace("trend_radar.", f'"{schema}".')
                .replace("tubedepth.", f'"{td_schema}".')
            )
    engine.dispose()


def _stage_row(runtime_url: str) -> Any:
    engine = create_engine(runtime_url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text(f"SELECT {COLUMNS} FROM pipeline_health WHERE stage_key = :k"), {"k": STAGE_KEY}
            ).one()
    finally:
        engine.dispose()


def _stale_artifacts(url: str, payloads: PayloadStore, *, count: int, at: datetime, tag: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        for index in range(count):
            stored = payloads.put("video", {"n": f"{tag}-{index}"})
            conn.execute(
                sa.insert(artifacts).values(
                    identifier=f"{tag}{index}",
                    kind="video",
                    target=f"target-{tag}{index}",
                    fingerprint=f"video:target-{tag}{index}",
                    digest=stored.digest,
                    byte_count=2,
                    fetched_at=at - timedelta(days=cli.PRUNE_MAX_AGE_DAYS + 1),
                    fresh_until=at - timedelta(days=cli.PRUNE_MAX_AGE_DAYS + 1),
                    schema_version="1",
                )
            )
    engine.dispose()


@pytest.fixture
def stood_up(
    needs_schema: str,
    trend_radar_schema: str,
    tubedepth_side_schema: str,
    needs_runtime_url: str,
    _schema_name: str,
) -> tuple[str, str]:
    """(the collector's url, the url needs_runtime reads the views through)."""
    _stand_up(needs_schema, _schema_name, tubedepth_side_schema)
    return _td_url(needs_schema, tubedepth_side_schema), needs_runtime_url


def test_a_prune_pass_that_succeeds_reads_as_a_fresh_stage(stood_up: tuple[str, str], tmp_path: Path):
    collector_url, runtime_url = stood_up
    now = datetime.now(UTC)
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(collector_url, payloads, count=2, at=now, tag="old")

    assert run("prune", database_url=collector_url, payload_root=tmp_path, captured_at=now) == 0

    row = _stage_row(runtime_url)
    assert (row.last_run_status, row.freshness) == ("ok", "ok")
    assert row.last_success_at is not None
    assert row.last_run_at is not None
    assert (row.requests, row.ok, row.failed) == (row.requests, row.requests, 0)


def test_a_prune_that_fails_every_night_turns_the_stage_stale_and_failed(
    stood_up: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The issue's headline. A success three days back and failures since: `youtube:prune` is
    declared on a one-day cadence, so freshness has to read `stalled` and `last_run_status` has to
    read `failed` -- the two facts pipeline_health deliberately never folds into one."""
    collector_url, runtime_url = stood_up
    now = datetime.now(UTC)
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(collector_url, payloads, count=2, at=now, tag="old")

    assert (
        run("prune", database_url=collector_url, payload_root=tmp_path, captured_at=now - timedelta(days=3))
        == 0
    )
    assert _stage_row(runtime_url).freshness == "stalled", "the seeded success has to be the stale one"

    def always_fails(conn: sa.Connection, *, cutoff: datetime) -> Any:
        raise sa_exc.OperationalError("DELETE", {}, Exception("canceling statement: lock timeout"))

    monkeypatch.setattr(cli, "_prune_one_batch", always_fails)
    assert run("prune", database_url=collector_url, payload_root=tmp_path, captured_at=now) == 1

    row = _stage_row(runtime_url)
    assert row.last_run_status == "failed"
    assert row.freshness == "stalled"
    assert now - row.last_success_at > timedelta(days=2), row.last_success_at
    assert row.last_run_at > row.last_success_at, "the failing pass is the last run, not the success"


def test_a_capped_pass_still_counts_as_having_run(
    stood_up: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`partial` belongs in "did it run" -- the view's own rule (#154). A capped prune did most of
    its work and leaves the rest to tomorrow, so it must not harden into `stalled`."""
    collector_url, runtime_url = stood_up
    now = datetime.now(UTC)
    payloads = PayloadStore(tmp_path)
    _stale_artifacts(collector_url, payloads, count=6, at=now, tag="lots")
    monkeypatch.setattr(cli, "PRUNE_BATCH_SIZE", 2)
    monkeypatch.setattr(cli, "PRUNE_MAX_BATCHES", 2)

    assert run("prune", database_url=collector_url, payload_root=tmp_path, captured_at=now) == 1

    row = _stage_row(runtime_url)
    assert (row.last_run_status, row.freshness) == ("partial", "ok")
    assert row.last_success_at is not None
