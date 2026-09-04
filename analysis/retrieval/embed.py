"""Turns chunks into vectors (ydc encode_chunks.py, v0.1.0 02440ab). **One model, one setting, in one run.**

Encoding in pieces means model revision · prefix · L2 normalization · dtype · text normalization · input
field -- **one of the six out of step** and the vectors cannot be merged. And being out of step raises **no
error**: cosine similarity still gives a number and only the ranking goes quietly wrong. So every setting is
written into the manifest and checked against on read (vectors.load).

It is **the whole set**, not an increment. When chunks grow it is burned again from the start -- with file
storage, replacing only part of it means keeping the order correspondence of matrix and ids by hand, and a
broken correspondence raises no error. Incremental gets a meaning the day the vectors move into the DB.

성분·식약처 텍스트는 인코딩하지 않는다. `에칠헥실트리아존` 을 벡터에 넣으면
`에칠헥실메톡시신나메이트` 도 비슷하다고 나오는데, 성분이 다른데 비슷하다고 하면 그건 순위
문제가 아니라 오답이다. 그쪽은 BM25 가 맡는다.

The heavy dependencies (sentence-transformers · torch) are in the `embed` extra only and are not in the image
-- encoding is something a person runs on a host with a GPU, not something cron does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
# What gets encoded. The MFDS ledger (`mfds`, fork #77) is the source that line was waiting for: a report
# number and a filed item name are letters, and a nearest neighbour of one filing is a different filing --
# an answer, not a ranking, that is wrong. BM25 carries it and the vector store never sees it.
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
    """The HF commit sha. 'unknown' when it cannot be read -- that is a fact too, and what is unknown is not
    pretended to be known."""
    try:
        from huggingface_hub import model_info  # pyright: ignore[reportMissingImports]

        return model_info(model).sha or "unknown"
    except Exception:
        return "unknown"


def load_encoder(model: str = MODEL, device: str | None = None):
    """sentence-transformers is called only here. When it is missing, it says what to install."""
    try:
        from sentence_transformers import (  # pyright: ignore[reportMissingImports]
            SentenceTransformer,
        )
    except ImportError as missing:  # pragma: no cover - a heavy dependency, not called from the tests
        # It must not say `uv sync --extra embed` -- installed that way, the next `tool/checks/test` syncs
        # with `--extra dev --extra retrieval --frozen` and removes it again (which is the right behaviour:
        # the tests have to run on the set the image carries). Naming the extra per run is what lasts.
        raise RuntimeError(
            "sentence-transformers 가 없다. "
            "`uv run --extra retrieval --extra embed cosmai retrieval embed …` 로 실행한다 "
            "-- `uv sync --extra embed` 로 깔아 두면 다음 tool/checks/test 가 지운다."
        ) from missing
    return SentenceTransformer(model, device=device)


def chunks_to_encode(
    conn: psycopg.Connection, sources: tuple[str, ...] = ENCODED_SOURCES
) -> list[tuple[str, str, str, datetime]]:
    """(chunk_id, source, text, chunked_at). In chunk_id order -- a rebake has to keep the row order for the
    comparison to hold.

    `chunked_at` is read along with it so the manifest's `chunked_at_max` can be taken **from the encoded
    rows**. Asking `max(chunked_at)` separately would write down a chunk that arrived in between and is not
    in the store, and then the coverage guard covers the mismatch (pipeline.coverage_note).

    Everything is read at once and encoded **after a commit**. Streaming holds the transaction open through
    the whole encoding, and needs_runtime cuts a connection after only 15 idle seconds (load_index takes the
    same shape for the same reason). Sharing the GPU with another job puts a single batch past those 15
    seconds, so streaming is not safe. 380k rows x a median of 127 characters is tens of MB, so it can be
    held in memory.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_id, source, text, chunked_at FROM retrieval_chunk "
            "WHERE source = ANY(%s) ORDER BY chunk_id",
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
    """Burns the whole set of chunks and stores it as one set."""
    import numpy as np

    revision = model_revision(model)
    encoder = load_encoder(model, device)

    rows: list[tuple[str, str]] = []
    blocks: list = []
    pack: list[str] = []
    latest: datetime | None = None

    def flush() -> None:
        if not pack:
            return
        # The e5 family needs `passage: ` on a document. Without it the performance just drops, with no error.
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

    for chunk_id, source, text, chunked_at in chunks_to_encode(conn, sources):
        rows.append((chunk_id, source))
        pack.append(text)
        latest = chunked_at if latest is None else max(latest, chunked_at)
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
            # A count alone cannot catch "same number, different set". An empty corpus has no maximum, so
            # None is written -- pretending to know the unknown makes the guard falsely reassuring.
            "chunked_at_max": latest.isoformat() if latest is not None else None,
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
    """The query vector. **The model and the prefix are read from the manifest** -- out of step with what
    burned the documents, the ranking goes quietly wrong, and writing the defaults again here makes two
    canonical copies at that moment.

    Passing `store` and `encoder` uses those. Scoring 63 queries in a row while reading them each time means
    opening a 1.2GB matrix and the model 61 times over.
    """
    store = store or load(out)
    encoder = encoder or load_encoder(store.model, device)
    vector = encoder.encode([store.query_prefix + query], normalize_embeddings=True, show_progress_bar=False)[
        0
    ]
    return [float(v) for v in vector]
