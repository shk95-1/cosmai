"""임베딩 적재의 배관. 모델은 부르지 않는다 -- 테스트는 오프라인이고 torch 는 이미지 밖이다.
가짜 인코더를 끼워 "안 태운 청크만 고르는가 · 프리픽스가 붙는가 · 행에 설정이 적히는가"를 잰다."""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import embed, vectors

pytestmark = pytest.mark.postgres


class FakeEncoder:
    """encode() 만 있으면 된다. 텍스트마다 다른 축을 세워 순서를 확인할 수 있게 한다."""

    def __init__(self):
        self.seen: list[str] = []

    def encode(self, texts, **_kw):
        self.seen.extend(texts)
        out = []
        for i, _text in enumerate(texts):
            vector = [0.0] * vectors.DIM
            vector[i % vectors.DIM] = 1.0
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
        for doc in ("d1", "d2"):
            cur.execute(
                "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
                "VALUES (%s, %s, 'youtube_comment', 0, %s, 'x')",
                (f"{doc}#0", doc, f"본문 {doc}"),
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


def test_a_vector_of_the_wrong_width_is_refused():
    with pytest.raises(ValueError):
        embed.to_literal([0.0, 1.0])


def test_a_literal_round_trips_through_pgvector(conn, fake):
    embed.run(conn)
    with conn.cursor() as cur:
        # public 한정이 필요하다: needs_runtime 의 search_path 는 `needs, pg_catalog` 라
        # 확장이 public 에 설치한 함수·연산자가 잡히지 않는다 (db/bootstrap.sql 의 주석).
        cur.execute("SELECT public.vector_dims(embedding) FROM retrieval_embedding LIMIT 1")
        row = cur.fetchone()
    assert row is not None and row[0] == vectors.DIM


def test_documents_get_the_passage_prefix(conn, fake):
    # 프리픽스가 빠지면 오류 없이 성능만 떨어진다. 그래서 여기서 세운다.
    embed.run(conn)
    assert fake.seen
    assert all(text.startswith(vectors.DOC_PREFIX) for text in fake.seen)


def test_the_row_records_what_made_the_vector(conn, fake):
    outcome = embed.run(conn)
    assert outcome.encoded == 2
    assert outcome.revision == "revsha"
    with conn.cursor() as cur:
        cur.execute("SELECT model, revision, doc_prefix, l2_normalized FROM retrieval_embedding LIMIT 1")
        row = cur.fetchone()
    assert row == (vectors.MODEL, "revsha", vectors.DOC_PREFIX, True)


def test_a_second_run_encodes_nothing(conn, fake):
    embed.run(conn)
    again = embed.run(conn)
    assert again.encoded == 0


def test_a_new_revision_re_encodes_everything(conn, fake, monkeypatch):
    embed.run(conn)
    monkeypatch.setattr(embed, "model_revision", lambda model: "newsha")
    again = embed.run(conn)
    assert again.encoded == 2
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT revision FROM retrieval_embedding")
        assert [r[0] for r in cur.fetchall()] == ["newsha"]


def test_a_chunk_added_later_is_the_only_one_re_encoded(conn, fake):
    embed.run(conn)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
            "VALUES ('d3#0', 'd3', 'youtube_comment', 0, '본문 d3', 'x')"
        )
    conn.commit()
    assert embed.run(conn).encoded == 1
