"""청크를 파일 벡터로 굽는 배관. 모델은 부르지 않는다 -- torch 는 이미지 밖이고 테스트는
오프라인이다. 가짜 인코더를 끼워 "프리픽스가 붙는가 · 행 순서가 chunk_id 순인가 ·
매니페스트에 설정이 남는가"를 잰다. 셋 다 어긋나도 오류가 안 나는 것들이다."""

from __future__ import annotations

import json

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import embed, vectors

pytestmark = pytest.mark.postgres


class FakeEncoder:
    """encode() 만 있으면 된다. 호출마다 다른 축을 세워 행 순서를 확인할 수 있게 한다."""

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
        # 일부러 chunk_id 역순으로 넣는다 -- 저장 순서가 삽입 순서를 따르면 안 된다.
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
    # 다시 태울 때 같은 순서가 나와야 두 벌을 대조할 수 있다.
    embed.run(conn, out=tmp_path / "e5base")
    assert vectors.load(tmp_path / "e5base").chunk_ids == ["d1#0", "d2#0"]


def test_documents_get_the_passage_prefix(conn, fake, tmp_path):
    # 프리픽스가 빠지면 오류 없이 성능만 떨어진다. 그래서 여기서 세운다.
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
    # 소스가 없으면 --source 로 좁히는 벡터 검색이 DB 를 한 번 더 읽어야 한다.
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
