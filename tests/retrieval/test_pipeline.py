"""청크 적재와 검색. needs 스키마와 원천 스키마가 한 테스트 안에서 같이 서야 하므로
needs_schema 픽스처가 만든 스키마에 원천 테이블을 직접 세운다 -- 원천 픽스처는 같은
스키마 이름을 쓰기 때문에 둘을 동시에 요구할 수 없다."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import corpus, pipeline, vectors

pytestmark = pytest.mark.postgres

COMMENTS_DDL = """
CREATE TABLE comments (
  video_id text NOT NULL, comment_id text NOT NULL, text text NOT NULL,
  published_at timestamptz, PRIMARY KEY (video_id, comment_id)
)
"""


def _connect(url: str, schema: str) -> psycopg.Connection:
    parsed = make_url(url)
    return psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=f"-csearch_path={schema},pg_catalog",
    )


@pytest.fixture
def owner(needs_schema: str, _schema_name: str):
    """원천 테이블을 세우고 쓰는 쪽. 운영에서 수집기가 자기 스키마에 쓰는 자리에 해당한다."""
    connection = _connect(needs_schema, _schema_name)
    with connection.cursor() as cur:
        cur.execute(COMMENTS_DDL)
        cur.executemany(
            "INSERT INTO comments (video_id, comment_id, text, published_at) VALUES (%s,%s,%s,now())",
            [
                ("v1", "c1", "백탁이 심해서 하얗게 떠요"),
                ("v1", "c2", "발림성은 좋은데 끈적임이 있다"),
                ("v2", "c3", "에칠헥실트리아존 들어간 제품"),
            ],
        )
        # 운영에서는 db/grants/needs_runtime_reader.sql 이 원천 스키마에 하는 일이다.
        cur.execute("GRANT SELECT ON comments TO needs_runtime")
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def conn(owner, needs_runtime_url: str):
    """파이프라인이 도는 롤. 운영과 같은 needs_runtime 이라야 GRANT 누락이 여기서 드러난다."""
    parsed = make_url(needs_runtime_url)
    connection = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )
    try:
        yield connection
    finally:
        connection.close()


def test_a_run_loads_one_chunk_per_short_comment(conn, _schema_name):
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert outcome.documents == 3
    assert outcome.chunks == 3
    assert outcome.written == 3
    assert outcome.problems == []
    with conn.cursor() as cur:
        cur.execute("SELECT chunk_id, doc_id, source, ordinal FROM retrieval_chunk ORDER BY chunk_id")
        rows = cur.fetchall()
    assert rows[0] == ("youtube_comment:c1#0", "youtube_comment:c1", "youtube_comment", 0)


def test_a_second_run_writes_nothing_new(conn, _schema_name):
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    again = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    # text_md5 가 같으면 UPDATE 를 건너뛴다 -- 안 그러면 재실행마다 30만 행이 죽은 튜플이 된다.
    assert again.chunks == 3
    assert again.written == 0


def test_changed_source_text_updates_the_chunk(conn, owner, _schema_name):
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    with owner.cursor() as cur:
        cur.execute("UPDATE comments SET text = '내용이 바뀌었다' WHERE comment_id = 'c1'")
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert outcome.written == 1
    with conn.cursor() as cur:
        cur.execute("SELECT text FROM retrieval_chunk WHERE chunk_id = 'youtube_comment:c1#0'")
        assert cur.fetchone()[0] == "내용이 바뀌었다"


def test_a_long_comment_becomes_several_ordinals(conn, owner, _schema_name):
    with owner.cursor() as cur:
        cur.execute(
            "INSERT INTO comments (video_id, comment_id, text, published_at) VALUES ('v3', 'c4', %s, now())",
            ("백탁. " * 400,),
        )
    owner.commit()
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ordinal FROM retrieval_chunk WHERE doc_id = 'youtube_comment:c4' ORDER BY ordinal"
        )
        ordinals = [r[0] for r in cur.fetchall()]
    # 계약: 0 부터 연속. 비면 check_rows 가 위반으로 잡는다.
    assert len(ordinals) > 1
    assert ordinals == list(range(len(ordinals)))


def test_a_shrunken_document_drops_its_stale_ordinals(conn, owner, _schema_name):
    """원천이 짧아지면 옛 ordinal 이 영구 잔존해 계약("0 부터 연속",
    contracts/ddl/needs/020_retrieval_chunk.sql:15)이 표 수준에서 깨진다 -- 배치만 보는
    check_rows 는 그 문서를 다시 다 봤다고 여기므로 위반을 못 낸다(#17 S9)."""
    with owner.cursor() as cur:
        cur.execute(
            "INSERT INTO comments (video_id, comment_id, text, published_at) VALUES ('v7', 'c7', %s, now())",
            ("백탁. " * 400,),
        )
    owner.commit()
    first = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert first.pruned == 0
    with owner.cursor() as cur:
        cur.execute("UPDATE comments SET text = '백탁이 조금 있다' WHERE comment_id = 'c7'")
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ordinal FROM retrieval_chunk WHERE doc_id = 'youtube_comment:c7' ORDER BY ordinal"
        )
        assert [r[0] for r in cur.fetchall()] == [0]
    assert outcome.pruned > 0
    assert outcome.problems == []


def test_pruning_the_tail_stays_idempotent(conn, owner, _schema_name):
    # 지울 꼬리가 없는 재실행은 아무 행도 건드리지 않는다 -- 안 그러면 매 실행이 죽은 튜플을 쌓는다.
    with owner.cursor() as cur:
        cur.execute(
            "INSERT INTO comments (video_id, comment_id, text, published_at) VALUES ('v7', 'c7', %s, now())",
            ("백탁. " * 400,),
        )
    owner.commit()
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    again = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert (again.written, again.pruned) == (0, 0)


def test_a_document_split_across_write_batches_is_not_a_false_violation(
    conn, owner, _schema_name, monkeypatch
):
    """실측(2026-08-25, 운영 381,950청크)에서 58건이 이렇게 났다 -- 자막 한 편이 최대 155조각이라
    배치 경계에 걸리고, 뒤쪽 배치만 보면 ordinal 이 5 부터 시작하는 것으로 보인다."""
    monkeypatch.setattr(pipeline, "WRITE_BATCH", 2)
    with owner.cursor() as cur:
        cur.execute(
            "INSERT INTO comments (video_id, comment_id, text, published_at) VALUES ('v9', 'c9', %s, now())",
            ("백탁. " * 400,),  # 여러 조각으로 쪼개지는 한 문서
        )
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert outcome.problems == []


def test_a_chunk_over_the_target_length_is_reported_but_not_blocking(conn, owner, _schema_name, monkeypatch):
    """split_text 는 500 이하를 보장하므로 자체 생성분에는 안 나지만, 이미 조각난 텍스트를
    그대로 흘려보내는 경로(외부 청크에 해당)에서는 날 수 있다 -- ydc v0.2.0 에서 27건이
    `[통과]` 뒤에 묻힌 것이 이 자리다(#2). 600자는 목표(500) 초과지만 하드스톱(1000) 아래라
    problems 를 건드리지 않는다(M11: 검증기가 500 에서 말하되, 실행 종료 코드는 그대로)."""
    monkeypatch.setattr(pipeline, "split_text", lambda text: [text])
    with owner.cursor() as cur:
        cur.execute(
            "INSERT INTO comments (video_id, comment_id, text, published_at) VALUES ('v9', 'c9', %s, now())",
            ("백" * 600,),
        )
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert outcome.problems == []
    assert outcome.over_target == 1
    assert outcome.over_target_max == 600
    assert "목표 상한 초과 1건 (최대 600자)" in outcome.note


def test_no_over_target_line_when_all_chunks_are_within_target(conn, _schema_name):
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert outcome.over_target == 0
    assert "목표 상한 초과" not in outcome.note


def test_the_index_is_cached_and_reused(conn, _schema_name, tmp_path):
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    first, _ = pipeline.load_index(conn, cache_dir=tmp_path)
    files = list(tmp_path.glob("index-*.pkl"))
    assert len(files) == 1
    # 두 번째는 피클에서 온다. 같은 답을 줘야 캐시가 정본과 갈리지 않는다.
    second, _ = pipeline.load_index(conn, cache_dir=tmp_path)
    assert second.search("백탁") == first.search("백탁")
    assert second.n == first.n


def test_the_topic_dictionary_is_part_of_the_cache_key():
    """topics.py 의 별칭은 Kiwi 사용자 단어이자 expand() 의 확장 목록이다 -- 사전 두 벌만 해시하면
    주제를 고친 날 96MB 옛 색인이 그대로 재사용된다(#17 S3)."""
    from analysis.retrieval import topics

    assert Path(topics.__file__).resolve() in {p.resolve() for p in pipeline.TOKENIZER_INPUTS}


def test_a_changed_tokenizer_input_invalidates_the_signature(conn, tmp_path, monkeypatch):
    # 토큰을 정하는 입력이 바뀌면 같은 본문이 다른 토큰이 된다 -- 서명이 안 움직이면 옛 색인이 산다.
    spare = tmp_path / "topics.py"
    spare.write_text("TOPICS = []\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "TOKENIZER_INPUTS", (spare,))
    before = pipeline.index_signature(conn, None)
    spare.write_text("TOPICS = ['백탁']\n", encoding="utf-8")
    assert pipeline.index_signature(conn, None) != before


def test_new_chunks_invalidate_the_cache(conn, owner, _schema_name, tmp_path):
    # 서명이 안 움직이면 어제 색인으로 오늘 검색하게 되고, 그것은 오류가 아니라 빠진 결과다.
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    before = pipeline.index_signature(conn, None)
    with owner.cursor() as cur:
        cur.execute(
            "INSERT INTO comments (video_id, comment_id, text, published_at) "
            "VALUES ('v8', 'c8', '새 댓글이다', now())"
        )
    owner.commit()
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert pipeline.index_signature(conn, None) != before


def test_search_finds_the_chunk_that_contains_the_query(conn, _schema_name):
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    hits = pipeline.search(conn, "백탁", top=3, cache_dir=None)
    assert hits
    assert hits[0][0] == "youtube_comment:c1#0"
    assert "백탁" in hits[0][2]


def test_search_finds_an_ingredient_by_its_exact_name(conn, _schema_name):
    # 성분 사전이 안 얹히면 여기가 조용히 빈다 -- 예외는 나지 않는다.
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    hits = pipeline.search(conn, "에칠헥실트리아존", top=3, cache_dir=None)
    assert [h[0] for h in hits][:1] == ["youtube_comment:c3#0"]


def test_search_on_an_empty_index_returns_nothing(conn):
    assert pipeline.search(conn, "백탁", cache_dir=None) == []


class _FakeEncoder:
    """질의를 태우는 자리만 채운다 -- 여기서 재는 것은 순위가 아니라 "저장소가 청크를 덮는가" 다."""

    def encode(self, texts, **_kw):
        return [[1.0] + [0.0] * (vectors.DIM - 1) for _ in texts]


@pytest.fixture
def encoded(conn, _schema_name, monkeypatch, tmp_path):
    """청크를 적재하고 그 위에 저장소를 굽는다. 이 뒤에 청크가 늘어나는 것이 #12 의 어긋남이다."""
    from analysis.retrieval import embed

    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    monkeypatch.setattr(embed, "load_encoder", lambda *a, **k: _FakeEncoder())
    monkeypatch.setattr(embed, "model_revision", lambda model: "revsha")
    out = tmp_path / "e5base"
    embed.run(conn, out=out, sources=(corpus.YOUTUBE_COMMENT,))
    return out


def _one_more_chunk(conn, chunk_id: str = "youtube_comment:c9#0") -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
            "VALUES (%s, %s, 'youtube_comment', 0, '백탁이 새로 생겼다', 'x')",
            (chunk_id, chunk_id.split("#")[0]),
        )
    conn.commit()


def test_vector_search_warns_about_the_chunks_the_store_does_not_cover(encoded, conn, capsys):
    """재청킹으로 청크가 늘면 BM25 는 캐시 키로 따라가지만 벡터는 오류 없이 옛 코퍼스 위에서만
    답한다 -- 아무도 검사하지 않으면 그 어긋남은 틀린 순위로도 안 나타난다(#12)."""
    _one_more_chunk(conn)
    hits = pipeline.ranked_chunks(conn, "백탁", engine="vector", store=encoded, cache_dir=None)
    assert hits  # 멈추지 않는다 -- 옛 코퍼스를 일부러 검색하는 정상 용법이 막히면 안 된다
    err = capsys.readouterr().err
    assert "경고" in err and "1건" in err


def test_hybrid_search_warns_on_the_same_drift(encoded, conn, capsys):
    # hybrid 도 같은 저장소를 쓴다 -- 융합이 어휘 쪽을 섞는다고 빠진 벡터가 채워지지 않는다.
    _one_more_chunk(conn)
    pipeline.ranked_chunks(conn, "백탁", engine="hybrid", store=encoded, cache_dir=None)
    assert "경고" in capsys.readouterr().err


def test_a_store_that_covers_the_corpus_says_nothing(encoded, conn, capsys):
    # 매번 찍히는 경고는 아무도 안 읽는다. 덮고 있으면 조용해야 한다.
    pipeline.ranked_chunks(conn, "백탁", engine="vector", store=encoded, cache_dir=None)
    assert capsys.readouterr().err == ""


def test_the_same_count_with_changed_text_is_caught_by_chunked_at_max(encoded, conn, owner, _schema_name):
    """`count` 만으로는 "같은 수, 다른 집합" 을 못 잡는다 -- 매니페스트의 chunked_at_max 가
    그 자리다(#12 완료 기준 3)."""
    with owner.cursor() as cur:
        cur.execute("UPDATE comments SET text = '백탁이 아주 심하다' WHERE comment_id = 'c1'")
    owner.commit()
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    store = vectors.load(encoded)
    assert len(store.chunk_ids) == pipeline.chunk_census(conn, (corpus.YOUTUBE_COMMENT,))[0]
    assert pipeline.coverage_note(conn, store) is not None


def test_a_store_made_before_chunked_at_max_still_searches_and_says_so(encoded, conn, capsys):
    """운영 저장소(2026-08-24 인코딩분)에는 이 키가 없다. 필수 키로 올리면 지금 도는 vector·hybrid
    검색이 통째로 거부된다 -- 없으면 말해 줄 자리이지 멈출 자리가 아니다."""
    _, _, manifest_path = vectors.paths(encoded)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["chunked_at_max"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    hits = pipeline.ranked_chunks(conn, "백탁", engine="vector", store=encoded, cache_dir=None)
    assert hits
    assert "chunked_at_max" in capsys.readouterr().err
