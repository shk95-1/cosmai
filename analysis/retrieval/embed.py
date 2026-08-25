"""청크를 벡터로 만든다 (slices/ydc/encode_chunks.py). **한 모델·한 설정으로 한 번에 돌린다.**

나눠서 인코딩하면 모델 리비전 · 프리픽스 · L2 정규화 · dtype · 텍스트 정규화 · 입력 필드
**여섯 개가 하나만 어긋나도** 벡터를 합칠 수 없다. 그런데 어긋나도 **오류가 안 난다** --
코사인 유사도는 숫자가 나오고 순위만 조용히 엉뚱해진다. 그래서 설정을 전부 매니페스트에 적고
읽을 때 대조한다(vectors.load).

증분이 아니라 **전량이다.** 청크가 늘면 처음부터 다시 태운다 -- 파일 저장이라 일부만 덧붙이면
행렬과 id 의 순서 대응을 손으로 지켜야 하고, 그 대응이 깨져도 오류가 안 난다. 벡터를 DB 로
옮기는 날 증분이 의미를 갖는다.

성분·식약처 텍스트는 인코딩하지 않는다. `에칠헥실트리아존` 을 벡터에 넣으면
`에칠헥실메톡시신나메이트` 도 비슷하다고 나오는데, 성분이 다른데 비슷하다고 하면 그건 순위
문제가 아니라 오답이다. 그쪽은 BM25 가 맡는다.

무거운 의존(sentence-transformers · torch)은 `embed` extra 에만 있고 이미지에는 넣지 않는다 --
인코딩은 GPU 가 있는 호스트에서 사람이 돌리는 일이지 크론이 하는 일이 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psycopg

from analysis.retrieval.vectors import (
    DEFAULT_STORE,
    DIM,
    DOC_PREFIX,
    MODEL,
    QUERY_PREFIX,
    load,
    save,
)

BATCH = 256
# 인코딩 대상. 성분 계열이 소스로 붙는 날 여기 한 줄로 제외한다.
ENCODED_SOURCES = ("youtube_comment", "youtube_video", "youtube_transcript", "commerce_review")


@dataclass(frozen=True)
class EmbedOutcome:
    model: str
    revision: str
    encoded: int
    out: Path

    @property
    def note(self) -> str:
        return f"{self.model}@{self.revision[:12]} · 청크 {self.encoded:,}개 -> {self.out}.npy"


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
        from sentence_transformers import (  # pyright: ignore[reportMissingImports]
            SentenceTransformer,
        )
    except ImportError as missing:  # pragma: no cover - 무거운 의존이라 테스트에서 부르지 않는다
        # `uv sync --extra embed` 라고 하면 안 된다 -- 그렇게 깔아도 다음 `tool/checks/test` 가
        # `--extra dev --extra retrieval --frozen` 으로 동기화하며 도로 지운다(그게 맞는 동작이다:
        # 테스트는 이미지가 싣는 집합에서 돌아야 한다). 실행마다 extra 를 말하는 쪽이 지속된다.
        raise RuntimeError(
            "sentence-transformers 가 없다. "
            "`uv run --extra retrieval --extra embed cosmai retrieval embed …` 로 실행한다 "
            "-- `uv sync --extra embed` 로 깔아 두면 다음 tool/checks/test 가 지운다."
        ) from missing
    return SentenceTransformer(model, device=device)


def chunks_to_encode(
    conn: psycopg.Connection, sources: tuple[str, ...] = ENCODED_SOURCES
) -> list[tuple[str, str, str]]:
    """(chunk_id, source, text). chunk_id 순으로 -- 다시 태워도 행 순서가 같아야 대조가 된다.

    한 번에 다 읽고 **커밋한 뒤** 인코딩한다. 스트리밍하면 트랜잭션이 인코딩 내내 열려 있고,
    needs_runtime 은 15초만 놀아도 연결을 끊는다(load_index 가 같은 이유로 같은 모양이다).
    GPU 를 다른 작업과 나눠 쓰면 배치 하나가 그 15초를 넘기므로 스트리밍은 안전하지 않다.
    38만 행 x 중앙 127자면 수십 MB 라 메모리에 들고 있어도 된다.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_id, source, text FROM retrieval_chunk WHERE source = ANY(%s) ORDER BY chunk_id",
            (list(sources),),
        )
        rows = cur.fetchall()
    conn.commit()
    return rows


def run(
    conn: psycopg.Connection,
    *,
    out: Path = DEFAULT_STORE,
    model: str = MODEL,
    device: str | None = None,
    batch: int = BATCH,
    sources: tuple[str, ...] = ENCODED_SOURCES,
) -> EmbedOutcome:
    """청크를 전량 태워 한 벌로 저장한다."""
    import numpy as np

    revision = model_revision(model)
    encoder = load_encoder(model, device)

    rows: list[tuple[str, str]] = []
    blocks: list = []
    pack: list[str] = []

    def flush() -> None:
        if not pack:
            return
        # e5 계열은 문서에 `passage: ` 를 붙여야 한다. 안 붙이면 에러 없이 성능만 떨어진다.
        blocks.append(
            np.asarray(
                encoder.encode(
                    [DOC_PREFIX + text for text in pack],
                    batch_size=batch,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                ),
                dtype="float32",
            )
        )
        pack.clear()

    for chunk_id, source, text in chunks_to_encode(conn, sources):
        rows.append((chunk_id, source))
        pack.append(text)
        if len(pack) >= batch:
            flush()
    flush()

    matrix = np.vstack(blocks) if blocks else np.zeros((0, DIM), dtype="float32")
    if matrix.shape[1:] != (DIM,):
        raise ValueError(f"모델이 {DIM} 차원을 내지 않는다: {matrix.shape}")
    save(
        out,
        matrix,
        rows,
        {
            "model": model,
            "revision": revision,
            "doc_prefix": DOC_PREFIX,
            "query_prefix": QUERY_PREFIX,
            "l2_normalized": True,
            "dtype": "float32",
            "dim": DIM,
            "batch": batch,
            "sources": list(sources),
        },
    )
    return EmbedOutcome(model, revision, len(rows), out)


def encode_query(
    query: str,
    *,
    out: Path = DEFAULT_STORE,
    device: str | None = None,
    store=None,
    encoder=None,
) -> list[float]:
    """질의 벡터. **모델과 프리픽스는 매니페스트에서 읽는다** -- 문서를 태운 것과 짝이 안 맞으면
    순위가 조용히 틀어지고, 기본값을 여기 다시 적으면 그 순간 정본이 두 벌이 된다.

    `store` 와 `encoder` 를 넘기면 그것을 쓴다. 질의 61개를 연달아 채점하는데 매번 다시 읽으면
    1.2GB 행렬과 모델을 61번 여는 셈이다.
    """
    store = store or load(out)
    encoder = encoder or load_encoder(store.model, device)
    vector = encoder.encode([store.query_prefix + query], normalize_embeddings=True, show_progress_bar=False)[
        0
    ]
    return [float(v) for v in vector]
