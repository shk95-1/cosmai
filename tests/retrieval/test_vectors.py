"""pgvector 저장과 RRF. 실제 모델은 부르지 않는다 -- 테스트는 오프라인이고, 여기서 재는 것은
"벡터가 들어가고 코사인 순서로 나오는가" 와 "확장이 없으면 조용히 넘어가지 않는가" 다."""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import vectors

pytestmark = pytest.mark.postgres


def test_rrf_prefers_what_both_rankings_agree_on():
    # 양쪽에서 2위인 b 가, 한쪽에서만 1위이고 다른 쪽에는 없는 a 를 이겨야 융합이 의미가 있다.
    fused = vectors.rrf(["a", "b", "c"], ["x", "b", "y"])
    assert fused[0] == "b"
    assert fused.index("b") < fused.index("a")


def test_rrf_is_deterministic_on_ties():
    # dict 순서에 기대면 같은 입력이 실행마다 다른 답을 준다.
    assert vectors.rrf(["a", "b"], ["a", "b"]) == vectors.rrf(["a", "b"], ["a", "b"])
    assert vectors.rrf(["a"], ["b"]) == ["a", "b"]


def test_a_query_vector_of_the_wrong_width_is_refused(needs_runtime_url):
    conn = _connect(needs_runtime_url)
    try:
        with pytest.raises(ValueError):
            vectors.search(conn, [0.0, 1.0])
    finally:
        conn.close()


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


def _unit(index: int) -> list[float]:
    """dim 축 하나만 1 인 단위 벡터. 코사인 거리가 축 사이에서 정확히 1 이 된다."""
    vector = [0.0] * vectors.DIM
    vector[index] = 1.0
    return vector


@pytest.fixture
def loaded(needs_schema: str, needs_runtime_url: str):
    conn = _connect(needs_runtime_url)
    with conn.cursor() as cur:
        for i, (doc, source) in enumerate(
            [("d1", "youtube_comment"), ("d2", "commerce_review"), ("d3", "youtube_comment")]
        ):
            cur.execute(
                "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
                "VALUES (%s, %s, %s, 0, '본문', 'x')",
                (f"{doc}#0", doc, source),
            )
            cur.execute(
                "INSERT INTO retrieval_embedding (chunk_id, model, revision, doc_prefix, "
                "l2_normalized, embedding) VALUES (%s, %s, 'rev', %s, true, %s::public.vector)",
                (
                    f"{doc}#0",
                    vectors.MODEL,
                    vectors.DOC_PREFIX,
                    "[" + ",".join(repr(v) for v in _unit(i)) + "]",
                ),
            )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def test_the_extension_is_present_in_the_test_harness(loaded):
    # 없으면 021 DDL 자체가 서지 않았을 것이다. 그 경우를 조용히 넘기지 않는다.
    vectors.require_extension(loaded)


def test_search_returns_the_nearest_chunk_first(loaded):
    hits = vectors.search(loaded, _unit(1), top=3)
    assert hits[0][0] == "d2#0"
    assert hits[0][1] == pytest.approx(0.0, abs=1e-6)
    # 직교하는 축은 코사인 거리 1 이다.
    assert all(h[1] == pytest.approx(1.0, abs=1e-6) for h in hits[1:])


def test_search_can_be_narrowed_to_one_source(loaded):
    hits = vectors.search(loaded, _unit(1), top=5, sources=("youtube_comment",))
    assert {h[0] for h in hits} == {"d1#0", "d3#0"}


def test_dropping_a_chunk_takes_its_embedding_with_it(loaded):
    # 청크가 사라졌는데 벡터가 남으면 검색이 없는 본문을 가리킨다.
    with loaded.cursor() as cur:
        cur.execute("DELETE FROM retrieval_chunk WHERE chunk_id = 'd2#0'")
        cur.execute("SELECT count(*) FROM retrieval_embedding WHERE chunk_id = 'd2#0'")
        row = cur.fetchone()
    assert row is not None and row[0] == 0
