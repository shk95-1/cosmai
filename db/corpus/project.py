"""`project:corpus` -- the live lineage's documents, projected out of `tubedepth` (fork #95, #93 D1·D2).

The archive loader beside this file reads CSVs that will never be produced again. This one reads the
collector's own tables and writes the same three columns' worth of meaning under a **second**
snapshot, so trend · judge · evidence run on live rows without a line changing. Four properties carry
the whole stage, and every one of them fails silently if it is got wrong:

1. **`source_metadata` is copied, never rebuilt.** `tubedepth.video_snapshots.source_metadata` already
   carries the archive's nine keys in the archive's spelling (`contracts/ddl/tubedepth/004`), so the
   video side of this projection is one column assignment. Re-assembling it key by key is where the
   spelling is lost -- a JSON `true` instead of the string `"True"` makes
   `analysis/sensitivity/pipeline.py`'s `declared == DECLARED` false for every live row and the
   declared half of ad marking becomes zero with no error and the rows still returned.
2. **The comment side has no column to copy**, so it is built here -- and it is built with the same
   `_repr_str` the flatten pass writes the video side with, imported rather than re-spelled. The three
   ways a rebuilt object diverges quietly are a Python-repr boolean written as a JSON boolean, a
   missing value written as the string `"None"` instead of jsonb `null`, and a key dropped because it
   is always null (`parent_comment_id`). `tests/test_project_corpus.py` has one arm for each.
3. **The author is only ever a hash**, and this module never spells that hash itself: `author_hash`
   comes from `db/corpus/author.py` (#92). A second implementation drifting by one character matches
   no creator comment and says so nowhere.
4. **Nothing is ever overwritten.** Every INSERT is `ON CONFLICT DO NOTHING` on
   `(snapshot_id, source, source_item_id)`, so `collected_at` is the first observation and never moves
   (#93 D2) -- the current view and like counts stay on the `tubedepth` side, where they are current.
   An archive snapshot is refused outright rather than trusted to that key.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql

from analysis.retrieval.normalize import normalize_text

# The spelling of the archive's `source_metadata`, imported from the pass that writes the video half of
# it. A private name across a package boundary is deliberate and is the cheaper of the two risks: the
# fork may not add a public alias to an upstream file, a second spelling here would drift in silence,
# and a rename upstream breaks this import loudly at import time.
from collectors.youtube.flatten import _ARCHIVE_INSTANT, _repr_str
from collectors.youtube.models import (
    COMMENT_INCLUDE_REPLIES,
    COMMENT_REFETCH_WINDOW_DAYS,
    MAX_COMMENTS_PER_VIDEO,
)
from db.corpus import contract
from db.corpus.author import AUTHOR_HASH, author_hash
from db.seed import panel

YOUTUBE_SCHEMA = "tubedepth"

VIDEO = "youtube_video"
COMMENT = "youtube_comment"

VIDEO_LONG = "video_long"
VIDEO_SHORT = "video_short"
VIDEO_UNKNOWN = "video_unknown"
CORPUS_COMMENT = "comment"

EMPTY_TEXT = "empty_text"
DUPLICATE_IN_PARENT = "duplicate_in_parent"
CLEAN = ""

WATCH_URL = "https://www.youtube.com/watch?v="

# ydc `to_common_schema.py` at the import pin v0.4.0 76db718, verbatim. **The comparison is
# inclusive**: 611 of the archive's videos have a duration of exactly 60, so `< 60` moves every one of
# them out of video_short and fails to reproduce the archive's 7,085 · 6,888 · 6 split
# (contracts/formats.md §Corpus snapshot, and the measurement on fork #95).
SHORTS_MAX_SECONDS = 60

LIVE_LABEL = "live-tubedepth"
LIVE_PRODUCED_BY = "db/corpus/project.py"
LIVE_NOTE = "live lineage projected from tubedepth (#93 D1)"
# One value for every live row. The archive's `source_run` names one of ydc's two discrete collection
# runs; a lineage that grows continuously has no such run, and naming the artifact per row instead
# would make `corpus_snapshot.source_runs` an unbounded list of artifact ids rewritten on every pass.
# Which fetch produced a row stays answerable on the tubedepth side, where `needs.collection_lineage`
# already asks it.
SOURCE_RUN = "live:tubedepth"

ARCHIVE_LINEAGE = "archive"
LIVE_LINEAGE = "live"

# Videos per page. Every page is one transaction, for the reason db/corpus's loader gives: needs_runtime
# runs under a 30s statement_timeout, and a comment fan-out of up to MAX_COMMENTS_PER_VIDEO rides on
# each of these.
BATCH = 200

LISTING_KINDS = ("channel.videos", "search.videos", "playlist.items", "trending.videos")

Progress = Callable[[str, int], None]


class NoLivePopulation(LookupError):
    """There is nothing to project yet. Blocked rather than failed, the same as `cosmai trend quarter`
    with no snapshot: the collector has not been stood up, so there is no run to call unsuccessful."""


class ArchiveSnapshotRefused(ValueError):
    """The resolved snapshot is the archive. #93 D0 makes the archive read-only, and a projection that
    landed on it would be indistinguishable afterwards from the handover's own rows."""


PANEL_SQL: LiteralString = "SELECT channel_id FROM panel_channel WHERE version = %s AND active"
SNAPSHOT_BY_LABEL: LiteralString = "SELECT snapshot_id, lineage FROM corpus_snapshot WHERE label = %s"
SNAPSHOT_BY_ID: LiteralString = "SELECT lineage FROM corpus_snapshot WHERE snapshot_id = %s"
NEXT_SNAPSHOT_ID: LiteralString = "SELECT coalesce(max(snapshot_id), 0) + 1 FROM corpus_snapshot"
SNAPSHOT_INSERT: LiteralString = """
INSERT INTO corpus_snapshot
  (snapshot_id, label, produced_by, source_runs, collected_at, note, lineage, instrument)
VALUES (%s, %s, %s, %s, %s, %s, 'live', %s::jsonb)
ON CONFLICT (snapshot_id) DO NOTHING
"""
# `active` is not in either statement. Switching the analysis over to the live lineage is a production
# UPDATE the coordinator runs on purpose (#93 D5, the completion report's production steps) -- a cron
# line that could move it would move it at 3am. `collected_at` only ever falls: a backfill reaching
# further back makes the lineage's earliest observation earlier, and 023 defines this column as that
# earliest instant. `instrument` is merged rather than replaced so #96's `dictionary_version` survives
# the next projection pass.
SNAPSHOT_REFRESH: LiteralString = """
UPDATE corpus_snapshot
   SET instrument = instrument || %s::jsonb,
       collected_at = least(collected_at, %s)
 WHERE snapshot_id = %s
"""
DOCUMENT_SQL: LiteralString = """
INSERT INTO corpus_document
  (snapshot_id, source, source_item_id, content_type, parent_item_id, channel_id,
   published_at, url, text, quality_flags, source_metadata, collected_at, source_run)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
ON CONFLICT (snapshot_id, source, source_item_id) DO NOTHING
"""
DOCUMENT_COUNT: LiteralString = "SELECT count(*) FROM corpus_document WHERE snapshot_id = %s"
# Narrowed to this stage's own rows. `needs.author_identifier_violation` also lists
# `tubedepth.comments` for the raw author id and display name the collector still writes, and those
# rows are upstream's to fix -- a projection reporting itself partial because of them would be partial
# on every pass and stop meaning anything. What the run answers for is what it wrote.
VIOLATIONS: LiteralString = (
    "SELECT violation, row_key FROM author_identifier_violation"
    " WHERE relation = 'needs.corpus_document' ORDER BY violation, row_key LIMIT 20"
)
LISTING_ROUTES: LiteralString = (
    "SELECT listing_route FROM live_listing_route WHERE listing_route IS NOT NULL ORDER BY 1"
)

FIND_RUN: LiteralString = "SELECT run_id FROM analysis_run WHERE note = %s ORDER BY run_id LIMIT 1"
REOPEN_RUN: LiteralString = (
    "UPDATE analysis_run SET status = 'running', finished_at = NULL, versions = %s::jsonb WHERE run_id = %s"
)
OPEN_RUN: LiteralString = (
    "INSERT INTO analysis_run (status, versions, note) VALUES ('running', %s::jsonb, %s) RETURNING run_id"
)
CLOSE_RUN: LiteralString = "UPDATE analysis_run SET status = %s, finished_at = now() WHERE run_id = %s"

# DISTINCT ON with the earliest fetch first: the row this lineage keeps is the **first** observation of
# a video, because `collected_at` is that instant and never moves (#93 D2). `source_metadata IS NOT
# NULL` sits in the WHERE rather than after the pick for the reason that makes it load-bearing: rows
# flattened before contracts/ddl/tubedepth/004 have no document at all, and taking the earliest row
# unconditionally would freeze a video's document with an empty `source_metadata` forever -- ad marking
# for that video would then read zero with nothing to notice it by. Such a video is left for the next
# fetch instead, and `deferred_videos` counts them.
VIDEO_PAGE = pgsql.SQL("""
SELECT DISTINCT ON (v.video_id)
       v.video_id, v.channel_id, v.title, v.published_at, v.duration_seconds,
       v.fetched_at, v.source_metadata::text
  FROM {schema}.video_snapshots v
 WHERE v.channel_id = ANY(%(channels)s)
   AND v.source_metadata IS NOT NULL
   AND v.fetched_at <= %(cutoff)s
   AND v.video_id > %(after)s
 ORDER BY v.video_id, v.fetched_at, v.artifact_id
 LIMIT %(batch)s
""")
FIRST_OBSERVATION = pgsql.SQL("""
SELECT min(v.fetched_at)
  FROM {schema}.video_snapshots v
 WHERE v.channel_id = ANY(%(channels)s)
   AND v.source_metadata IS NOT NULL
   AND v.fetched_at <= %(cutoff)s
""")
DEFERRED_VIDEOS = pgsql.SQL("""
SELECT count(DISTINCT v.video_id)
  FROM {schema}.video_snapshots v
 WHERE v.channel_id = ANY(%(channels)s)
   AND v.fetched_at <= %(cutoff)s
   AND NOT EXISTS (
        SELECT 1 FROM {schema}.video_snapshots w
         WHERE w.video_id = v.video_id AND w.source_metadata IS NOT NULL AND w.fetched_at <= %(cutoff)s)
""")
# `listing_entries` is read for this number and nothing else. A listing row carries a title, a channel
# and a duration, so it looks like enough to write a document from -- and it is not: it has no
# `source_metadata`, and ON CONFLICT DO NOTHING would make that emptiness permanent for the video. What
# it can say is how far the metadata fan-out is behind the listing, which is a number an operator needs.
UNFLATTENED_LISTED = pgsql.SQL("""
SELECT count(DISTINCT l.video_id)
  FROM {schema}.listing_entries l
 WHERE l.channel_id = ANY(%(channels)s)
   AND l.fetched_at <= %(cutoff)s
   AND NOT EXISTS (SELECT 1 FROM {schema}.video_snapshots v WHERE v.video_id = l.video_id)
""")
# first_seen_at leads the ordering so that the duplicate flag is stable across incremental passes: a
# comment already stored sorts ahead of one that arrived later, so the row that keeps the clean flag
# stays the row that kept it last time (rule 9 counts the first copy and flags the rest).
COMMENT_PAGE = pgsql.SQL("""
SELECT c.video_id, c.comment_id, c.parent_id, c.text, c.author_id, c.like_count,
       c.published_at, c.first_seen_at
  FROM {schema}.comments c
 WHERE c.video_id = ANY(%(videos)s)
   AND c.first_seen_at <= %(cutoff)s
 ORDER BY c.video_id, c.first_seen_at, c.published_at NULLS LAST, c.comment_id
""")


def content_type_of(duration: object) -> str:
    """ydc `content_type_of` at the import pin, expressed over the **value** rather than the column.

    The archive read an ISO-8601 duration as text and stored `'P0D'` for a live stream, while
    `tubedepth.video_snapshots.duration_seconds` is `integer NULL` and can only ever be a number or
    nothing. Keeping the unparseable branch is what stops `video_unknown` from quietly becoming
    "NULL only" the day something else feeds this function.
    """
    text = "" if duration is None else str(duration).strip()
    if not text:
        return VIDEO_UNKNOWN
    try:
        seconds = float(text)
    except ValueError:
        return VIDEO_UNKNOWN
    return VIDEO_SHORT if seconds <= SHORTS_MAX_SECONDS else VIDEO_LONG


def channel_hash(author_id: str | None) -> str | None:
    """The comment author's channel id as the one hash #92 named, whichever end it arrives hashed at.

    `tubedepth.comments.author_id` holds the raw identifier today and
    `needs.author_identifier_violation` lists every live row for it; the fix belongs at the collector
    (contracts/formats.md, "upstream #183 is the change that stops it at the source"). When it lands,
    this column holds a hash already -- and hashing a hash is the exact silent failure #92 is about, so
    the shape is asked before the function is applied rather than after.
    """
    if not author_id:
        return None
    return author_id if AUTHOR_HASH.fullmatch(author_id) else author_hash(author_id)


def comment_metadata(
    *, comment_id: str, parent_id: str | None, author_id: str | None, like_count: int | None, seen: datetime
) -> dict[str, Any]:
    """The archive's seven comment keys, in the archive's spelling.

    Two of the seven cannot be answered from `tubedepth.comments` and are jsonb `null` rather than
    absent: `parent_comment_id`, which the archive carries as null on all 247,338 of its rows anyway
    (the reply relationship lives in the `parent_item_id` **column**), and `total_reply_count`, which
    the collector does not persist -- `commentThreads.totalReplyCount` reaches the payload and stops
    there. A key dropped because it is always null is one of the three ways this object diverges
    without raising, so both stay present.
    """
    return {
        "author_channel_hash": channel_hash(author_id),
        # A Python-repr boolean, not a JSON one. `is_reply` has no reader today; the identically
        # spelled `has_paid_product_placement` on the video side has one, and one rule for both is what
        # stops the two from drifting.
        "is_reply": _repr_str(parent_id is not None),
        "parent_comment_id": _repr_str(parent_id),
        # yt-dlp's parent_id is the thread the reply hangs under; a top-level comment is its own thread.
        "thread_id": parent_id or comment_id,
        "collected_at": seen.astimezone(UTC).strftime(_ARCHIVE_INSTANT),
        "like_count": _repr_str(like_count),
        "total_reply_count": None,
    }


@dataclass(frozen=True)
class Counts:
    videos: int = 0
    comments: int = 0
    video_rows: int = 0
    comment_rows: int = 0
    deferred_videos: int = 0
    unflattened_listed: int = 0
    undated_videos: int = 0
    undated_comments: int = 0


@dataclass
class Outcome:
    """What one pass did. `status` follows the entrypoint convention: ok, or partial when the invariant
    view has something to say about the rows that were just written."""

    snapshot_id: int
    run_id: int
    cutoff: datetime
    counts: Counts
    violations: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "partial" if self.violations else "ok"

    @property
    def note(self) -> str:
        c = self.counts
        return (
            f"project-corpus snapshot={self.snapshot_id} run={self.run_id}"
            f" cutoff={self.cutoff.isoformat()} videos={c.videos} comments={c.comments}"
            f" new_videos={c.video_rows} new_comments={c.comment_rows}"
            f" deferred_videos={c.deferred_videos} unflattened_listed={c.unflattened_listed}"
            f" undated_videos={c.undated_videos} undated_comments={c.undated_comments}"
        )


def run_note_of(snapshot_id: int) -> str:
    """The `analysis_run.note` this stage is found by. It carries no counts on purpose: `_run_id` finds
    the run by this exact string and reopens it instead of piling up a row per pass, the same shape
    `analysis/trend/pipeline.py` uses, and `needs.analysis_health` reads its first space-delimited
    token as the stage name."""
    return f"project-corpus:snapshot{snapshot_id}"


def _run_id(cur: psycopg.Cursor[Any], note: str, versions: Mapping[str, Any]) -> int:
    payload = json.dumps(versions, ensure_ascii=False)
    cur.execute(FIND_RUN, (note,))
    if found := cur.fetchone():
        cur.execute(REOPEN_RUN, (payload, found[0]))
        return int(found[0])
    cur.execute(OPEN_RUN, (payload, note))
    created = cur.fetchone()
    assert created is not None
    return int(created[0])


def resolve_snapshot(cur: psycopg.Cursor[Any], *, snapshot_id: int | None, label: str) -> int:
    """The live snapshot's id: the one already carrying this label, or the next free number.

    The archive is refused here rather than left to the primary key. `ON CONFLICT DO NOTHING` would
    make a projection onto snapshot 1 look like a clean no-op while any video the archive happens not
    to hold would land in it for good, and afterwards nothing on the row says which lineage wrote it.
    """
    if snapshot_id is None:
        cur.execute(SNAPSHOT_BY_LABEL, (label,))
        found = cur.fetchone()
        if found:
            snapshot_id, lineage = int(found[0]), found[1]
        else:
            cur.execute(NEXT_SNAPSHOT_ID)
            row = cur.fetchone()
            assert row is not None
            return int(row[0])
    else:
        cur.execute(SNAPSHOT_BY_ID, (snapshot_id,))
        row = cur.fetchone()
        lineage = row[0] if row else LIVE_LINEAGE
    if lineage == ARCHIVE_LINEAGE:
        raise ArchiveSnapshotRefused(
            f"snapshot {snapshot_id} is the archive (#93 D0): it is read, never recomputed."
            " The live lineage grows under its own snapshot_id"
        )
    return int(snapshot_id)


def instrument(cur: psycopg.Cursor[Any]) -> dict[str, Any]:
    """How this observation is being made (`corpus_snapshot.instrument`, 030 · #93 D5).

    Every value is read from the collector's own constants rather than typed in again, so the
    instrument recorded on the snapshot and the instrument the collector actually runs cannot part.
    `dictionary_version` is #96's to add, and the merge in SNAPSHOT_REFRESH is what lets it.
    """
    cur.execute(LISTING_ROUTES)
    return {
        # A sorted list, always, even for the one route in use. A scalar would have to choose between
        # two answers the day a second route also answers listings, and the choice that reads best --
        # the latest one -- is the one that hides the other. fork #91 option D has each analysis axis
        # declare the routes it admits, and a list is the shape that question is asked of.
        "listing_route": [route for (route,) in cur.fetchall()],
        # Both halves of the comment instrument. A JSON boolean is correct here and only here: this
        # column is not `source_metadata` and nothing compares it against a Python repr.
        "comment_depth": {"threads": MAX_COMMENTS_PER_VIDEO, "include_replies": COMMENT_INCLUDE_REPLIES},
        "refetch_window_days": COMMENT_REFETCH_WINDOW_DAYS,
        # Not a knob but a limitation, recorded on the row because it is the kind of divergence that is
        # invisible afterwards: `tubedepth.video_snapshots` has no description column and DDL 004's nine
        # keys do not carry one, so a live video's `text` is its title alone while the archive's is
        # title + description (the manifest's text_rule). Every mention count on the live lineage is
        # read against a shorter text than the archive's, and #93 D0 already forbids one series
        # spanning both.
        "text_parts": ["title"],
    }


def video_document(row: Sequence[Any], snapshot_id: int) -> tuple[Any, ...]:
    """One `video_snapshots` row as a `corpus_document` row. `metadata_json` goes in as the text it came
    out as -- it is never parsed into Python values and re-serialised, which is the step the archive's
    spelling would be lost in."""
    video_id, channel_id, title, published_at, duration, fetched_at, metadata_json = row
    text = normalize_text(title)
    contract.check_author(f"{VIDEO}:{video_id}", json.loads(metadata_json), source=VIDEO)
    return (
        snapshot_id,
        VIDEO,
        video_id,
        content_type_of(duration),
        None,  # a video has no parent (023's partial index is on the comment side)
        channel_id,
        published_at,
        f"{WATCH_URL}{video_id}",
        text,
        EMPTY_TEXT if not text else CLEAN,
        metadata_json,
        fetched_at,
        SOURCE_RUN,
    )


def comment_documents(
    rows: Sequence[Sequence[Any]], parents: Mapping[str, str], snapshot_id: int
) -> tuple[list[tuple[Any, ...]], int]:
    """A page of `comments` rows as `corpus_document` rows, plus how many had no time of their own.

    Rule 3 is what the `parents` map is for: a comment's quarter is its parent video's, so it carries
    the parent's `channel_id` and `parent_item_id` and never its own `published_at` is asked for a
    quarter. Rules 8 and 9 are the two flags -- and only ever **one** of them, because every consumer
    matches `quality_flags` exactly (`analysis/trend/pipeline.py`'s `COUNTED_FLAGS` is a value list,
    not a set of tags), so a row carrying both joined would be counted by neither. An empty body wins:
    it is excluded from every count, while a duplicate still counts toward the raw denominator.
    """
    built: list[tuple[Any, ...]] = []
    undated = 0
    seen: dict[tuple[str, str], int] = {}
    for video_id, comment_id, parent_id, body, author_id, like_count, published_at, first_seen_at in rows:
        if published_at is None:
            undated += 1
            continue
        text = normalize_text(body)
        if not text:
            flags = EMPTY_TEXT
        else:
            key = (video_id, text)
            flags = DUPLICATE_IN_PARENT if key in seen else CLEAN
            seen[key] = seen.get(key, 0) + 1
        metadata = comment_metadata(
            comment_id=comment_id,
            parent_id=parent_id,
            author_id=author_id,
            like_count=like_count,
            seen=first_seen_at,
        )
        contract.check_author(f"{COMMENT}:{comment_id}", metadata, source=COMMENT)
        built.append(
            (
                snapshot_id,
                COMMENT,
                comment_id,
                CORPUS_COMMENT,
                video_id,
                parents[video_id],
                published_at,
                f"{WATCH_URL}{video_id}",
                text,
                flags,
                json.dumps(metadata, ensure_ascii=False),
                first_seen_at,
                SOURCE_RUN,
            )
        )
    return built, undated


def _video_pages(
    conn: psycopg.Connection[Any], statement: pgsql.Composed, params: dict[str, Any], batch: int
) -> Iterator[list[Sequence[Any]]]:
    after = ""
    while True:
        with conn.cursor() as cur:
            cur.execute(statement, {**params, "after": after, "batch": batch})
            page = cur.fetchall()
        if not page:
            return
        yield page
        after = page[-1][0]


def _insert(cur: psycopg.Cursor[Any], rows: Sequence[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    cur.executemany(DOCUMENT_SQL, rows)
    return max(cur.rowcount, 0)


def project(  # noqa: PLR0913 -- every argument is a seam one test or the CLI needs
    conn: psycopg.Connection[Any],
    *,
    youtube_schema: str = YOUTUBE_SCHEMA,
    snapshot_id: int | None = None,
    label: str = LIVE_LABEL,
    cutoff: datetime | None = None,
    panel_version: int | None = None,
    batch: int = BATCH,
    progress: Progress | None = None,
) -> Outcome:
    """One projection pass. Idempotent: a second pass over the same `tubedepth` rows inserts 0 rows."""
    schema = {"schema": pgsql.Identifier(youtube_schema)}
    at = cutoff or datetime.now(UTC)
    with conn.cursor() as cur:
        version = panel_version if panel_version is not None else panel.active_version(cur)
        if version is None:
            raise NoLivePopulation("no active panel roster; run `python -m db.seed --only panel` first")
        cur.execute(PANEL_SQL, (version,))
        channels = [channel_id for (channel_id,) in cur.fetchall()]
        if not channels:
            raise NoLivePopulation(f"panel roster version {version} has no active channel")
        scope: dict[str, Any] = {"channels": channels, "cutoff": at}
        cur.execute(FIRST_OBSERVATION.format(**schema), scope)
        row = cur.fetchone()
        first_seen = row[0] if row else None
        if first_seen is None:
            raise NoLivePopulation(
                f"{youtube_schema} holds no flattened video with source_metadata for the "
                f"{len(channels)} panel channels at or before {at.isoformat()}; run"
                " `cosmai collect youtube` (watch -> work -> flatten) first"
            )
        live = resolve_snapshot(cur, snapshot_id=snapshot_id, label=label)
        cur.execute(DEFERRED_VIDEOS.format(**schema), scope)
        deferred = int((cur.fetchone() or (0,))[0])
        cur.execute(UNFLATTENED_LISTED.format(**schema), scope)
        unflattened = int((cur.fetchone() or (0,))[0])
        knobs = json.dumps(instrument(cur), ensure_ascii=False)
        cur.execute(
            SNAPSHOT_INSERT,
            (live, label, LIVE_PRODUCED_BY, [SOURCE_RUN], first_seen, LIVE_NOTE, knobs),
        )
        cur.execute(SNAPSHOT_REFRESH, (knobs, first_seen, live))
        run_id = _run_id(cur, run_note_of(live), {"snapshot": live, "cutoff": at.isoformat()})
    conn.commit()

    videos = comments = video_rows = comment_rows = undated_videos = undated_comments = 0
    for page in _video_pages(conn, VIDEO_PAGE.format(**schema), scope, batch):
        documents = []
        parents: dict[str, str] = {}
        for row in page:
            if row[3] is None:  # published_at: corpus_document.published_at is NOT NULL
                undated_videos += 1
                continue
            documents.append(video_document(row, live))
            parents[row[0]] = row[1]
        with conn.cursor() as cur:
            video_rows += _insert(cur, documents)
            cur.execute(COMMENT_PAGE.format(**schema), {"videos": list(parents), "cutoff": at})
            built, undated = comment_documents(cur.fetchall(), parents, live)
            comment_rows += _insert(cur, built)
        conn.commit()
        videos += len(documents)
        comments += len(built)
        undated_comments += undated
        if progress:
            progress("corpus_document", videos + comments)

    counts = Counts(
        videos=videos,
        comments=comments,
        video_rows=video_rows,
        comment_rows=comment_rows,
        deferred_videos=deferred,
        unflattened_listed=unflattened,
        undated_videos=undated_videos,
        undated_comments=undated_comments,
    )
    outcome = Outcome(snapshot_id=live, run_id=run_id, cutoff=at, counts=counts)
    with conn.cursor() as cur:
        cur.execute(VIOLATIONS)
        outcome.violations = [f"{violation} {row_key}" for violation, row_key in cur.fetchall()]
        cur.execute(CLOSE_RUN, (outcome.status, run_id))
    conn.commit()
    return outcome
