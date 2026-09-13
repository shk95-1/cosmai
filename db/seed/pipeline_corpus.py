"""The corpus lineage's stages and edges -- the share `db/seed/pipeline.py` leaves to the fork (#94).

That file's `needs.corpus_*` note says so in as many words: an upstream checkout has no such table,
so an upstream contract naming one would sit in the same spot as #107/#150, and the edge is the
fork's to add to its own contract. This module is that addition, and it is a separate module rather
than more rows in `pipeline.py` for the same reason -- `pipeline.py` is upstream's file, and
`tests/test_pipeline_stage.py` reads its `STAGES` to hold the crontab against it. The rows here are
all `enabled = False` and carry no cron line, so mixing them in would make that test answer for
stages the crontab is not meant to schedule.

The shapes and both UPSERT statements are imported from `pipeline.py` rather than copied: the
declaration being the source of truth is what makes it an upsert, and two copies of that statement
would be two answers to "what happens when the interval changes".

One value could not be taken from #94's brief as written. `pipeline_stage` carries
`CONSTRAINT pipeline_stage_arm_check CHECK (arm IN ('commerce', 'naver', 'youtube', 'analyze'))`
(contracts/ddl/needs/007_pipeline_stage.sql), which is upstream DDL and cannot be widened additively
-- widening a CHECK means dropping it, and that needs human approval (tests/test_ddl_additive_only.py).
So `corpus:load` takes the arm `analyze`, which is also the arm that is actually true of it:
`pipeline_health`'s two families are the collector arms, which log into `collector_health`, and
analysis, which logs into `analysis_health` -- a corpus load writes a `needs.analysis_run` row, not a
collector one. The stage_key stays `corpus:load`, the name #93's graph and #95/#96 use. Giving the
corpus its own arm is a separate, destructive migration and a decision for the coordinator (#94 report).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg

from db.seed._common import counts, write
from db.seed.pipeline import EDGE_UPSERT, STAGE, STORE, TABLES, UPSERT, Edge, Stage

# Every row here but one is enabled = False. The archive stage is off because it ran once and must
# never run again (#93 D0); the three analysis stages are off because their cron line is #96's to land,
# and a stage declared enabled with nothing scheduling it reads as stalled forever on the ops screen.
# `project:corpus` is the exception because fork #95 landed its cron line at the same time as this row
# -- the two only ever move together (tests/test_pipeline_stage.py holds them against each other).
STAGES: tuple[Stage, ...] = (
    # expected_interval '0' -- the archive is not periodic at all. A period cannot say "never", and
    # enabled = False is what the screen actually reads (pipeline_health colours a disabled stage
    # 'disabled' before it looks at freshness), so the interval is the smallest value that parses.
    Stage(
        "corpus:load",
        "analyze",
        "load",
        "0",
        False,
        "archive: loaded once 2026-08-26, never rerun (#93 D0)",
    ),
    # The arm is `analyze` for the same reason `corpus:load` takes it: 007's CHECK cannot be widened
    # additively, and of the four arms it is the true one -- this stage writes a needs.analysis_run row
    # and no collector_health row. The dataset name is what makes the stage_key `project:corpus`, which
    # is the name #93's graph, the crontab line and needs.pipeline_health all spell.
    Stage(
        "project:corpus",
        "analyze",
        "corpus",
        "1 hour",
        True,
        "live lineage: tubedepth -> needs.corpus_document (#93 D1)",
    ),
    # Fork #96's gated live chain. Its cron line (`cosmai match topic`) is in stack/crontab.d/analyze, and all
    # four rows stay disabled until the chain's gate can pass -- the active snapshot live, its text carrying a
    # description (upstream shk95-1/cosmai#264). Until then every night's run exits blocked having written
    # nothing, and an enabled row would read that as a stage that never ran.
    Stage(
        "match:topic",
        "analyze",
        "topic",
        "1 day",
        False,
        "live lineage: corpus_document -> corpus_mention, gated on lineage and text_parts (#96)",
    ),
    Stage("analyze:trend", "analyze", "trend", "1 day", False, "inside `cosmai match topic`, gated (#96)"),
    Stage("analyze:judge", "analyze", "judge", "1 day", False, "inside `cosmai match topic`, gated (#96)"),
    Stage(
        "analyze:evidence", "analyze", "evidence", "1 day", False, "inside `cosmai match topic`, gated (#96)"
    ),
)


def _writes(stage: str, store: str, note: str = "") -> Edge:
    return Edge(stage, STAGE, store, STORE, note)


def _reads(store: str, stage: str, note: str = "") -> Edge:
    return Edge(store, STORE, stage, STAGE, note)


EDGES: tuple[Edge, ...] = (
    # -- the archive load (db/corpus/, fork #4). One pass wrote all three tables under snapshot 1.
    _writes("corpus:load", "needs.corpus_snapshot", "one row: the 2026-08-19 handover"),
    _writes("corpus:load", "needs.corpus_document", "261,317 documents"),
    _writes("corpus:load", "needs.corpus_mention", "ydc's matcher, at collection time (#93 D0)"),
    # -- the live projection (fork #95). Three reads, one write; the three source tables are #93's
    # graph verbatim, and listing_entries is read for how far the metadata fan-out is behind rather
    # than to write a document from (db/corpus/project.py says why that distinction is load-bearing).
    _writes("project:corpus", "needs.corpus_document", "live snapshot, ON CONFLICT DO NOTHING (#93 D2)"),
    _reads("tubedepth.video_snapshots", "project:corpus", "the video document and its source_metadata"),
    _reads("tubedepth.comments", "project:corpus", "the comment documents"),
    _reads("tubedepth.listing_entries", "project:corpus", "listed videos not yet flattened"),
    _reads("needs.panel_channel", "project:corpus", "the 43 channels projected, active roster"),
    _writes("project:corpus", "needs.corpus_snapshot", "one row: the live lineage"),
    # -- the live match (fork #96). The mentions, and the dictionary version it stamps on the snapshot.
    _writes("match:topic", "needs.corpus_mention", "live snapshot; a dictionary change re-matches it whole"),
    _writes("match:topic", "needs.corpus_snapshot", "instrument.dictionary_version, merged"),
    _reads("needs.corpus_document", "match:topic", "documents with no mention row yet"),
    _reads("needs.aspect_lexicon", "match:topic", "the active retrieval-topic dictionary"),
    # -- the three analysis stages, in the order the graph of #93 draws them.
    _writes("analyze:trend", "needs.metrics_topic_quarter", "cosmai trend quarter"),
    _writes("analyze:judge", "needs.topic_quarter_judgement", "cosmai trend judge"),
    _writes("analyze:evidence", "needs.topic_quarter_evidence", "cosmai trend evidence"),
    # -- Reads. panel_channel is on this list because it is the denominator: without it the quarterly
    # ratio has no population, which is the one thing formats.md says cannot be implicit.
    _reads("needs.corpus_document", "analyze:trend", "the quarterly document population"),
    _reads("needs.corpus_mention", "analyze:trend", "the numerator"),
    _reads("needs.panel_channel", "analyze:trend", "the denominator's roster"),
    _reads("needs.metrics_topic_quarter", "analyze:judge", "the verdict is a derivation of this row"),
    _reads("needs.topic_quarter_judgement", "analyze:evidence", "the cell evidence is asked for"),
    _reads("needs.corpus_document", "analyze:evidence", "the documents the evidence points back at"),
)


def load(cur: psycopg.Cursor[Any], _source: Path) -> dict[str, int]:
    """Loads the corpus lineage's declaration. Neither the slice nor eval/ is read, so source is unused."""
    write(cur, UPSERT, [tuple(s) for s in STAGES])
    write(cur, EDGE_UPSERT, [tuple(e) for e in EDGES])
    return counts(cur, TABLES)
