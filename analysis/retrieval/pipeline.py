"""원천 -> 청크 -> needs.retrieval_chunk, 그리고 그 청크 위에 세우는 BM25 검색.

배치마다 커밋한다. needs_runtime 의 transaction_timeout 이 60초라 30만 행을 한 트랜잭션에
담으면 끝까지 가지 못한다 -- analysis/aggregate·polarity 가 같은 이유로 같은 모양이다.
"""

from __future__ import annotations

import hashlib
import os
import pickle
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

from analysis.retrieval import corpus
from analysis.retrieval.bm25 import DICTIONARIES, Index
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

    def validate_and_flush() -> None:
        found, *_ = check_rows(batch)
        problems.extend(p for p in found if p not in problems)
        flush()

    for row in chunk_rows(documents):
        # 배치는 **문서 경계에서만** 끊는다. "ordinal 이 0 부터 연속"은 문서 전체에 걸린 성질이라
        # 한 문서를 두 배치로 자르면 뒤쪽이 ordinal 5 부터 시작하는 것으로 보여 거짓 위반이 난다
        # (실측: 자막 한 편이 최대 155조각이라 자막에서만 수십 건). 30만 행을 리스트로 물리지
        # 않으려고 배치를 쓰는 것이므로, 상한을 넘긴 뒤 다음 문서가 시작될 때 끊는다.
        if batch and len(batch) >= WRITE_BATCH and row["doc_id"] != batch[-1]["doc_id"]:
            validate_and_flush()
        seen_docs.add(row["doc_id"])
        total += 1
        batch.append(row)
    validate_and_flush()
    return ChunkOutcome(len(seen_docs), total, written, problems)


CACHE_DIR = Path("var/retrieval/bm25")


def index_signature(conn: psycopg.Connection, sources: tuple[str, ...] | None) -> str:
    """이 색인이 무엇 위에 세워졌는지. 하나라도 달라지면 캐시를 다시 만들어야 한다.

    청크 수와 최신 `chunked_at` 이면 충분하다 -- UPSERT 가 본문이 바뀐 행만 `chunked_at` 을
    올리므로 내용 변화는 최댓값을 움직이고, 삭제는 개수를 움직인다. 사전이 바뀌면 같은 본문이
    다른 토큰이 되므로 사전 해시도 넣는다(ydc bm25.py 의 캐시 키와 같은 발상).
    """
    where, params = "", ()
    if sources:
        where, params = "WHERE source = ANY(%s)", (list(sources),)
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*), max(chunked_at) FROM retrieval_chunk {where}", params)  # noqa: S608
        count, latest = cur.fetchone() or (0, None)
    conn.commit()  # 뒤이어 형태소 분석이 붙으므로 트랜잭션을 열어 둔 채로 나가지 않는다
    parts = [str(count), str(latest), ",".join(sources or ())]
    for path in DICTIONARIES:
        parts.append(hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "-")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def load_index(
    conn: psycopg.Connection,
    sources: tuple[str, ...] | None = None,
    *,
    cache_dir: Path | None = CACHE_DIR,
) -> tuple[Index, dict[str, str]]:
    """(색인, chunk_id -> source). 청크 단위로 색인한다 -- 문서 단위로 합치면 500자 제한이
    무의미해지고, 평가가 문서 단위를 원할 때는 `#ordinal` 을 떼어 접는다.

    **캐시가 없으면 쓸 수 없다.** 실측(2026-08-25, 381,950청크)으로 형태소 분석이 10분을 넘겨
    `cosmai retrieval search` 한 번이 그만큼 걸렸다. 피클에 담는 것은 클래스가 아니라 `state()`
    dict 다 -- 클래스를 담으면 모듈 경로가 바뀌는 날 캐시 전체를 못 읽는다.
    """
    cached = cache_dir / f"index-{index_signature(conn, sources)}.pkl" if cache_dir else None
    if cached and cached.exists():
        state = pickle.loads(cached.read_bytes())
        return Index.from_state(state["index"]), state["origin"]

    where, params = "", ()
    if sources:
        where, params = "WHERE source = ANY(%s)", (list(sources),)
    with conn.cursor() as cur:
        cur.execute(f"SELECT chunk_id, source, text FROM retrieval_chunk {where} ORDER BY chunk_id", params)  # noqa: S608
        rows = cur.fetchall()
    # 색인을 세우는 동안(38만 청크면 10분을 넘는다) 트랜잭션이 열려 있으면 needs_runtime 의
    # idle_in_transaction_session_timeout(15초)이 연결을 끊는다. 실측으로 여기서 끊겼다.
    conn.commit()
    ids = [r[0] for r in rows]
    index = Index(ids, [r[2] for r in rows])
    origin = {r[0]: r[1] for r in rows}
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        # 임시 파일에 쓰고 옮긴다 -- 두 실행이 겹치면 반쯤 쓰인 피클을 읽게 된다.
        scratch = cached.with_suffix(f".{os.getpid()}.tmp")
        scratch.write_bytes(pickle.dumps({"index": index.state(), "origin": origin}))
        scratch.replace(cached)
    return index, origin


def ranked_chunks(
    conn: psycopg.Connection,
    query: str,
    *,
    engine: str = "bm25",
    top: int = 10,
    sources: tuple[str, ...] | None = None,
    store: Path | None = None,
    cache_dir: Path | None = CACHE_DIR,
    index: Index | None = None,
    vector_store=None,
    encoder=None,
) -> list[tuple[str, float]]:
    """(chunk_id, 점수). 세 검색기가 같은 모양으로 답한다 -- eval 이 같은 잣대로 재려면 필요하다.

    `index` · `vector_store` · `encoder` 를 넘기면 그것을 쓴다. eval 은 질의 61개를 연달아 돌리는데
    매번 다시 읽으면 96MB 피클과 1.2GB 행렬과 모델을 61번씩 여는 셈이다.

    점수의 뜻은 엔진마다 다르다(BM25 는 클수록, 벡터는 코사인 거리라 작을수록 가깝다). 그래서
    비교는 언제나 순위로 한다 -- RRF 를 쓰는 이유도 같다.
    """
    if engine == "bm25":
        lexical_index = index or load_index(conn, sources, cache_dir=cache_dir)[0]
        return lexical_index.search(query, k=top)

    from analysis.retrieval import embed, vectors

    out = store or vectors.DEFAULT_STORE
    loaded = vector_store or vectors.load(out)  # 파일이 없으면 StoreMissing 으로 여기서 멈춘다
    query_vector = embed.encode_query(query, out=out, store=loaded, encoder=encoder)
    if engine == "vector":
        return vectors.search(loaded, query_vector, top=top, sources=sources)
    if engine == "hybrid":
        lexical_index = index or load_index(conn, sources, cache_dir=cache_dir)[0]
        lexical = [c for c, _ in lexical_index.search(query, k=top * 4)]
        semantic = [c for c, _ in vectors.search(loaded, query_vector, top=top * 4, sources=sources)]
        fused = vectors.rrf(lexical, semantic)[:top]
        # 융합 결과의 점수는 순위 자체다. 두 스케일을 섞어 적으면 읽는 쪽이 오해한다.
        return [(chunk_id, float(rank)) for rank, chunk_id in enumerate(fused, 1)]
    raise ValueError(f"모르는 엔진: {engine!r}")


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    engine: str = "bm25",
    top: int = 10,
    sources: tuple[str, ...] | None = None,
    store: Path | None = None,
    cache_dir: Path | None = CACHE_DIR,
) -> list[tuple[str, float, str]]:
    """(chunk_id, 점수, 본문)."""
    hits = ranked_chunks(
        conn, query, engine=engine, top=top, sources=sources, store=store, cache_dir=cache_dir
    )
    if not hits:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_id, text FROM retrieval_chunk WHERE chunk_id = ANY(%s)",
            ([chunk_id for chunk_id, _ in hits],),
        )
        texts = dict(cur.fetchall())
    return [(chunk_id, score, texts.get(chunk_id, "")) for chunk_id, score in hits]
