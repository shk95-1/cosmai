"""`match:topic` -- the live lineage's mentions, matched here, not by the collector (fork #96, #93 D4·D5).

The archive's `corpus_mention` was written by ydc's matcher at collection time. The live lineage's is written
by this stage from `corpus_document`, with `analysis.retrieval.topics.match_topics` on the active
`retrieval-topic` dictionary, so a dictionary bump re-runs this stage and nothing else. Three properties carry
it, and each one fails by returning rows rather than by raising:

1. **The span comes from the predicate that decided the match.** `match_topics` finds a `ko` term by
   substring over the lower-cased text and a `latin` term with the topic's compiled boundary pattern. ydc's
   `first_span` found latin terms by plain substring, and doing that here would let `span_start` point
   inside a longer word the pattern rejected -- a mention whose `matched_term` is not what matched. So the
   span is asked of the same substring test and the same compiled pattern object, and a hit that yields no
   span raises instead of being written without one.
2. **A dictionary change is a full re-match of that snapshot, never a silent mix.** The dictionary's version
   and fingerprint are recorded in `corpus_snapshot.instrument`; when either differs from the active
   dictionary, this snapshot's mention rows are deleted under this stage's own run and matched again.
   The fingerprint rides along because rows can be added to a version that is already active without its
   number moving (`analysis/retrieval/topics.py`, `Topics.stamp`). `MATCHER_VERSION` rides as the third key
   because the fingerprint hashes the dictionary's rows, not the code that matches them (fork #97).
3. **The archive is never written.** An archive snapshot is refused before anything is read, the same way
   `db/corpus/project.py` refuses it; its mentions are ydc's observation (#93 D0).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, LiteralString

import psycopg

from analysis.retrieval import topics as topic_registry
from analysis.retrieval.topics import Topics, match_topics
from db.corpus.project import ARCHIVE_LINEAGE, CLOSE_RUN, ArchiveSnapshotRefused, _run_id

# Documents per page, each page one transaction: needs_runtime runs under a 30s statement_timeout.
BATCH = 1000

Progress = Callable[[str, int], None]

SNAPSHOT_SQL: LiteralString = "SELECT lineage, instrument FROM corpus_snapshot WHERE snapshot_id = %s"
# Merged, never replaced: `project:corpus` owns the other instrument keys and refreshes them every hour.
STAMP_INSTRUMENT: LiteralString = (
    "UPDATE corpus_snapshot SET instrument = instrument || %s::jsonb WHERE snapshot_id = %s"
)
CLEAR_MENTIONS: LiteralString = "DELETE FROM corpus_mention WHERE snapshot_id = %s"
# "Not yet matched" is "has no mention row". A document that matches no topic has none either, so it is read
# again on every pass -- the price of needing no marker table, and the reason a partial page cannot leave a
# document half-matched: a page's mentions commit together.
DOCUMENT_PAGE: LiteralString = """
SELECT d.doc_id, d.text
  FROM corpus_document d
 WHERE d.snapshot_id = %(snapshot)s
   AND d.collected_at <= %(cutoff)s
   AND d.doc_id > %(after)s
   AND NOT EXISTS (SELECT 1 FROM corpus_mention m
                    WHERE m.snapshot_id = d.snapshot_id AND m.doc_id = d.doc_id)
 ORDER BY d.doc_id
 LIMIT %(batch)s
"""
MENTION_SQL: LiteralString = """
INSERT INTO corpus_mention
  (snapshot_id, doc_id, topic_id, topic_type, trend_use, matched_term, span_start)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (snapshot_id, doc_id, topic_id) DO NOTHING
"""

DICTIONARY_VERSION = "dictionary_version"
DICTIONARY_FINGERPRINT = "dictionary_fingerprint"
MATCHER_VERSION = "matcher_version"


class NoSnapshot(LookupError):
    """The named snapshot does not exist. Blocked rather than failed: there is nothing to match yet."""


class SpanDiverged(AssertionError):
    """A topic matched and no span could be derived from the same predicate. Writing the mention without a
    span would hide that the two tests parted."""


@dataclass(frozen=True)
class Mention:
    topic_id: str
    topic_type: str
    trend_use: bool
    matched_term: str
    span_start: int


def _origin(text: str) -> list[int] | None:
    """Where each character of `text.lower()` came from, or None when lowering kept every character single."""
    lowered = text.lower()
    if len(lowered) == len(text):
        return None
    origin: list[int] = []
    for index, char in enumerate(text):
        origin.extend([index] * len(char.lower()))
    return origin


def _spelling(terms: list[str], surface: str) -> str:
    """The dictionary term the compiled alternation matched, recovered from the surface it matched."""
    for term in terms:
        if re.fullmatch(re.escape(term), surface, re.IGNORECASE):
            return term
    raise SpanDiverged(f"the latin pattern matched {surface!r}, which is none of the topic's terms")


def first_span(text: str, entry: dict[str, Any], dictionary: Topics) -> tuple[str, int] | None:
    """The dictionary's spelling and the 0-based character offset of the topic's earliest occurrence.

    ydc `v0.4.0` `first_span`'s order: the lowest offset across the topic's `ko` terms, then its `latin`
    terms, a tie keeping the first found. The two tests are `match_topics`'s own --
    `term.lower() in text.lower()` and `dictionary.latin(topic).search(text)` -- so no span can come from an
    occurrence the match did not accept.
    """
    lowered = text.lower()
    best: tuple[str, int] | None = None
    for term in entry["ko"]:
        at = lowered.find(term.lower())
        if at >= 0 and (best is None or at < best[1]):
            best = (term, at)
    if best is not None and (origin := _origin(text)) is not None:
        best = (best[0], origin[best[1]])
    pattern = dictionary.latin(entry["topic"])
    found = pattern.search(text) if pattern else None
    if found is not None and (best is None or found.start() < best[1]):
        best = (_spelling(entry["latin"], found.group(0)), found.start())
    return best


def mentions_of(text: str, dictionary: Topics) -> list[Mention]:
    """Every topic of the dictionary the text matches, `trend_use` or not (manifest rule 7)."""
    by_topic = {entry["topic"]: entry for entry in dictionary.entries}
    made: list[Mention] = []
    for topic in match_topics(text, include_excluded=True, dictionary=dictionary):
        entry = by_topic[topic]
        span = first_span(text, entry, dictionary)
        if span is None:
            raise SpanDiverged(f"topic {topic!r} matched but no span was found by the same test")
        made.append(Mention(topic, entry["topic_type"], bool(entry["trend_use"]), span[0], span[1]))
    return made


@dataclass(frozen=True)
class MatchOutcome:
    snapshot_id: int
    run_id: int
    cutoff: datetime
    dictionary_version: int | None
    rematched: bool
    cleared: int
    documents: int
    mentions: int

    @property
    def status(self) -> str:
        return "ok"

    @property
    def violations(self) -> list[str]:
        return []

    @property
    def note(self) -> str:
        return (
            f"match-topic snapshot={self.snapshot_id} run={self.run_id} cutoff={self.cutoff.isoformat()}"
            f" dictionary=v{self.dictionary_version} rematched={str(self.rematched).lower()}"
            f" cleared={self.cleared} documents={self.documents} new_mentions={self.mentions}"
        )


def run_note_of(snapshot_id: int) -> str:
    """The `analysis_run.note` the stage is found and re-opened by, one row per snapshot."""
    return f"match-topic:snapshot{snapshot_id}"


def _page(
    conn: psycopg.Connection[Any], snapshot_id: int, cutoff: datetime, after: str, batch: int
) -> list[Any]:
    with conn.cursor() as cur:
        cur.execute(
            DOCUMENT_PAGE, {"snapshot": snapshot_id, "cutoff": cutoff, "after": after, "batch": batch}
        )
        return cur.fetchall()


def match(
    conn: psycopg.Connection[Any],
    *,
    snapshot_id: int,
    cutoff: datetime | None = None,
    batch: int = BATCH,
    progress: Progress | None = None,
) -> MatchOutcome:
    """One pass over the snapshot's documents collected at or before `cutoff`. Idempotent: a second pass over
    the same documents with the same dictionary inserts nothing."""
    at = cutoff or datetime.now(UTC)
    with conn.cursor() as cur:
        cur.execute(SNAPSHOT_SQL, (snapshot_id,))
        found = cur.fetchone()
    conn.commit()
    if found is None:
        raise NoSnapshot(f"corpus snapshot {snapshot_id} does not exist; run `cosmai project corpus` first")
    lineage, instrument = found[0], dict(found[1] or {})
    if lineage == ARCHIVE_LINEAGE:
        raise ArchiveSnapshotRefused(
            f"snapshot {snapshot_id} is the archive (#93 D0): its mentions are ydc's observation and are"
            " never written"
        )
    # `load` commits after reading, so it runs before the transaction that deletes and stamps.
    dictionary = topic_registry.load(conn)
    # Read at call time, not imported by name, so a bump is seen without reloading this module.
    matcher = topic_registry.MATCHER_VERSION
    recorded = (
        instrument.get(DICTIONARY_VERSION),
        instrument.get(DICTIONARY_FINGERPRINT),
        instrument.get(MATCHER_VERSION),
    )
    rematched = recorded != (dictionary.version, dictionary.fingerprint, matcher)
    stamp = {
        DICTIONARY_VERSION: dictionary.version,
        DICTIONARY_FINGERPRINT: dictionary.fingerprint,
        MATCHER_VERSION: matcher,
    }
    versions = {"snapshot": snapshot_id, "cutoff": at.isoformat(), "topics": dictionary.version}
    cleared = 0
    with conn.cursor() as cur:
        run_id = _run_id(cur, run_note_of(snapshot_id), versions)
        if rematched:
            cur.execute(CLEAR_MENTIONS, (snapshot_id,))
            cleared = max(cur.rowcount, 0)
        cur.execute(STAMP_INSTRUMENT, (json.dumps(stamp, ensure_ascii=False), snapshot_id))
    conn.commit()

    documents = mentions = 0
    after = ""
    while page := _page(conn, snapshot_id, at, after, batch):
        rows = [
            (snapshot_id, doc_id, m.topic_id, m.topic_type, m.trend_use, m.matched_term, m.span_start)
            for doc_id, body in page
            for m in mentions_of(body, dictionary)
        ]
        with conn.cursor() as cur:
            if rows:
                cur.executemany(MENTION_SQL, rows)
                mentions += max(cur.rowcount, 0)
        conn.commit()
        documents += len(page)
        after = page[-1][0]
        if progress:
            progress("corpus_mention", mentions)

    with conn.cursor() as cur:
        cur.execute(CLOSE_RUN, ("ok", run_id))
    conn.commit()
    return MatchOutcome(
        snapshot_id=snapshot_id,
        run_id=run_id,
        cutoff=at,
        dictionary_version=dictionary.version,
        rematched=rematched,
        cleared=cleared,
        documents=documents,
        mentions=mentions,
    )
