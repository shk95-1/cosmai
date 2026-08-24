"""원천 -> 청크 -> needs.retrieval_chunk, 그리고 그 청크 위에 세우는 BM25 검색.

배치마다 커밋한다. needs_runtime 의 transaction_timeout 이 60초라 30만 행을 한 트랜잭션에
담으면 끝까지 가지 못한다 -- analysis/aggregate·polarity 가 같은 이유로 같은 모양이다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date

import psycopg

from analysis.retrieval import corpus
from analysis.retrieval.bm25 import Index
from analysis.retrieval.chunks import check_rows, split_text
from analysis.retrieval.normalize import normalize_text

WRITE_BATCH = 1000

UPSERT = """
INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5)
VALUES (%(chunk_id)s, %(doc_id)s, %(source)s, %(ordinal)s, %(text)s, %(text_md5)s)
ON CONFLICT (chunk_id) DO UPDATE SET
  text = EXCLUDED.text, text_md5 = EXCLUDED.text_md5, chunked_at = now()
WHERE retrieval_chunk.text_md5 IS DISTINCT FROM EXCLUDED.text_md5
"""

# 청크를 지우고 다시 넣지 않는 이유: 원천이 줄어드는 일은 정상이 아니고(수집기는 추가만 한다),
# 지웠다 넣으면 그 사이 검색이 빈다. 다시 돌리면 바뀐 조각만 UPDATE 된다.


@dataclass(frozen=True)
class ChunkOutcome:
    documents: int
    chunks: int
    written: int
    problems: list[str]

    @property
    def note(self) -> str:
        head = f"문서 {self.documents:,} -> 청크 {self.chunks:,} (변경 {self.written:,})"
        return head if not self.problems else f"{head}; 계약 위반 {len(self.problems)}종"


def chunk_rows(documents: Iterable[corpus.Document]) -> Iterator[dict]:
    """문서 하나를 0개 이상의 청크 행으로. 빈 본문은 색인에 넣지 않는다."""
    for document in documents:
        pieces = split_text(normalize_text(document.text))
        for ordinal, piece in enumerate(pieces):
            yield {
                "chunk_id": f"{document.doc_id}#{ordinal}",
                "doc_id": document.doc_id,
                "source": document.source,
                "ordinal": ordinal,
                "text": piece,
                "text_md5": hashlib.md5(piece.encode()).hexdigest(),
            }


def run(
    conn: psycopg.Connection,
    *,
    youtube_schema: str = "tubedepth",
    commerce_schema: str = "trend_radar",
    since: date | None = None,
    sources: tuple[str, ...] = corpus.SOURCES,
) -> ChunkOutcome:
    """원천을 훑어 청크를 적재한다. 계약 위반은 세어서 돌려주되 적재를 막지는 않는다 --
    한 소스의 결함으로 나머지 세 소스의 색인이 통째로 비는 편이 더 나쁘다."""
    documents = corpus.documents(
        conn,
        youtube_schema=youtube_schema,
        commerce_schema=commerce_schema,
        since=since,
        sources=sources,
    )
    seen_docs: set[str] = set()
    total = written = 0
    batch: list[dict] = []
    problems: list[str] = []

    def flush() -> None:
        nonlocal written
        if not batch:
            return
        with conn.cursor() as cur:
            cur.executemany(UPSERT, batch)
            written += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
        batch.clear()

    for row in chunk_rows(documents):
        seen_docs.add(row["doc_id"])
        total += 1
        batch.append(row)
        if len(batch) >= WRITE_BATCH:
            # 검증은 배치 단위다. 30만 행을 리스트로 물리면 메모리에 두 벌이 된다.
            found, *_ = check_rows(batch)
            problems.extend(p for p in found if p not in problems)
            flush()
    found, *_ = check_rows(batch)
    problems.extend(p for p in found if p not in problems)
    flush()
    return ChunkOutcome(len(seen_docs), total, written, problems)


def load_index(
    conn: psycopg.Connection, sources: tuple[str, ...] | None = None
) -> tuple[Index, dict[str, str]]:
    """(색인, chunk_id -> source). 청크 단위로 색인한다 -- 문서 단위로 합치면 500자 제한이
    무의미해지고, 평가가 문서 단위를 원할 때는 `#ordinal` 을 떼어 접는다."""
    where, params = "", ()
    if sources:
        where, params = "WHERE source = ANY(%s)", (list(sources),)
    with conn.cursor() as cur:
        cur.execute(f"SELECT chunk_id, source, text FROM retrieval_chunk {where} ORDER BY chunk_id", params)
        rows = cur.fetchall()
    ids = [r[0] for r in rows]
    return Index(ids, [r[2] for r in rows]), {r[0]: r[1] for r in rows}


def search(
    conn: psycopg.Connection, query: str, *, top: int = 10, sources: tuple[str, ...] | None = None
) -> list[tuple[str, float, str]]:
    """(chunk_id, 점수, 본문). 색인을 매번 세운다 -- 캐시는 실측 후에 붙인다."""
    index, _ = load_index(conn, sources)
    hits = index.search(query, k=top)
    if not hits:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_id, text FROM retrieval_chunk WHERE chunk_id = ANY(%s)",
            ([chunk_id for chunk_id, _ in hits],),
        )
        texts = dict(cur.fetchall())
    return [(chunk_id, score, texts.get(chunk_id, "")) for chunk_id, score in hits]
