"""The plumbing that bakes chunks into file vectors. The model is not called -- torch is outside the image
and the tests are offline. A fake encoder is put in and it measures "is the prefix attached · is the row
order chunk_id order · are the settings left in the manifest". All three raise no error when out of step."""

from __future__ import annotations

import json

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import embed, vectors

pytestmark = pytest.mark.postgres


class FakeEncoder:
    """Only encode() is needed. It builds a different axis per call so the row order can be checked."""

    def __init__(self):
        self.seen: list[str] = []

    def encode(self, texts, **_kw):
        out = []
        for text in texts:
            vector = [0.0] * vectors.DIM
            vector[len(self.seen) % vectors.DIM] = 1.0
            self.seen.append(text)
            out.append(vector)
        return out


def _connect(url: str) -> psycopg.Connection:
    parsed = make_url(url)
    return psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )


@pytest.fixture
def conn(needs_schema: str, needs_runtime_url: str):
    connection = _connect(needs_runtime_url)
    with connection.cursor() as cur:
        # Inserted in reverse chunk_id order on purpose -- the stored order must not follow the insert order.
        for doc, source in (("d2", "commerce_review"), ("d1", "youtube_comment")):
            cur.execute(
                "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
                "VALUES (%s, %s, %s, 0, %s, 'x')",
                (f"{doc}#0", doc, source, f"본문 {doc}"),
            )
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def fake(monkeypatch):
    encoder = FakeEncoder()
    monkeypatch.setattr(embed, "load_encoder", lambda *a, **k: encoder)
    monkeypatch.setattr(embed, "model_revision", lambda model: "revsha")
    return encoder


def test_a_run_writes_a_store_that_loads_back(conn, fake, tmp_path):
    outcome = embed.run(conn, out=tmp_path / "e5base")
    assert outcome.encoded == 2
    assert outcome.revision == "revsha"
    store = vectors.load(tmp_path / "e5base")
    assert len(store.chunk_ids) == 2
    assert store.matrix.shape == (2, vectors.DIM)  # pyright: ignore[reportAttributeAccessIssue]


def test_rows_are_ordered_by_chunk_id(conn, fake, tmp_path):
    # A rebake has to give the same order for the two sets to be compared.
    embed.run(conn, out=tmp_path / "e5base")
    assert vectors.load(tmp_path / "e5base").chunk_ids == ["d1#0", "d2#0"]


def test_documents_get_the_passage_prefix(conn, fake, tmp_path):
    # Without the prefix the performance just drops, with no error. So it is pinned down here.
    embed.run(conn, out=tmp_path / "e5base")
    assert fake.seen
    assert all(text.startswith(vectors.DOC_PREFIX) for text in fake.seen)


def test_the_manifest_records_what_made_the_vectors(conn, fake, tmp_path):
    embed.run(conn, out=tmp_path / "e5base")
    _, _, manifest_path = vectors.paths(tmp_path / "e5base")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["model"] == vectors.MODEL
    assert manifest["revision"] == "revsha"
    assert manifest["doc_prefix"] == vectors.DOC_PREFIX
    assert manifest["query_prefix"] == vectors.QUERY_PREFIX
    assert manifest["l2_normalized"] is True
    assert manifest["dim"] == vectors.DIM
    assert manifest["count"] == 2


def test_the_source_of_each_row_travels_with_it(conn, fake, tmp_path):
    # Without the sources, a vector search narrowed by --source has to read the DB once more.
    embed.run(conn, out=tmp_path / "e5base")
    assert vectors.load(tmp_path / "e5base").sources == ["youtube_comment", "commerce_review"]


def test_a_source_left_out_is_not_encoded(conn, fake, tmp_path):
    embed.run(conn, out=tmp_path / "e5base", sources=("youtube_comment",))
    assert vectors.load(tmp_path / "e5base").chunk_ids == ["d1#0"]


def test_an_empty_corpus_still_writes_a_readable_store(needs_schema, needs_runtime_url, fake, tmp_path):
    connection = _connect(needs_runtime_url)
    try:
        outcome = embed.run(connection, out=tmp_path / "e5base")
    finally:
        connection.close()
    assert outcome.encoded == 0
    assert vectors.load(tmp_path / "e5base").chunk_ids == []


def test_the_manifest_records_how_far_the_chunks_had_been_rebuilt(conn, fake, tmp_path):
    """With only `count` written down, nobody can catch "same number, different set" -- it is the value the
    coverage guard reads, so it is taken straight from the encoded rows (#12)."""
    embed.run(conn, out=tmp_path / "e5base")
    _, _, manifest_path = vectors.paths(tmp_path / "e5base")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with conn.cursor() as cur:
        cur.execute("SELECT max(chunked_at) FROM retrieval_chunk")
        latest = cur.fetchone()[0]  # pyright: ignore[reportOptionalSubscript]
    conn.commit()
    assert manifest["chunked_at_max"] == latest.isoformat()


def test_an_empty_corpus_records_no_chunked_at_max(needs_schema, needs_runtime_url, fake, tmp_path):
    # An empty corpus has no maximum. Writing a value that pretends to know makes the guard falsely
    # reassuring.
    connection = _connect(needs_runtime_url)
    try:
        embed.run(connection, out=tmp_path / "e5base")
    finally:
        connection.close()
    _, _, manifest_path = vectors.paths(tmp_path / "e5base")
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["chunked_at_max"] is None
