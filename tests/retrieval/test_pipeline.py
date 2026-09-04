"""청크 적재와 검색. needs 스키마와 원천 스키마가 한 테스트 안에서 같이 서야 하므로
needs_schema 픽스처가 만든 스키마에 원천 테이블을 직접 세운다 -- 원천 픽스처는 같은
스키마 이름을 쓰기 때문에 둘을 동시에 요구할 수 없다."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import chunks, corpus, pipeline, vectors
from tests.retrieval.conftest import csv_rows, install_topics

pytestmark = pytest.mark.postgres

# `now()` 로 넣은 행은 걸리고 2020년 행은 걸리지 않는 경계. `date.today()` 를 쓰면 컨테이너가
# UTC 라 한국 시각으로 오전에는 오늘 넣은 행이 어제로 보여 훑기가 통째로 빈다(실측).
SINCE = date(2021, 1, 1)

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
    """파이프라인이 도는 롤. 운영과 같은 needs_runtime 이라야 GRANT 누락이 여기서 드러난다.

    주제 사전도 여기서 스키마에 세운다 -- 색인은 활성 사전 없이는 서지 않는다(#8)."""
    parsed = make_url(needs_runtime_url)
    connection = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=parsed.query["options"],  # pyright: ignore[reportArgumentType]
    )
    install_topics(connection)
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


def test_an_emptied_document_loses_all_its_chunks(conn, owner, _schema_name):
    """본문이 통째로 비면 조각이 0개라 그 문서는 배치에도 꼬리 삭제에도 들어가지 않았다 --
    S9 가 덮은 것은 "짧아진" 문서뿐이고, 사라진 원천을 가리키는 청크가 검색에 계속 잡혔다(#23)."""
    with owner.cursor() as cur:
        cur.execute(
            "INSERT INTO comments (video_id, comment_id, text, published_at) VALUES ('v7', 'c7', %s, now())",
            ("백탁. " * 400,),
        )
    owner.commit()
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    with owner.cursor() as cur:
        cur.execute("UPDATE comments SET text = '' WHERE comment_id = 'c7'")
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM retrieval_chunk WHERE doc_id = 'youtube_comment:c7'")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM retrieval_chunk")
        assert cur.fetchone()[0] == 3  # 나머지 문서는 그대로다
    assert outcome.emptied > 0
    # 훑어서 본문이 빈 것을 직접 본 것이지, 안 나와서 사라졌다고 친 것이 아니다.
    assert outcome.vanished == 0
    assert "본문이 빈 문서의 청크" in outcome.note


def test_a_row_that_vanished_from_the_source_loses_its_chunks(conn, owner, _schema_name):
    # 본문만 비는 것과 행 자체가 사라지는 것은 원천에서 다른 일이다 -- 후자는 훑기에 아예 안 나온다.
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    with owner.cursor() as cur:
        cur.execute("DELETE FROM comments WHERE comment_id = 'c1'")
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert outcome.vanished == 1
    with conn.cursor() as cur:
        cur.execute("SELECT doc_id FROM retrieval_chunk ORDER BY doc_id")
        assert [r[0] for r in cur.fetchall()] == ["youtube_comment:c2", "youtube_comment:c3"]
    assert "사라진 문서의 청크" in outcome.note


def test_an_incremental_run_never_drops_what_it_did_not_scan(conn, owner, _schema_name):
    """가장 위험한 자리. `--since` 범위 밖 문서는 훑지 않으므로 "안 나왔다"가 "사라졌다"의 근거가
    될 수 없다 -- 그렇게 읽으면 증분 실행 한 번이 코퍼스 대부분을 지운다."""
    with owner.cursor() as cur:
        cur.execute(
            "INSERT INTO comments (video_id, comment_id, text, published_at) "
            "VALUES ('v4', 'c5', '오래된 댓글이다', '2020-01-01')"
        )
    owner.commit()
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,), since=SINCE)
    assert outcome.documents == 3  # 오래된 댓글은 범위 밖이라 이 실행이 보지 못했다
    assert (outcome.vanished, outcome.pruned, outcome.emptied) == (0, 0, 0)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM retrieval_chunk WHERE doc_id = 'youtube_comment:c5'")
        assert cur.fetchone()[0] == 1
    assert "사라진 문서" in outcome.note  # 안 찾았다는 사실은 말한다


def test_an_incremental_run_still_clears_a_body_it_scanned_as_empty(conn, owner, _schema_name):
    # 빈 본문의 근거는 훑기 안에 있다 -- 증분이라고 미룰 이유가 없고, 미루면 그 청크가 계속 잡힌다.
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    with owner.cursor() as cur:
        cur.execute("UPDATE comments SET text = '' WHERE comment_id = 'c1'")
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,), since=SINCE)
    assert outcome.emptied == 1
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM retrieval_chunk WHERE doc_id = 'youtube_comment:c1'")
        assert cur.fetchone()[0] == 0


def test_the_cleanup_stays_idempotent(conn, owner, _schema_name):
    # 지울 것이 없는 재실행이 행을 건드리면 매 실행이 죽은 튜플을 쌓는다(#17 과 같은 자리).
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    with owner.cursor() as cur:
        cur.execute("UPDATE comments SET text = '' WHERE comment_id = 'c1'")
        cur.execute("DELETE FROM comments WHERE comment_id = 'c2'")
    owner.commit()
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    again = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert (again.written, again.pruned, again.emptied, again.vanished) == (0, 0, 0, 0)
    assert "삭제" not in again.note


def test_a_source_that_scanned_nothing_is_left_out_of_the_cleanup(conn, owner, _schema_name):
    """훑어서 문서가 0건인 소스는 "다 사라졌다"와 "못 읽었다"(빈 스키마·안 돈 수집기)가 구분되지
    않는다. 삭제는 되돌릴 수 없으므로 안전한 쪽으로 기울이되, 건너뛴 사실은 말한다."""
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    with owner.cursor() as cur:
        cur.execute("DELETE FROM comments")
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert outcome.vanished == 0
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM retrieval_chunk")
        assert cur.fetchone()[0] == 3
    assert corpus.YOUTUBE_COMMENT in outcome.note


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


def test_the_topic_dictionary_is_not_a_file_in_the_cache_key():
    """주제 별칭은 Kiwi 사용자 단어이자 expand() 의 확장 목록이라 토큰을 정한다. 그 원천이
    `needs.aspect_lexicon` 으로 옮겨간 뒤 `topics.py` 를 해시하면 **주제 내용을 안 덮는 해시**가
    캐시 키에 남아, 사전을 바꿔도 옛 색인이 재사용되는 것을 못 막는다(#17 S3 -> #8)."""
    from analysis.retrieval import topics

    files = {p.resolve() for p in pipeline.TOKENIZER_INPUTS}
    assert Path(topics.__file__).resolve() not in files
    assert topics.DICTIONARY_CSV.resolve() not in files  # 적재 원본이지 런타임 입력이 아니다


def test_a_changed_topic_dictionary_invalidates_the_index_cache(conn, _schema_name, tmp_path):
    """완료 기준 3번이 주제에 대해 깨지던 자리다 -- 서명이 활성 사전(버전 + 내용 지문)을 따라가지
    않으면 별칭을 하나 더한 날 96MB 옛 색인이 그대로 답한다(#8)."""
    from db.lexicon import activate, insert_aspects

    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    pipeline.load_index(conn, cache_dir=tmp_path)
    before = pipeline.index_signature(conn, None)
    with conn.cursor() as cur:
        more = ("백탁", "generic", "", "허옇", False, "retrieval-topic", 1, {"term_kind": "ko"})
        wider = [*csv_rows(), more]
        insert_aspects(cur, wider, 2, active=False)
        assert pipeline.index_signature(conn, None) == before  # 켜기 전에는 아무 일도 없다
        activate(cur, "aspect", 2)
    conn.commit()
    assert pipeline.index_signature(conn, None) != before
    pipeline.load_index(conn, cache_dir=tmp_path)
    assert len(list(tmp_path.glob("index-*.pkl"))) == 2


def test_an_index_cannot_be_built_without_an_active_dictionary(needs_runtime_url, _schema_name):
    """사전이 안 켜져 있으면 색인은 오류 없이 서고 검색만 조용히 빈다 -- 그 초록이 가장 나쁘다."""
    bare = _connect(needs_runtime_url, _schema_name)
    try:
        with pytest.raises(LookupError, match="cosmai lexicon"):
            pipeline.load_index(bare, cache_dir=None)
    finally:
        bare.close()


def test_a_changed_tokenizer_input_invalidates_the_signature(conn, tmp_path, monkeypatch):
    # 토큰을 정하는 입력이 바뀌면 같은 본문이 다른 토큰이 된다 -- 서명이 안 움직이면 옛 색인이 산다.
    spare = tmp_path / "user_dictionary.tsv"
    spare.write_text("백탁\tNNG\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "TOKENIZER_INPUTS", (spare,))
    before = pipeline.index_signature(conn, None)
    spare.write_text("백탁\tNNG\n허옇\tVA\n", encoding="utf-8")
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


def test_search_leaves_no_open_transaction(conn, _schema_name):
    """마지막 SELECT 뒤에 커밋이 없으면 연결이 idle in transaction 으로 남는다 -- 그 트랜잭션은
    vacuum 을 막고, 끊는 것은 15초짜리 idle_in_transaction_session_timeout 뿐이다(#18 M13)."""
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert pipeline.search(conn, "백탁", top=3, cache_dir=None)
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


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


def test_the_sample_cap_holds_across_the_whole_run(conn, owner, _schema_name, monkeypatch):
    """check_rows 의 종류별 3건 상한은 한 배치 안에서만 걸린다 -- 실측 규모(381,950청크 = 382배치)
    에서 배치마다 리셋되면 한 종류가 천 줄 넘게 쌓여 보고가 다시 읽을 수 없어진다(#18 M12)."""
    monkeypatch.setattr(pipeline, "split_text", lambda text: [text])  # 하드스톱 초과를 그대로 흘린다
    monkeypatch.setattr(pipeline, "WRITE_BATCH", 1)  # 문서 하나가 배치 하나
    with owner.cursor() as cur:
        cur.executemany(
            "INSERT INTO comments (video_id, comment_id, text, published_at) VALUES (%s,%s,%s,now())",
            [("v9", f"c1{i}", "백" * 1100) for i in range(10)],
        )
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert sum(1 for p in outcome.problems if p.startswith("너무 긺")) == chunks.SAMPLES_PER_KIND


def test_the_note_counts_kinds_not_samples(conn, owner, _schema_name, monkeypatch):
    # 한 종류의 표본 3건이 "3종" 으로 읽히면 위반의 폭을 잘못 본다.
    monkeypatch.setattr(pipeline, "split_text", lambda text: [text])
    monkeypatch.setattr(pipeline, "WRITE_BATCH", 1)
    with owner.cursor() as cur:
        cur.executemany(
            "INSERT INTO comments (video_id, comment_id, text, published_at) VALUES (%s,%s,%s,now())",
            [("v9", f"c1{i}", "백" * 1100) for i in range(10)],
        )
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert len(outcome.problems) > 1
    assert "계약 위반 1종" in outcome.note


def test_violations_in_two_batches_read_as_two_coordinates(conn, owner, _schema_name, monkeypatch):
    """행 번호는 한 배치 안에서 세어져 "2행"이 배치마다 다른 문서를 가리켰다. 표본 상한이 실행
    전체로 이어진 뒤(#18 M12b) 남는 표본이 어느 문서인지 읽히지 않는다 -- 좌표가 모호하면 사람이
    원본을 찾아가라는 메시지의 목적이 없어진다(#27)."""
    monkeypatch.setattr(pipeline, "WRITE_BATCH", 1)  # 문서 하나가 배치 하나
    real = pipeline.document_rows

    def blank_source(documents):
        # source 없음은 행 자체의 좌표 말고는 서로를 가를 것이 없는 종류다.
        for document, rows in real(documents):
            yield document, [row | {"source": ""} for row in rows]

    monkeypatch.setattr(pipeline, "document_rows", blank_source)
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    missing = [p for p in outcome.problems if p.startswith("source 없음")]
    # 세 문서가 세 배치로 갈렸고 각각 자기 chunk_id 로 읽힌다 -- 전에는 셋 다 "2행" 이라
    # 표본 상한을 채우기도 전에 같은 메시지로 접혀 한 건만 남았다.
    assert {p.split(": ", 1)[1] for p in missing} == {
        f"{corpus.YOUTUBE_COMMENT}:c{i}#0" for i in (1, 2, 3)
    }, outcome.problems
