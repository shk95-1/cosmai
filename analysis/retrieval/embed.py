"""청크를 벡터로 만든다 (slices/ydc/encode_chunks.py). **한 모델·한 설정으로 한 번에 돌린다.**

나눠서 인코딩하면 모델 리비전 · 프리픽스 · L2 정규화 · dtype · 텍스트 정규화 · 입력 필드
**여섯 개가 하나만 어긋나도** 벡터를 합칠 수 없다. 그런데 어긋나도 **오류가 안 난다** --
코사인 유사도는 숫자가 나오고 순위만 조용히 엉뚱해진다. ydc 는 그것을 매니페스트 파일로 막았고,
여기서는 행마다 model · revision · doc_prefix · l2_normalized 를 같이 적어 막는다.

성분·식약처 텍스트는 인코딩하지 않는다. `에칠헥실트리아존` 을 벡터에 넣으면
`에칠헥실메톡시신나메이트` 도 비슷하다고 나오는데, 성분이 다른데 비슷하다고 하면 그건 순위
문제가 아니라 오답이다. 그쪽은 BM25 가 맡는다.

무거운 의존(sentence-transformers · torch)은 `embed` extra 에만 있고 이미지에는 넣지 않는다 --
인코딩은 GPU 가 있는 호스트에서 사람이 돌리는 일이지 크론이 하는 일이 아니다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import psycopg

from analysis.retrieval.vectors import DIM, DOC_PREFIX, MODEL, QUERY_PREFIX, require_extension

BATCH = 256
READ_BATCH = 2000


@dataclass(frozen=True)
class EmbedOutcome:
    model: str
    revision: str
    encoded: int

    @property
    def note(self) -> str:
        return f"{self.model}@{self.revision[:12]} · 청크 {self.encoded:,}개 인코딩"


def model_revision(model: str) -> str:
    """HF 커밋 sha. 못 읽으면 'unknown' -- 그것도 사실이고, 모르는 것을 아는 척하지 않는다."""
    try:
        from huggingface_hub import model_info  # pyright: ignore[reportMissingImports]

        return model_info(model).sha or "unknown"
    except Exception:
        return "unknown"


def load_encoder(model: str = MODEL, device: str | None = None):
    """sentence-transformers 를 여기서만 부른다. 없으면 무엇을 깔아야 하는지 말한다."""
    try:
        from sentence_transformers import SentenceTransformer  # pyright: ignore[reportMissingImports]
    except ImportError as missing:  # pragma: no cover - 무거운 의존이라 테스트에서 부르지 않는다
        raise RuntimeError("uv sync --extra embed 로 sentence-transformers 를 깔아야 한다") from missing
    return SentenceTransformer(model, device=device)


def pending(conn: psycopg.Connection, model: str, revision: str) -> Iterator[tuple[str, str]]:
    """아직 이 모델·리비전으로 안 태운 청크. 성분 소스는 애초에 대상이 아니다."""
    with conn.cursor(name="retrieval_pending") as cur:
        cur.itersize = READ_BATCH
        cur.execute(
            "SELECT c.chunk_id, c.text FROM retrieval_chunk c "
            "LEFT JOIN retrieval_embedding e ON e.chunk_id = c.chunk_id "
            "  AND e.model = %s AND e.revision = %s "
            "WHERE e.chunk_id IS NULL ORDER BY c.chunk_id",
            (model, revision),
        )
        yield from cur


def to_literal(vector) -> str:
    """pgvector 의 텍스트 표기. psycopg 에 어댑터를 등록하지 않고 캐스트로 넘긴다."""
    values = list(vector)
    if len(values) != DIM:
        raise ValueError(f"벡터가 {DIM} 차원이 아니다: {len(values)}")
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


UPSERT = """
INSERT INTO retrieval_embedding
  (chunk_id, model, revision, doc_prefix, l2_normalized, embedding)
VALUES (%s, %s, %s, %s, true, %s::public.vector)
ON CONFLICT (chunk_id) DO UPDATE SET
  model = EXCLUDED.model, revision = EXCLUDED.revision, doc_prefix = EXCLUDED.doc_prefix,
  l2_normalized = EXCLUDED.l2_normalized, embedding = EXCLUDED.embedding, embedded_at = now()
"""


def run(
    conn: psycopg.Connection, *, model: str = MODEL, device: str | None = None, batch: int = BATCH
) -> EmbedOutcome:
    """안 태운 청크만 태운다. 배치마다 커밋한다 -- 30만 청크를 한 트랜잭션에 담을 수 없다."""
    require_extension(conn)
    revision = model_revision(model)
    encoder = load_encoder(model, device)

    encoded = 0
    pack: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal encoded
        if not pack:
            return
        # e5 계열은 문서에 `passage: ` 를 붙여야 한다. 안 붙이면 에러 없이 성능만 떨어진다.
        matrix = encoder.encode(
            [DOC_PREFIX + text for _, text in pack],
            batch_size=batch,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        with conn.cursor() as cur:
            cur.executemany(
                UPSERT,
                [
                    (chunk_id, model, revision, DOC_PREFIX, to_literal(row))
                    for (chunk_id, _), row in zip(pack, matrix, strict=True)
                ],
            )
        conn.commit()
        encoded += len(pack)
        pack.clear()

    for chunk_id, text in pending(conn, model, revision):
        pack.append((chunk_id, text))
        if len(pack) >= batch:
            flush()
    flush()
    return EmbedOutcome(model, revision, encoded)


def encode_query(query: str, *, model: str = MODEL, device: str | None = None) -> list[float]:
    """질의에는 `query: ` 를 붙인다. 문서 프리픽스와 짝이 안 맞으면 순위가 조용히 틀어진다."""
    encoder = load_encoder(model, device)
    vector = encoder.encode([QUERY_PREFIX + query], normalize_embeddings=True, show_progress_bar=False)[0]
    return [float(v) for v in vector]
