"""Reads the documents to search from the source schemas.

ydc read one `common/document.csv`. Here that file is not made and the DB is read directly -- the collectors
already write the same rows, so a second copy in a CSV would leave nobody able to say which is canonical.

All paging is keyset. needs_runtime is statement_timeout 30s · transaction_timeout 60s, so OFFSET paging
(O(n^2) overall) or a whole scan in one transaction runs into that wall --
analysis/linker/pipeline.py uses the same shape for the same reason.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg import sql as pgsql

BATCH = 2000

# The prefix of doc_id and the source of a chunk. Change the value and the chunks already stored and the new
# ones become different documents.
YOUTUBE_COMMENT = "youtube_comment"
YOUTUBE_VIDEO = "youtube_video"
YOUTUBE_TRANSCRIPT = "youtube_transcript"
COMMERCE_REVIEW = "commerce_review"
SOURCES = (YOUTUBE_COMMENT, YOUTUBE_VIDEO, YOUTUBE_TRANSCRIPT, COMMERCE_REVIEW)


@dataclass(frozen=True)
class Document:
    """One document to search. chunks.py takes this and cuts it into pieces."""

    doc_id: str
    source: str
    text: str


# video_id is picked not because it is a value we want but because it is the leading column of the cursor --
# the natural key of a comment is (video_id, comment_id) and only that order rides the index.
COMMENTS = pgsql.SQL("""
SELECT video_id, comment_id, text FROM {schema}.comments
WHERE (video_id, comment_id) > (%s, %s)
  AND (%s::date IS NULL OR published_at::date >= %s::date)
ORDER BY video_id, comment_id LIMIT %s
""")

# video_snapshots holds the same video several times, so only the newest is used. There is no description
# column, so only the title is used -- ydc joined title + description from the API response
# (video_text in ydc trend.py, v0.1.0 02440ab).
VIDEOS = pgsql.SQL("""
SELECT DISTINCT ON (video_id) video_id, title FROM {schema}.video_snapshots
WHERE video_id > %s AND (%s::date IS NULL OR published_at::date >= %s::date)
ORDER BY video_id, fetched_at DESC LIMIT %s
""")

TRANSCRIPTS = pgsql.SQL("""
SELECT DISTINCT ON (video_id) video_id, full_text FROM {schema}.transcripts
WHERE video_id > %s ORDER BY video_id, fetched_at DESC LIMIT %s
""")

# The natural key of review is (source, review_key). review_key can collide across sites, so both go in.
REVIEWS = pgsql.SQL("""
SELECT source, review_key, body FROM {schema}.review
WHERE (source, review_key) > (%s, %s)
  AND (%s::date IS NULL OR written_at::date >= %s::date)
ORDER BY source, review_key LIMIT %s
""")


def _keyset(conn: psycopg.Connection, query: pgsql.Composed, params: tuple, key_len: int) -> Iterator[tuple]:
    """Feeds a few key columns of the last row back as the cursor of the next page."""
    cursor: tuple = ("",) * key_len
    while True:
        with conn.cursor() as cur:
            cur.execute(query, (*cursor, *params, BATCH))
            rows = cur.fetchall()
        if not rows:
            return
        yield from rows
        if len(rows) < BATCH:
            return
        cursor = tuple(rows[-1][:key_len])


def youtube_comments(conn: psycopg.Connection, schema: str, since: date | None = None) -> Iterator[Document]:
    query = COMMENTS.format(schema=pgsql.Identifier(schema))
    for _video_id, comment_id, text in _keyset(conn, query, (since, since), key_len=2):
        yield Document(f"{YOUTUBE_COMMENT}:{comment_id}", YOUTUBE_COMMENT, text)


def youtube_videos(conn: psycopg.Connection, schema: str, since: date | None = None) -> Iterator[Document]:
    query = VIDEOS.format(schema=pgsql.Identifier(schema))
    for video_id, title in _keyset(conn, query, (since, since), key_len=1):
        yield Document(f"{YOUTUBE_VIDEO}:{video_id}", YOUTUBE_VIDEO, title)


def youtube_transcripts(
    conn: psycopg.Connection, schema: str, since: date | None = None
) -> Iterator[Document]:
    # A transcript has no time of its own. since is measured on the video, so it is not applied here.
    query = TRANSCRIPTS.format(schema=pgsql.Identifier(schema))
    for video_id, full_text in _keyset(conn, query, (), key_len=1):
        yield Document(f"{YOUTUBE_TRANSCRIPT}:{video_id}", YOUTUBE_TRANSCRIPT, full_text)


def review_doc_id(source: str, review_key: str) -> str:
    """The `doc_id` of one review. Kept in this one place so that whoever traces a chunk back to its source
    row (the cross-check of fork #7) does not write the same grammar twice -- written in two places, the join
    goes quietly to 0 rows the day only one of them changes."""
    return f"{COMMERCE_REVIEW}:{source}:{review_key}"


def commerce_reviews(conn: psycopg.Connection, schema: str, since: date | None = None) -> Iterator[Document]:
    query = REVIEWS.format(schema=pgsql.Identifier(schema))
    for source, review_key, body in _keyset(conn, query, (since, since), key_len=2):
        yield Document(review_doc_id(source, review_key), COMMERCE_REVIEW, body or "")


def documents(
    conn: psycopg.Connection,
    *,
    youtube_schema: str = "tubedepth",
    commerce_schema: str = "trend_radar",
    since: date | None = None,
    sources: tuple[str, ...] = SOURCES,
) -> Iterator[Document]:
    """The four sources as one stream. Sources are not chosen by hard-coding, so another source is one line
    here."""
    if YOUTUBE_COMMENT in sources:
        yield from youtube_comments(conn, youtube_schema, since)
    if YOUTUBE_VIDEO in sources:
        yield from youtube_videos(conn, youtube_schema, since)
    if YOUTUBE_TRANSCRIPT in sources:
        yield from youtube_transcripts(conn, youtube_schema, since)
    if COMMERCE_REVIEW in sources:
        yield from commerce_reviews(conn, commerce_schema, since)
