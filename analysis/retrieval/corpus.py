"""원천 스키마에서 검색 대상 문서를 읽는다.

ydc 는 `common/document.csv` 한 장을 읽었다. 여기서는 그 파일을 만들지 않고 DB 를 바로 읽는다 --
수집기가 이미 같은 행을 쓰고 있으므로 CSV 를 한 벌 더 두면 어느 쪽이 정본인지 알 수 없게 된다.

페이징은 전부 키셋이다. needs_runtime 은 statement_timeout 30s · transaction_timeout 60s 라
OFFSET 페이징(전체 O(n^2))이나 한 트랜잭션 통짜 스캔은 그 벽에 부딪힌다 --
analysis/linker/pipeline.py 가 같은 이유로 같은 모양을 쓴다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

import psycopg
from psycopg import sql as pgsql

BATCH = 2000

# doc_id 의 접두사이자 청크의 source. 값이 바뀌면 이미 쌓인 청크와 새 청크가 다른 문서가 된다.
YOUTUBE_COMMENT = "youtube_comment"
YOUTUBE_VIDEO = "youtube_video"
YOUTUBE_TRANSCRIPT = "youtube_transcript"
COMMERCE_REVIEW = "commerce_review"
SOURCES = (YOUTUBE_COMMENT, YOUTUBE_VIDEO, YOUTUBE_TRANSCRIPT, COMMERCE_REVIEW)


@dataclass(frozen=True)
class Document:
    """검색 대상 문서 하나. chunks.py 가 이걸 받아 조각으로 나눈다."""

    doc_id: str
    source: str
    text: str


# video_id 를 고르는 것은 쓰려는 값이어서가 아니라 커서의 앞칸이기 때문이다 -- 댓글의 자연키가
# (video_id, comment_id) 이고 그 순서로만 인덱스를 탄다.
COMMENTS = pgsql.SQL("""
SELECT video_id, comment_id, text FROM {schema}.comments
WHERE (video_id, comment_id) > (%s, %s)
  AND (%s::date IS NULL OR published_at::date >= %s::date)
ORDER BY video_id, comment_id LIMIT %s
""")

# video_snapshots 는 같은 영상을 여러 번 담으므로 최신 것만 본다. 설명(description) 컬럼이 없어
# 제목만 쓴다 -- ydc 는 API 응답에서 제목 + 설명을 붙였다(analysis/slices/ydc/trend.py video_text).
VIDEOS = pgsql.SQL("""
SELECT DISTINCT ON (video_id) video_id, title FROM {schema}.video_snapshots
WHERE video_id > %s AND (%s::date IS NULL OR published_at::date >= %s::date)
ORDER BY video_id, fetched_at DESC LIMIT %s
""")

TRANSCRIPTS = pgsql.SQL("""
SELECT DISTINCT ON (video_id) video_id, full_text FROM {schema}.transcripts
WHERE video_id > %s ORDER BY video_id, fetched_at DESC LIMIT %s
""")

# review 의 자연키는 (source, review_key) 다. 사이트가 달라도 review_key 가 겹칠 수 있어 둘 다 넣는다.
REVIEWS = pgsql.SQL("""
SELECT source, review_key, body FROM {schema}.review
WHERE (source, review_key) > (%s, %s)
  AND (%s::date IS NULL OR written_at::date >= %s::date)
ORDER BY source, review_key LIMIT %s
""")


def _keyset(conn: psycopg.Connection, query: pgsql.Composed, params: tuple, key_len: int) -> Iterator[tuple]:
    """마지막 행의 키 몇 칸을 다음 페이지의 커서로 되먹인다."""
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
    # 자막에는 자기 시각이 없다. since 는 영상 기준이라 여기서는 걸지 않는다.
    query = TRANSCRIPTS.format(schema=pgsql.Identifier(schema))
    for video_id, full_text in _keyset(conn, query, (), key_len=1):
        yield Document(f"{YOUTUBE_TRANSCRIPT}:{video_id}", YOUTUBE_TRANSCRIPT, full_text)


def review_doc_id(source: str, review_key: str) -> str:
    """리뷰 하나의 `doc_id`. 청크를 원천 행으로 되짚는 쪽(포크 #7 의 대조)이 같은 문법을 두 번 적지
    않도록 여기 한 자리에 둔다 -- 두 자리에 적히면 한쪽만 바뀌는 날 조인이 조용히 0행이 된다."""
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
    """네 원천을 한 흐름으로. 소스를 하드코딩해 고르지 않으므로 원천이 늘면 여기 한 줄이다."""
    if YOUTUBE_COMMENT in sources:
        yield from youtube_comments(conn, youtube_schema, since)
    if YOUTUBE_VIDEO in sources:
        yield from youtube_videos(conn, youtube_schema, since)
    if YOUTUBE_TRANSCRIPT in sources:
        yield from youtube_transcripts(conn, youtube_schema, since)
    if COMMERCE_REVIEW in sources:
        yield from commerce_reviews(conn, commerce_schema, since)
