"""벡터 검색과 RRF 융합 (slices/ydc/hybrid.py 의 DB 판).

**핵심은 합치는 방법이 아니라 비교다.** 하이브리드가 BM25 단독보다 나은지 확인하지 않고 붙이면
좋아졌다고 믿을 근거가 없다. 그래서 세 검색기가 같은 모양으로 결과를 내놓고, eval 이 `--engine`
으로 골라 같은 잣대로 잰다.

RRF 를 쓰는 이유는 **점수 스케일이 다르기 때문**이다 -- BM25 는 11.83 같은 값이고 코사인은
0~1 이다. 정규화해서 더하면 그 정규화 방식이 또 하나의 손잡이가 된다. RRF 는 순위만 쓴다.
"""

from __future__ import annotations

import psycopg

RRF_K = 60  # 관행값. 우리 데이터로 다시 뽑지 않았다 -- 뽑으려면 자동 라벨로 골라야 한다
DIM = 768
MODEL = "intfloat/multilingual-e5-base"
DOC_PREFIX = "passage: "
QUERY_PREFIX = "query: "  # 안 붙이면 오류 없이 성능만 떨어진다


class ExtensionMissing(RuntimeError):
    """pgvector 가 없다. shared-postgres 가 postgres:18 로 되돌아간 경우가 이것이다."""


def require_extension(conn: psycopg.Connection) -> None:
    """벡터 경로는 여기서 먼저 막는다 -- 확장이 없으면 첫 쿼리에서 알 수 없는 타입 오류가 난다."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        if cur.fetchone() is None:
            raise ExtensionMissing(
                "pgvector 확장이 없다. shared-postgres 이미지가 pgvector/pgvector:pg18 인지 "
                "확인하고 `CREATE EXTENSION vector` 를 적용해야 한다 (#28 단계 4)."
            )


def rrf(*rankings: list[str], k: int = RRF_K) -> list[str]:
    """여러 순위를 하나로. score(d) = sum 1 / (k + rank_i(d))."""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(item, rank)
    # 동점은 처음 등장한 순위로 가른다 -- dict 순서에 기대면 실행마다 답이 흔들린다.
    return sorted(scores, key=lambda item: (-scores[item], first_seen[item], item))


def search(
    conn: psycopg.Connection,
    query_vector: list[float],
    *,
    top: int = 10,
    sources: tuple[str, ...] | None = None,
) -> list[tuple[str, float]]:
    """(chunk_id, 코사인 거리). 거리이므로 작을수록 가깝다."""
    require_extension(conn)
    if len(query_vector) != DIM:
        raise ValueError(f"질의 벡터가 {DIM} 차원이 아니다: {len(query_vector)}")
    literal = "[" + ",".join(repr(float(v)) for v in query_vector) + "]"
    # SELECT 절의 벡터 · (있으면) source 목록 · LIMIT 순으로 자리를 맞춘다.
    if sources:
        statement = (
            "SELECT e.chunk_id, e.embedding OPERATOR(public.<=>) %s::public.vector AS distance "
            "FROM retrieval_embedding e JOIN retrieval_chunk c USING (chunk_id) "
            "WHERE c.source = ANY(%s) ORDER BY distance LIMIT %s"
        )
        params: tuple = (literal, list(sources), top)
    else:
        statement = (
            "SELECT e.chunk_id, e.embedding OPERATOR(public.<=>) %s::public.vector AS distance "
            "FROM retrieval_embedding e ORDER BY distance LIMIT %s"
        )
        params = (literal, top)
    with conn.cursor() as cur:
        cur.execute(statement, params)
        return [(chunk_id, float(distance)) for chunk_id, distance in cur.fetchall()]
