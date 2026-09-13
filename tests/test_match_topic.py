"""`match:topic` and the gated live chain (fork #96, from #93 D4 · D5).

Every failure this stage can have returns rows and raises nothing, so the tests are arranged by the way each
one would be wrong:

* **A span from a different rule than the match.** ydc found latin terms by substring; `match_topics` uses a
  compiled boundary pattern. A substring span lands inside a word the pattern rejected.
* **A dictionary change that re-matches in place.** A version change deletes this snapshot's mentions and
  matches again, and never touches the archive's.
* **An ungated cron.** `trend quarter` reads the active snapshot, which is the archive until the coordinator
  switches it, so the chain refuses unless the active snapshot is live and its text carries a description.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, LiteralString

import pytest
from sqlalchemy import create_engine, text

from analysis.retrieval import topics as topic_registry
from analysis.retrieval.topics import match_topics
from analysis.trend import live
from analysis.trend.pipeline import SCOPE, note_of
from cosmai.cli import main
from db import corpus, seed
from db.corpus import match as stage
from db.corpus.project import ArchiveSnapshotRefused
from db.seed._common import connect
from tests.retrieval.conftest import csv_topics

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "trend_sample"
VIEWS = (
    ROOT / "db" / "views" / "metrics_topic_quarter_violation.sql",
    ROOT / "db" / "views" / "topic_quarter_judgement_violation.sql",
    ROOT / "db" / "views" / "topic_quarter_evidence_quote.sql",
    ROOT / "db" / "views" / "topic_quarter_evidence_violation.sql",
)
ARCHIVE, LIVE = corpus.SNAPSHOT_ID, corpus.SNAPSHOT_ID + 1


def _dictionary(*rows: tuple[str, str, str, str]) -> topic_registry.Topics:
    """(topic, term, term_kind, trend_use) rows through the loader's own conversion."""
    return topic_registry.from_rows(
        (
            (topic, term, {"term_kind": kind, "topic_type": "attribute", "trend_use": trend})
            for topic, term, kind, trend in rows
        ),
        7,
    )


SYNTHETIC = _dictionary(
    ("alpha", "SPF", "latin", "true"),
    ("beta", "gloss", "ko", "true"),
    ("beta", "sheen", "ko", "true"),
    ("gamma", "matte", "ko", "false"),
)


def _spans(body: str) -> dict[str, tuple[str, int]]:
    return {m.topic_id: (m.matched_term, m.span_start) for m in stage.mentions_of(body, SYNTHETIC)}


# ---------- the span is the match's own ----------
def test_a_latin_term_only_inside_a_longer_word_is_no_mention():
    body = "an unSPFable finish"
    # A substring rule finds it; the pattern that decides the match does not, so neither may the span.
    assert "spf" in body.lower()
    assert match_topics(body, include_excluded=True, dictionary=SYNTHETIC) == []
    assert "alpha" not in _spans(body)


def test_a_latin_term_inside_a_word_and_standalone_spans_the_standalone_one():
    body = "xSPFx first, then SPF 50"
    assert _spans(body)["alpha"] == ("SPF", body.index("SPF 50"))
    assert body.lower().find("spf") != body.index("SPF 50")  # the offset a substring rule would write


def test_matched_term_is_the_dictionarys_spelling_not_the_surface_form():
    assert _spans("spf 50 daily")["alpha"] == ("SPF", 0)


def test_the_lowest_offset_across_the_topics_terms_wins_whatever_their_order():
    body = "a sheen and then gloss"
    assert SYNTHETIC.entries[1]["ko"] == ["gloss", "sheen"]
    assert _spans(body)["beta"] == ("sheen", body.index("sheen"))


def test_every_topic_is_written_including_the_ones_trends_do_not_use():
    written = {m.topic_id: m.trend_use for m in stage.mentions_of("matte gloss", SYNTHETIC)}
    assert written == {"beta": True, "gamma": False}


def test_the_offset_is_into_the_stored_text_even_when_lowercasing_changes_its_length():
    """`str.lower` turns U+0130 into two characters, so an offset read off the lower-cased copy would be one
    to the right of the term in the text production's check reads it against."""
    body = "İ gloss"
    assert len(body.lower()) == len(body) + 1
    term, at = _spans(body)["beta"]
    assert body[at : at + len(term)] == term


def _fixture_rows(name: str) -> list[dict[str, str]]:
    csv.field_size_limit(10**7)
    with (FIXTURE / "corpus" / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_on_the_sample_every_span_reads_back_its_term_and_ydc_found_nothing_we_miss():
    """The production check on 105,358 archive rows, asked of the repo dictionary over the sample: the span
    points at the term, and the ydc-only column of the matcher-difference measurement is empty here too."""
    dictionary = csv_topics()
    ours: set[tuple[str, str]] = set()
    for row in _fixture_rows("document.csv"):
        for mention in stage.mentions_of(row["text"], dictionary):
            ours.add((row["doc_id"], mention.topic_id))
            piece = row["text"][mention.span_start : mention.span_start + len(mention.matched_term)]
            assert piece.lower() == mention.matched_term.lower(), (row["doc_id"], mention)
    theirs = {(row["doc_id"], row["topic_id"]) for row in _fixture_rows("mention.csv")}
    assert theirs - ours == set()


# ---------- the stage against a database ----------
DOCUMENT_COPY: LiteralString = """
INSERT INTO corpus_document
  (snapshot_id, source, source_item_id, content_type, parent_item_id, channel_id, published_at, url,
   text, quality_flags, source_metadata, collected_at, source_run)
SELECT %s, source, source_item_id, content_type, parent_item_id, channel_id, published_at, url,
       text, quality_flags, source_metadata, collected_at, source_run
  FROM corpus_document WHERE snapshot_id = %s
"""
LIVE_SNAPSHOT: LiteralString = """
INSERT INTO corpus_snapshot (snapshot_id, label, source_runs, collected_at, lineage, instrument)
VALUES (%s, 'live-test', ARRAY['live:test'], %s, 'live', %s::jsonb)
"""
MENTIONS: LiteralString = "SELECT count(*) FROM corpus_mention WHERE snapshot_id = %s"


def _one(url: str, statement: LiteralString, params: tuple[Any, ...] = ()) -> Any:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(statement, params)
        row = cur.fetchone()
    return row[0] if row else None


def _execute(url: str, statement: LiteralString, params: tuple[Any, ...] = ()) -> None:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(statement, params)
        conn.commit()


@pytest.fixture
def lineages(needs_schema: str, needs_runtime_url: str, _schema_name: str) -> str:
    """The archive and a live snapshot side by side: snapshot 1 is the sample loaded the archive's way and
    marked `archive`, snapshot 2 is the same documents under the live lineage with no mention yet."""
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET ROLE needs_owner"))
            for view in VIEWS:
                conn.exec_driver_sql(view.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
    finally:
        engine.dispose()
    seed.run_all(needs_runtime_url, only=("panel",))
    where = ["--kind", "aspect", "--version", "1", "--url", needs_runtime_url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0
    with connect(needs_runtime_url) as conn:
        corpus.load(conn, FIXTURE / "corpus")
    _execute(
        needs_runtime_url, "UPDATE corpus_snapshot SET lineage = 'archive' WHERE snapshot_id = %s", (ARCHIVE,)
    )
    instrument = json.dumps({"text_parts": ["title", "description"]})
    _execute(needs_runtime_url, LIVE_SNAPSHOT, (LIVE, datetime(2026, 8, 19, tzinfo=UTC), instrument))
    _execute(needs_runtime_url, DOCUMENT_COPY, (LIVE, ARCHIVE))
    return needs_runtime_url


def _activate(url: str, snapshot_id: int) -> None:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute("UPDATE corpus_snapshot SET active = false WHERE active")
        cur.execute("UPDATE corpus_snapshot SET active = true WHERE snapshot_id = %s", (snapshot_id,))
        conn.commit()


@pytest.mark.postgres
def test_the_stage_writes_the_live_snapshots_mentions_and_never_the_archives(lineages: str):
    url = lineages
    archive_before = _one(url, MENTIONS, (ARCHIVE,))
    assert archive_before > 0
    with connect(url) as conn:
        outcome = stage.match(conn, snapshot_id=LIVE)

    assert outcome.status == "ok"
    assert outcome.rematched is True and outcome.cleared == 0
    assert outcome.mentions == _one(url, MENTIONS, (LIVE,)) > 0
    assert _one(url, MENTIONS, (ARCHIVE,)) == archive_before
    # The production identity the archive satisfies 105,358 of 105,358 times, asked of what was written.
    misses = _one(
        url,
        "SELECT count(*) FROM corpus_mention m JOIN corpus_document d USING (snapshot_id, doc_id)"
        " WHERE m.snapshot_id = %s"
        " AND lower(substr(d.text, m.span_start + 1, length(m.matched_term)))"
        " IS DISTINCT FROM lower(m.matched_term)",
        (LIVE,),
    )
    assert misses == 0
    # All fifteen topics, trend_use as the dictionary says (manifest rule 7).
    assert (
        _one(url, "SELECT count(*) FROM corpus_mention WHERE snapshot_id = %s AND NOT trend_use", (LIVE,)) > 0
    )

    instrument = _one(url, "SELECT instrument FROM corpus_snapshot WHERE snapshot_id = %s", (LIVE,))
    assert instrument["dictionary_version"] == 1
    assert instrument["text_parts"] == ["title", "description"]  # merged, never replaced
    versions = _one(url, "SELECT versions FROM analysis_run WHERE run_id = %s", (outcome.run_id,))
    assert versions["snapshot"] == LIVE and versions["topics"] == 1
    assert datetime.fromisoformat(versions["cutoff"]) == outcome.cutoff


@pytest.mark.postgres
def test_the_archive_snapshot_is_refused_before_anything_is_written(lineages: str):
    url = lineages
    before = _one(url, MENTIONS, (ARCHIVE,))
    with connect(url) as conn, pytest.raises(ArchiveSnapshotRefused):
        stage.match(conn, snapshot_id=ARCHIVE)
    assert _one(url, MENTIONS, (ARCHIVE,)) == before
    assert _one(url, "SELECT count(*) FROM analysis_run WHERE note = %s", (stage.run_note_of(ARCHIVE),)) == 0


@pytest.mark.postgres
def test_a_second_pass_adds_nothing_and_a_dictionary_change_rematches_the_whole_snapshot(lineages: str):
    url = lineages
    archive_before = _one(url, MENTIONS, (ARCHIVE,))
    with connect(url) as conn:
        first = stage.match(conn, snapshot_id=LIVE)
        again = stage.match(conn, snapshot_id=LIVE)
    assert (again.rematched, again.cleared, again.mentions) == (False, 0, 0)
    assert again.run_id == first.run_id

    # A stale row and a recorded version the active dictionary no longer is.
    _execute(url, "UPDATE corpus_mention SET matched_term = 'stale' WHERE snapshot_id = %s", (LIVE,))
    _execute(
        url,
        "UPDATE corpus_snapshot SET instrument = instrument || '{\"dictionary_version\": 0}'"
        " WHERE snapshot_id = %s",
        (LIVE,),
    )
    with connect(url) as conn:
        redone = stage.match(conn, snapshot_id=LIVE)
    assert redone.rematched is True
    assert redone.cleared == first.mentions == redone.mentions
    assert _one(url, "SELECT count(*) FROM corpus_mention WHERE matched_term = 'stale'") == 0
    assert _one(url, MENTIONS, (ARCHIVE,)) == archive_before


@pytest.mark.postgres
def test_a_document_collected_after_the_cutoff_is_not_matched(lineages: str):
    with connect(lineages) as conn:
        outcome = stage.match(conn, snapshot_id=LIVE, cutoff=datetime(2000, 1, 1, tzinfo=UTC))
    assert (outcome.documents, outcome.mentions) == (0, 0)


# ---------- the gate ----------
def _runs(url: str) -> int:
    return _one(url, "SELECT count(*) FROM analysis_run")


@pytest.mark.postgres
def test_the_chain_refuses_while_the_active_snapshot_is_the_archive(lineages: str, capsys: Any):
    """The default state: `corpus.load` left the archive active. Ungated, `trend quarter` would re-open the
    archive's run and recompute it every night."""
    url = lineages
    runs = _runs(url)
    assert main(["match", "topic", "--url", url]) == 2
    out = capsys.readouterr().out
    assert "archive" in out and str(ARCHIVE) in out
    assert _runs(url) == runs
    assert _one(url, MENTIONS, (LIVE,)) == 0


@pytest.mark.postgres
def test_the_chain_refuses_a_live_snapshot_whose_text_is_the_title_alone(lineages: str, capsys: Any):
    url = lineages
    _execute(
        url,
        'UPDATE corpus_snapshot SET instrument = instrument || \'{"text_parts": ["title"]}\''
        " WHERE snapshot_id = %s",
        (LIVE,),
    )
    _activate(url, LIVE)
    runs = _runs(url)
    assert main(["match", "topic", "--url", url]) == 2
    assert live.REQUIRED_TEXT_PART in capsys.readouterr().out
    assert _runs(url) == runs


@pytest.mark.postgres
def test_the_chain_runs_end_to_end_on_a_live_snapshot_and_leaves_the_archive_alone(
    lineages: str, capsys: Any
):
    url = lineages
    archive_mentions = _one(url, MENTIONS, (ARCHIVE,))
    _activate(url, LIVE)
    assert main(["match", "topic", "--url", url]) in (0, 1)
    out = capsys.readouterr().out
    for head in ("match-topic ", "trend quarter ", "trend judge ", "trend evidence "):
        assert head in out, out

    [(run_id, versions)] = _rows_of(
        url, "SELECT run_id, versions FROM analysis_run WHERE note = %s", (note_of(SCOPE, LIVE, 1),)
    )
    assert versions["snapshot"] == LIVE and versions["topics"] == 1 and "cutoff" in versions
    assert _one(url, "SELECT count(*) FROM topic_quarter_judgement WHERE run_id = %s", (run_id,)) > 0
    assert _one(url, "SELECT count(*) FROM topic_quarter_evidence WHERE run_id = %s", (run_id,)) > 0
    assert _one(url, "SELECT count(*) FROM topic_quarter_evidence WHERE snapshot_id <> %s", (LIVE,)) == 0
    assert _one(url, MENTIONS, (ARCHIVE,)) == archive_mentions


def _rows_of(url: str, statement: LiteralString, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(statement, params)
        return cur.fetchall()
