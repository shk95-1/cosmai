"""The archive lineage: DDL 030's two columns and one-archive index, the three fixed archive views,
and the corpus lineage's rows in the pipeline graph (fork #94, decision #93 D0 / D5).

What could quietly go wrong here is the **run resolution**. The archive's reading surface is fixed by
construction only while the run it names is the archive snapshot's run; resolve it wrong -- or
hard-code it -- and the fixed archive silently starts answering with a live run's numbers, which is
the one outcome #93 D0 exists to prevent. So the fixture stands two lineages side by side, each with
its own run and its own output rows, and asks the views to tell them apart.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import psycopg
import pytest
from psycopg import sql as pgsql
from sqlalchemy import create_engine, text

from analysis.judge import THIN
from analysis.trend.pipeline import SCOPE, note_of
from db import seed
from db.seed import pipeline, pipeline_corpus
from db.seed._common import connect

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
DDL = ROOT / "contracts" / "ddl" / "needs" / "030_corpus_lineage.sql"
STAGE_DDL = ROOT / "contracts" / "ddl" / "needs" / "007_pipeline_stage.sql"
VIEWS = (
    ROOT / "db" / "views" / "archive_metrics_topic_quarter.sql",
    ROOT / "db" / "views" / "archive_topic_quarter_judgement.sql",
    ROOT / "db" / "views" / "archive_topic_quarter_evidence.sql",
)
VIEW_NAMES = tuple(p.stem for p in VIEWS)
ARCHIVE, LIVE = 1, 2
PANEL_VERSION = 1
AT = datetime(2026, 8, 19, tzinfo=UTC)


class Lineage(NamedTuple):
    """What the fixture built for one lineage: its run, and the document its evidence points at."""

    run_id: int
    doc_id: str


def _runs_of(view: str) -> pgsql.Composed:
    """The view name is composed rather than interpolated: psycopg's execute takes a literal."""
    return pgsql.SQL("SELECT DISTINCT run_id FROM {}").format(pgsql.Identifier(view))


def _install_views(url: str, schema: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET ROLE needs_owner"))
            for view in VIEWS:
                conn.exec_driver_sql(view.read_text(encoding="utf-8").replace("needs.", f'"{schema}".'))
    finally:
        engine.dispose()


def _snapshot(cur: psycopg.Cursor[Any], snapshot_id: int, label: str, lineage: str | None) -> None:
    if lineage is None:
        cur.execute(
            "INSERT INTO corpus_snapshot (snapshot_id, label, source_runs, collected_at) "
            "VALUES (%s, %s, %s, %s)",
            (snapshot_id, label, ["run-" + label], AT),
        )
    else:
        cur.execute(
            "INSERT INTO corpus_snapshot (snapshot_id, label, source_runs, collected_at, lineage) "
            "VALUES (%s, %s, %s, %s, %s)",
            (snapshot_id, label, ["run-" + label], AT, lineage),
        )


def _document(cur: psycopg.Cursor[Any], snapshot_id: int) -> str:
    cur.execute(
        "INSERT INTO corpus_document (snapshot_id, source, source_item_id, content_type, channel_id, "
        "published_at, text, collected_at, source_run) "
        "VALUES (%s, 'youtube_video', %s, 'video_long', 'c1', %s, 'body', %s, 'r') RETURNING doc_id",
        (snapshot_id, f"v{snapshot_id}", AT, AT),
    )
    row = cur.fetchone()
    assert row
    return str(row[0])


def _run(cur: psycopg.Cursor[Any], snapshot_id: int, status: str = "ok", started_at: datetime = AT) -> int:
    """One analysis run for that snapshot, its note written by the real writer's grammar.

    note_of() rather than a literal: the view resolves the run by that grammar, and a literal here
    would keep passing on the day the writer's format changed -- which is the day the archive's
    surface silently empties.
    """
    cur.execute(
        "INSERT INTO analysis_run (started_at, status, versions, note) "
        "VALUES (%s, %s, '{}'::jsonb, %s) RETURNING run_id",
        (started_at, status, note_of("all", snapshot_id, PANEL_VERSION)),
    )
    row = cur.fetchone()
    assert row
    return int(row[0])


def _output(cur: psycopg.Cursor[Any], run_id: int, snapshot_id: int, doc_id: str) -> None:
    """One metrics row, its verdict, and one evidence row hanging off that verdict."""
    cell = (run_id, "all", "topic", "2026Q1", "youtube_video", "long_form", PANEL_VERSION, "product")
    cur.execute(
        "INSERT INTO metrics_topic_quarter (run_id, scope, topic_key, quarter, source, content_type, "
        "panel_version, panel_role, mentions, documents, quarter_mentions, denom_channels, sample_ok) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 5, 9, 10, 3, true)",
        cell,
    )
    cur.execute(
        "INSERT INTO topic_quarter_judgement (run_id, scope, topic_key, quarter, source, content_type, "
        "panel_version, panel_role, trend_type, judged, evidence_strength, single_source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false, 0, true)",
        (*cell, THIN),
    )
    cur.execute(
        "INSERT INTO topic_quarter_evidence (run_id, scope, topic_key, quarter, source, content_type, "
        "panel_version, panel_role, rank, snapshot_id, doc_id, like_count) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, 0)",
        (*cell, snapshot_id, doc_id),
    )


@pytest.fixture
def lineages(needs_schema: str, needs_runtime_url: str, _schema_name: str) -> dict[str, Lineage]:
    """Two lineages, each with its own trend-quarter run and its own output rows.

    The live snapshot gets a *trend-quarter* run on purpose, not some other stage's: a run that did
    not match the grammar at all would prove nothing, since the thing that has to hold is that the
    snapshot id inside the note is what parts the two.
    """
    _install_views(needs_schema, _schema_name)
    built: dict[str, Lineage] = {}
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO panel_roster (version, note) VALUES (%s, 'test')", (PANEL_VERSION,))
        for snapshot_id, label, lineage, name in (
            (ARCHIVE, "yt-handoff-20260819", "archive", "archive"),
            (LIVE, "live-20260912", "live", "live"),
        ):
            _snapshot(cur, snapshot_id, label, lineage)
            doc_id = _document(cur, snapshot_id)
            built[name] = Lineage(_run(cur, snapshot_id), doc_id)
            _output(cur, built[name].run_id, snapshot_id, doc_id)
        conn.commit()
    return built


# --- DDL 030 ---------------------------------------------------------------------------------


def test_a_snapshot_written_without_a_lineage_is_live(needs_runtime_url: str):
    """Special is never what a row gets by saying nothing -- the same reading 023 gives `active`.

    Every writer of this table inserts a snapshot naming no lineage (db/corpus's loader today,
    #95's project:corpus next), so the other default would make the second snapshot ever loaded
    collide with the one-archive index and no re-collection could land again.
    """
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        _snapshot(cur, LIVE, "live-20260912", None)
        cur.execute("SELECT lineage, instrument FROM corpus_snapshot WHERE snapshot_id = %s", (LIVE,))
        assert cur.fetchone() == ("live", {})


def test_a_second_archive_snapshot_is_refused_and_more_live_ones_are_not(needs_runtime_url: str):
    """One archive, carried by the index rather than by whoever writes the row."""
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        _snapshot(cur, ARCHIVE, "yt-handoff-20260819", "archive")
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            _snapshot(cur, LIVE, "second-archive", "archive")
        conn.rollback()
        _snapshot(cur, LIVE, "live-20260912", None)
        _snapshot(cur, 3, "live-20260913", "live")
        conn.commit()
        cur.execute("SELECT count(*) FROM corpus_snapshot")
        assert cur.fetchone() == (3,)


def test_the_lineage_vocabulary_is_closed(needs_runtime_url: str):
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.CheckViolation):
            _snapshot(cur, ARCHIVE, "yt-handoff-20260819", "staging")
        conn.rollback()


def test_the_ddl_names_every_instrument_key_the_goal_asks_for():
    """The keys are prose in the DDL rather than a CHECK (they grow), so the prose is what is held."""
    body = DDL.read_text(encoding="utf-8")
    for key in ("listing_route", "comment_depth", "refetch_window_days", "dictionary_version"):
        assert key in body, key


# --- the three archive views -----------------------------------------------------------------


@pytest.mark.parametrize("view", VIEW_NAMES)
def test_an_archive_view_returns_the_archive_runs_rows_alone(
    lineages: dict[str, Lineage], needs_runtime_url: str, view: str
):
    """The live lineage's run is a trend-quarter run too, and its rows sit in the same three tables."""
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute(_runs_of(view))
        assert [r[0] for r in cur.fetchall()] == [lineages["archive"].run_id]


def test_the_archive_surface_follows_the_latest_ok_run_of_the_archive_snapshot(
    lineages: dict[str, Lineage], needs_runtime_url: str
):
    """Resolved at query time, not pinned. Production carries exactly one trend-quarter run today, so
    the two things a re-run would change are unmeasurable there and are put in here instead: a later
    run that failed must not take the surface, and a later one that succeeded must -- for the three
    views, not only for the helper."""
    later, latest = AT + timedelta(days=1), AT + timedelta(days=2)
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        failed = _run(cur, ARCHIVE, "failed", latest)
        conn.commit()
        cur.execute("SELECT run_id FROM archive_run")
        assert cur.fetchone() == (lineages["archive"].run_id,), f"the failed run {failed} took over"

        rerun = _run(cur, ARCHIVE, "ok", later)
        _output(cur, rerun, ARCHIVE, lineages["archive"].doc_id)
        conn.commit()
        cur.execute("SELECT run_id, snapshot_id FROM archive_run")
        assert cur.fetchone() == (rerun, ARCHIVE)
        for view in VIEW_NAMES:
            cur.execute(_runs_of(view))
            assert [r[0] for r in cur.fetchall()] == [rerun], view


def test_the_archive_surface_is_empty_while_the_archive_run_is_not_ok(
    lineages: dict[str, Lineage], needs_runtime_url: str
):
    """The window the migration's comment names, and the one that can actually happen.

    `analysis/trend/pipeline.py`'s `_run_id()` finds the run **by note** and re-opens that same row
    rather than inserting another, so a re-run of `cosmai trend quarter` against the archive snapshot
    does not add a row -- it flips the one row to `running`, and an aborted run leaves it there.
    `partial` does the same, and since shk95-1/cosmai#201 that is what a run with nothing to write
    closes itself as -- while a run with no population at all no longer opens one, so it no longer
    touches this row. Under #93 D0 the archive is never recomputed so this should not arise, but if
    it does the surface has to go **empty rather than wrong**, and that is the trade `archive_run`'s
    status filter makes. The re-run test above
    fabricates a second run row with the same note, which is a state `_run_id()` cannot produce;
    this is the state it can.
    """
    archive_run = lineages["archive"].run_id
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        for status in ("running", "partial"):
            cur.execute("UPDATE analysis_run SET status = %s WHERE run_id = %s", (status, archive_run))
            conn.commit()
            cur.execute("SELECT count(*) FROM archive_run")
            assert cur.fetchone() == (0,), status
            for view in VIEW_NAMES:
                cur.execute(_runs_of(view))
                assert cur.fetchall() == [], f"{view} under status={status}"
        cur.execute("UPDATE analysis_run SET status = 'ok' WHERE run_id = %s", (archive_run,))
        conn.commit()
        cur.execute("SELECT run_id FROM archive_run")
        assert cur.fetchone() == (archive_run,)


def test_the_archive_surface_is_empty_when_no_snapshot_is_marked_archive(
    needs_schema: str, needs_runtime_url: str, _schema_name: str
):
    """An empty answer, never another lineage's rows -- the failure mode this issue is about, and the
    state production stands in between the migration and the coordinator's one-row archive write."""
    _install_views(needs_schema, _schema_name)
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        _snapshot(cur, LIVE, "live-20260912", None)
        conn.commit()
        cur.execute("SELECT count(*) FROM archive_run")
        assert cur.fetchone() == (0,)
        cur.execute("SELECT count(*) FROM archive_metrics_topic_quarter")
        assert cur.fetchone() == (0,)


def test_note_of_still_reproduces_the_note_production_carries():
    """`FIND_RUN` reopens a run by **exact note match**, so `note_of()` must keep reproducing the
    string run 23 was written with — byte for byte.

    Production's archive run carries `trend-quarter:v0.2:<scope>:snapshot1:panel1` and always will:
    #93 D0 says the archive is read and never recomputed, so nothing rewrites it. Add a segment to
    `note_of()` — `content_type` is the one shk95-1/cosmai#200 raises — and the next
    `cosmai trend quarter` against snapshot 1 does not find run 23. It opens a **new** run, and
    `needs.archive_run`'s `strpos(':snapshot1:panel')` still matches that new note, so
    `ORDER BY started_at DESC` hands the archive surface to a freshly computed run. The three
    archive views then answer from a computation instead of from the observation, and **nothing
    raises**: the row counts may even be identical.

    The other fixtures here build their notes with `note_of()` so the grammar follows its writer,
    which is right for them and blind to exactly this — they would move with the change and keep
    passing. This one is the frozen side of the same coupling. The scope is imported rather than
    spelled because it is Korean corpus data (`tool/checks/lang`); everything else is a literal on
    purpose, and that is what has no room for an inserted segment.

    If `note_of()` genuinely must change, this test is the place that says what else has to move:
    run 23's identity, and `needs.archive_run`'s ordering assumption.
    """
    assert note_of(SCOPE, ARCHIVE, PANEL_VERSION) == "trend-quarter:v0.2:" + SCOPE + ":snapshot1:panel1"


def test_no_archive_view_writes_anything():
    """`#94`'s must-hold: the views read. A view cannot write, so what is asked is that none of these
    files carries a statement that would."""
    for path in (DDL, *VIEWS):
        body = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
        assert not re.search(r"\b(UPDATE|DELETE|INSERT|TRUNCATE)\b", body, re.IGNORECASE), path.name


# --- the corpus lineage in the pipeline graph -------------------------------------------------

STAGE_KEYS = {s.stage_key for s in pipeline_corpus.STAGES}
STORES = {e.from_key for e in pipeline_corpus.EDGES if e.from_kind == pipeline.STORE} | {
    e.to_key for e in pipeline_corpus.EDGES if e.to_kind == pipeline.STORE
}
STAGE_REFS = {e.from_key for e in pipeline_corpus.EDGES if e.from_kind == pipeline.STAGE} | {
    e.to_key for e in pipeline_corpus.EDGES if e.to_kind == pipeline.STAGE
}


def test_the_forks_stages_are_disjoint_from_upstreams():
    """Two modules upserting into one table: an overlapping key would make the later load overwrite
    the earlier, and which one won would depend on the group order."""
    assert not STAGE_KEYS & {s.stage_key for s in pipeline.STAGES}
    pairs = {(e.from_key, e.to_key) for e in pipeline_corpus.EDGES}
    assert len(pairs) == len(pipeline_corpus.EDGES)
    assert not pairs & {(e.from_key, e.to_key) for e in pipeline.EDGES}


def test_every_stage_is_in_the_graph_and_every_edge_names_a_declared_stage():
    assert STAGE_REFS == STAGE_KEYS
    assert not [e for e in pipeline_corpus.EDGES if e.from_kind == e.to_kind == pipeline.STAGE]


def test_every_store_is_a_table_this_checkout_declares():
    """The same question tests/test_pipeline_edge.py asks of upstream's edges, asked of the fork's --
    a store key is a contract only while a table of that name exists here.

    The source schemas are read too since fork #95: `project:corpus` reads three `tubedepth` tables,
    and those are declared by the baseline dump under contracts/ddl/current/ rather than by a
    `CREATE TABLE needs.` line. Left out, an edge naming a table nobody has would stay green.
    """
    declared = {
        f"needs.{name}"
        for path in sorted((ROOT / "contracts" / "ddl" / "needs").glob("*.sql"))
        for name in re.findall(r"CREATE TABLE needs\.(\w+)", path.read_text(encoding="utf-8"))
    }
    declared |= {
        name
        for path in sorted((ROOT / "contracts" / "ddl" / "current").glob("app.*.sql"))
        for name in re.findall(
            r"CREATE TABLE (?:IF NOT EXISTS )?([A-Za-z_][\w.]*)", path.read_text(encoding="utf-8")
        )
    }
    assert STORES <= declared, sorted(STORES - declared)


def test_every_arm_is_one_the_stage_table_accepts():
    """007's CHECK is upstream DDL and cannot be widened additively, so a stage declared with an arm
    outside it would fail at load rather than at review (#94 report, decision 2)."""
    found = re.search(r"CHECK \(arm IN \(([^)]*)\)\)", STAGE_DDL.read_text(encoding="utf-8"))
    assert found
    vocabulary = {v.strip().strip("'") for v in found.group(1).split(",")}
    assert {s.arm for s in pipeline_corpus.STAGES} <= vocabulary


def test_only_the_stage_with_a_cron_line_is_enabled():
    """The archive ran once and never runs again (#93 D0); the three analysis stages and match:topic run
    inside fork #96's gated chain, which stays disabled until its gate can pass. An
    enabled stage with nothing scheduling it reads as stalled forever on the ops screen, and the
    reverse -- a scheduled stage left disabled -- reads as 'disabled' while it really runs.
    #181 pauses the project/topic rollout pending #328; tests/test_pipeline_stage.py holds
    the declarations against the current cron lines."""
    assert {s.stage_key for s in pipeline_corpus.STAGES if s.enabled} == set()


def test_the_seed_puts_the_rows_in_and_a_second_run_changes_nothing(needs_runtime_url: str):
    groups = ("pipeline", "pipeline_corpus")
    first = seed.run_all(needs_runtime_url, only=groups)
    assert first == {
        "pipeline_stage": len(pipeline.STAGES) + len(pipeline_corpus.STAGES),
        "pipeline_edge": len(pipeline.EDGES) + len(pipeline_corpus.EDGES),
    }
    assert seed.run_all(needs_runtime_url, only=groups) == first
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT stage_key FROM pipeline_stage WHERE NOT enabled ORDER BY stage_key")
        gated = {s.stage_key for s in pipeline_corpus.STAGES if not s.enabled}
        assert {r[0] for r in cur.fetchall()} >= gated
