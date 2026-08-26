"""literal / heldout 의 정의를 고정한다. 두 모드의 차이가 흐려지면 heldout 숫자가
"벡터가 넘어야 하는 선"이 아니게 되고, #28 단계 4의 채택 기준이 근거를 잃는다."""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import eval as retrieval_eval
from analysis.retrieval.bm25 import Index
from tests.retrieval.conftest import install_topics

pytestmark = pytest.mark.postgres


def test_a_query_is_a_topic_alias_and_excluded_topics_are_left_out():
    literal = retrieval_eval.queries("literal")
    assert literal
    topics = {topic for topic, _ in literal}
    # `선크림`·`추천_재구매` 는 판별력이 없어 판정에서 빠졌다. 평가에서도 빠져야 한다.
    assert "선크림" not in topics and "추천_재구매" not in topics
    assert ("백탁", "하얗게") in literal


def test_heldout_drops_a_topic_that_has_only_one_alias():
    # 별칭이 하나면 뺄 것이 없어 heldout 질문 자체가 성립하지 않는다.
    assert any(t == "혼합자차" for t, _ in retrieval_eval.queries("literal"))
    assert not any(t == "혼합자차" for t, _ in retrieval_eval.queries("heldout"))


def test_chunks_of_one_document_count_once():
    # 긴 문서 하나가 상위 10칸을 차지하면 P@10 이 문서 수가 아니라 조각 수를 잰다.
    ranked = retrieval_eval.to_docs(["d1#0", "d1#1", "d2#0", "d1#2", "d3#0"], k=10)
    assert ranked == ["d1", "d2", "d3"]


def test_scoring_counts_rank_positions():
    p, mrr, hit = retrieval_eval.score(["a", "b", "c"], {"b"})
    assert p == pytest.approx(1 / 3)
    assert mrr == pytest.approx(1 / 2)
    assert hit is True
    assert retrieval_eval.score([], {"b"}) == (0.0, 0.0, False)


def test_held_out_docs_are_removed_by_token_not_by_substring():
    # `하얘` 와 `하얗게` 는 글자가 안 겹치지만 Kiwi 는 같은 어간을 준다. 토큰으로 빼야 한다.
    index = Index(["d1#0", "d2#0"], ["하얘서 별로다", "발림성이 좋다"])
    assert retrieval_eval.docs_with_tokens(index, "하얗게") == {"d1"}


@pytest.fixture
def loaded(needs_schema: str, needs_runtime_url: str):
    """청크는 운영과 같은 needs_runtime 이 쓴다 -- migrator 는 needs_owner 소유 표에 못 쓴다.

    주제 사전도 같은 스키마에 세운다: 정답은 그 DB 의 활성 사전이 만든다(#8)."""
    parsed = make_url(needs_runtime_url)
    conn = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )
    rows = [
        ("백탁이 심하다", "d1"),
        ("하얗게 뜬다", "d2"),
        ("하얘서 못 쓰겠다", "d3"),
        ("발림성이 좋다", "d4"),
    ]
    install_topics(conn)
    with conn.cursor() as cur:
        for text, doc in rows:
            cur.execute(
                "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
                "VALUES (%s, %s, 'youtube_comment', 0, %s, 'x')",
                (f"{doc}#0", doc, text),
            )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def test_gold_comes_from_the_topic_dictionary_over_the_chunks(loaded):
    gold = retrieval_eval.gold_from_chunks(loaded)
    assert gold["백탁"] == {"d1", "d2", "d3"}
    assert "d4" not in gold["백탁"]


@pytest.fixture
def mixed_sources(loaded):
    """소스가 둘인 코퍼스 -- 한 소스로 좁힌 평가는 이 코퍼스에서만 틀린 점수를 낸다."""
    with loaded.cursor() as cur:
        cur.execute(
            "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
            "VALUES ('r1#0', 'r1', 'commerce_review', 0, %s, 'y')",
            ("백탁이 남는다",),
        )
    loaded.commit()
    return loaded


def test_the_gold_is_narrowed_to_the_evaluated_sources(mixed_sources):
    # 색인과 검색이 youtube_comment 로 좁혀지면 r1 은 어떤 엔진으로도 나올 수 없다.
    gold = retrieval_eval.gold_from_chunks(mixed_sources, ("youtube_comment",))
    assert gold["백탁"] == {"d1", "d2", "d3"}
    # 소스를 안 넘기면 전 소스가 정답이다 -- 좁히기는 요구했을 때만 일어난다.
    assert retrieval_eval.gold_from_chunks(mixed_sources)["백탁"] == {"d1", "d2", "d3", "r1"}


def test_gold_size_counts_only_the_evaluated_sources(mixed_sources):
    # 닿을 수 없는 문서가 정답에 남으면 P@10·Hit@10 이 깎이고 gold_size 가 거짓말을 한다.
    rows = {
        r.query: r
        for r in retrieval_eval.run(mixed_sources, "literal", sources=("youtube_comment",), cache_dir=None)
    }
    assert rows["백탁"].gold_size == 3


def test_the_gold_pages_through_the_chunks_narrowed_by_source(mixed_sources, monkeypatch):
    """서버 커서로 38만 행을 한 트랜잭션에 훑으면 needs_runtime 의 transaction_timeout(60초,
    db/bootstrap.sql:48)이 트랜잭션 **총 수명**을 끊는다 -- 키셋으로 페이지마다 끊고, 주제 매칭은
    커밋한 뒤에 돈다. 소스 좁힘(#16)이 그 페이징과 함께 살아 있어야 한다(#17 S4)."""
    from analysis.retrieval import topics

    seen: list = []
    matched = topics.match_topics

    def watching(text, **kw):
        seen.append(mixed_sources.info.transaction_status)
        return matched(text, **kw)

    monkeypatch.setattr(topics, "match_topics", watching)
    monkeypatch.setattr(retrieval_eval, "GOLD_PAGE", 2)  # 청크 4개를 여러 페이지로 나눈다
    gold = retrieval_eval.gold_from_chunks(mixed_sources, ("youtube_comment",))
    assert gold["백탁"] == {"d1", "d2", "d3"}  # commerce_review 의 r1 은 좁힘 밖이다
    assert len(seen) == 4  # 페이지가 잘려도 청크를 빠뜨리거나 두 번 세지 않는다
    assert set(seen) == {psycopg.pq.TransactionStatus.IDLE}
    # 좁히지 않으면 전 소스가 정답이다 -- 페이징이 그 뜻을 바꾸지 않는다.
    assert retrieval_eval.gold_from_chunks(mixed_sources)["백탁"] == {"d1", "d2", "d3", "r1"}


def test_literal_finds_the_documents_that_carry_the_query_word(loaded):
    rows = retrieval_eval.run(loaded, "literal", cache_dir=None)
    by_query = {r.query: r for r in rows}
    assert by_query["백탁"].hit is True
    assert by_query["백탁"].p_at_k > 0


def test_heldout_asks_for_the_documents_the_query_word_is_missing_from(loaded):
    rows = {r.query: r for r in retrieval_eval.run(loaded, "heldout", cache_dir=None)}
    row = rows["백탁"]
    # `백탁` 이 든 d1 은 정답에서 빠지고, 같은 주제의 d2·d3 만 남는다.
    assert row.gold_size == 2
    # BM25 는 여기서 거의 0 이 정상이다 -- 그 0 이 벡터가 넘어야 하는 선이다.
    assert row.p_at_k == 0.0


def test_the_summary_reports_the_query_count(loaded):
    assert "질의" in retrieval_eval.summary(retrieval_eval.run(loaded, "literal", cache_dir=None))
    assert retrieval_eval.summary([]).startswith("질의 0개")


def test_an_unknown_engine_is_refused(loaded):
    with pytest.raises(ValueError):
        retrieval_eval.run(loaded, "literal", engine="lucene", cache_dir=None)


def test_cache_dir_none_leaves_no_files_behind(loaded, tmp_path, monkeypatch):
    """`None` 을 "기본값"으로 읽으면 캐시를 끌 방법이 없고, 실제로 테스트가 레포의
    var/retrieval/bm25 에 색인 세 개를 남겼다(2026-08-25)."""
    from analysis.retrieval import pipeline

    monkeypatch.setattr(pipeline, "CACHE_DIR", tmp_path)
    retrieval_eval.run(loaded, "literal", cache_dir=None)
    assert list(tmp_path.iterdir()) == []
    # 기본값을 그대로 쓰면 그 자리에 남는다 -- 둘이 구분된다는 것이 이 테스트의 요점이다.
    retrieval_eval.run(loaded, "literal")
    assert list(tmp_path.glob("index-*.pkl"))


def test_building_the_gold_leaves_no_open_transaction(loaded):
    """서버 커서가 연 트랜잭션을 닫지 않으면, 뒤이어 1.2GB 벡터와 모델을 읽는 동안
    needs_runtime 이 연결을 끊는다(2026-08-25, literal/vector 가 9분 만에 여기서 죽었다)."""
    retrieval_eval.gold_from_chunks(loaded)
    assert loaded.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_the_vector_store_and_encoder_are_opened_once(loaded, monkeypatch, tmp_path):
    """질의마다 열면 1.2GB 행렬과 모델을 61번 읽는다. 결과는 같고 시간만 사라지므로
    수치로는 드러나지 않는다."""
    import numpy as np

    from analysis.retrieval import embed, vectors

    # 저장소는 청크와 같은 chunk_id 를 가져야 to_docs 가 정답과 맞물린다.
    with loaded.cursor() as cur:
        cur.execute("SELECT chunk_id, source FROM retrieval_chunk ORDER BY chunk_id")
        rows = cur.fetchall()
    loaded.commit()
    matrix = np.zeros((len(rows), vectors.DIM), dtype="float32")
    matrix[:, 0] = 1.0
    out = tmp_path / "e5base"
    manifest = {"model": "m", "l2_normalized": True, "query_prefix": "query: ", "dim": vectors.DIM}
    vectors.save(out, matrix, rows, manifest)

    opened = {"store": 0, "encoder": 0}
    real_load = vectors.load

    def counting_load(path=vectors.DEFAULT_STORE):
        opened["store"] += 1
        return real_load(path)

    class FakeEncoder:
        def encode(self, texts, **_kw):
            return [[1.0] + [0.0] * (vectors.DIM - 1) for _ in texts]

    def counting_encoder(*_a, **_kw):
        opened["encoder"] += 1
        return FakeEncoder()

    monkeypatch.setattr(vectors, "load", counting_load)
    monkeypatch.setattr(embed, "load_encoder", counting_encoder)

    rows_out = retrieval_eval.run(loaded, "literal", engine="vector", store=out, cache_dir=None)
    assert rows_out, "질의가 하나도 채점되지 않았다"
    assert opened == {"store": 1, "encoder": 1}, opened


def test_an_unknown_mode_is_refused(loaded):
    with pytest.raises(ValueError):
        retrieval_eval.run(loaded, "vibes", cache_dir=None)


def test_the_scorecard_carries_the_coverage_warning(loaded, monkeypatch, tmp_path):
    """저장소가 청크를 다 덮지 않아도 점수는 멀쩡한 숫자로 나온다 -- 채점표가 어느 코퍼스 위에서
    나왔는지 그 자리에 적혀 있지 않으면 아무도 못 알아챈다(#12 완료 기준 2)."""
    import numpy as np

    from analysis.retrieval import embed, vectors

    with loaded.cursor() as cur:
        cur.execute("SELECT chunk_id, source, chunked_at FROM retrieval_chunk ORDER BY chunk_id")
        rows = cur.fetchall()
    loaded.commit()
    matrix = np.zeros((len(rows), vectors.DIM), dtype="float32")
    matrix[:, 0] = 1.0
    out = tmp_path / "e5base"
    vectors.save(
        out,
        matrix,
        [(chunk_id, source) for chunk_id, source, _ in rows],
        {
            "model": "m",
            "l2_normalized": True,
            "query_prefix": "query: ",
            "dim": vectors.DIM,
            "sources": ["youtube_comment"],
            "chunked_at_max": max(chunked_at for _, _, chunked_at in rows).isoformat(),
        },
    )
    # 저장소를 구운 뒤 청크가 하나 늘었다. 벡터는 이 행을 오류 없이 못 본다.
    with loaded.cursor() as cur:
        cur.execute(
            "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
            "VALUES ('d9#0', 'd9', 'youtube_comment', 0, '백탁이 새로 생겼다', 'z')"
        )
    loaded.commit()

    class FakeEncoder:
        def encode(self, texts, **_kw):
            return [[1.0] + [0.0] * (vectors.DIM - 1) for _ in texts]

    monkeypatch.setattr(embed, "load_encoder", lambda *_a, **_kw: FakeEncoder())
    scored = retrieval_eval.run(loaded, "literal", engine="vector", store=out, cache_dir=None)
    assert scored, "질의가 하나도 채점되지 않았다"
    assert "note" in retrieval_eval.FIELDS  # CSV 로 떨어져 나가도 같이 간다
    assert all(row.note for row in scored)
    assert scored[0].note in retrieval_eval.summary(scored)
    # bm25 에는 대조할 저장소가 없다 -- 없는 경고를 지어내지 않는다.
    assert all(not row.note for row in retrieval_eval.run(loaded, "literal", cache_dir=None))


def _covering_store(conn, out):
    """지금 청크를 그대로 덮는 저장소. 덮으면 `coverage_note` 가 None 이라 경고 자리가 빈다 --
    판본이 경고에 얹혀 있으면 바로 그때 아무 데도 안 남는다."""
    import numpy as np

    from analysis.retrieval import vectors

    with conn.cursor() as cur:
        cur.execute("SELECT chunk_id, source, chunked_at FROM retrieval_chunk ORDER BY chunk_id")
        rows = cur.fetchall()
    conn.commit()
    matrix = np.zeros((len(rows), vectors.DIM), dtype="float32")
    matrix[:, 0] = 1.0
    vectors.save(
        out,
        matrix,
        [(chunk_id, source) for chunk_id, source, _ in rows],
        {
            "model": "intfloat/multilingual-e5-base",
            "revision": "revsha",
            "l2_normalized": True,
            "query_prefix": "query: ",
            "dim": vectors.DIM,
            "sources": ["youtube_comment"],
            "chunked_at_max": max(chunked_at for _, _, chunked_at in rows).isoformat(),
        },
    )
    return out


def test_every_vector_row_carries_the_store_version_even_when_nothing_is_off(loaded, monkeypatch, tmp_path):
    """어긋날 때만 남으면 **정상일 때** 판본이 안 남는다 -- ydc 에서 "1차 → 2차" 로 라벨한 델타가
    실은 "식약처 벡터 없음 → 2차" 였고 2차 산출물을 덮어썼다(2026-08-26, #49)."""
    from analysis.retrieval import embed, vectors

    out = _covering_store(loaded, tmp_path / "e5base")

    class FakeEncoder:
        def encode(self, texts, **_kw):
            return [[1.0] + [0.0] * (vectors.DIM - 1) for _ in texts]

    monkeypatch.setattr(embed, "load_encoder", lambda *_a, **_kw: FakeEncoder())
    scored = retrieval_eval.run(loaded, "literal", engine="vector", store=out, cache_dir=None)
    assert scored, "질의가 하나도 채점되지 않았다"
    # 이 저장소는 청크를 다 덮는다. 경고가 있으면 이 테스트는 "정상일 때"를 재고 있지 않다.
    assert all(not row.note for row in scored), scored[0].note
    assert "store" in retrieval_eval.FIELDS  # CSV 로 떨어져 나가도 같이 간다
    stamped = vectors.load(out).stamp
    assert all(row.store == stamped for row in scored)
    assert "model=intfloat/multilingual-e5-base" in stamped and "vectors=4" in stamped
    assert stamped in retrieval_eval.summary(scored)
    # 저장소를 여는 엔진이 둘이다 -- vector 만 실으면 hybrid 행이 판본 없이 남는다.
    fused = retrieval_eval.run(loaded, "literal", engine="hybrid", store=out, cache_dir=None)
    assert fused and all(row.store == stamped for row in fused)
    # bm25 는 저장소를 열지 않는다 -- 없는 판본을 지어내지 않는다.
    assert all(not row.store for row in retrieval_eval.run(loaded, "literal", cache_dir=None))
