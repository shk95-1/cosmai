"""청크 적재와 검색. needs 스키마와 원천 스키마가 한 테스트 안에서 같이 서야 하므로
needs_schema 픽스처가 만든 스키마에 원천 테이블을 직접 세운다 -- 원천 픽스처는 같은
스키마 이름을 쓰기 때문에 둘을 동시에 요구할 수 없다."""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy.engine import make_url

from analysis.retrieval import corpus, pipeline

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


def test_search_finds_the_chunk_that_contains_the_query(conn, _schema_name):
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    hits = pipeline.search(conn, "백탁", top=3)
    assert hits
    assert hits[0][0] == "youtube_comment:c1#0"
    assert "백탁" in hits[0][2]


def test_search_finds_an_ingredient_by_its_exact_name(conn, _schema_name):
    # 성분 사전이 안 얹히면 여기가 조용히 빈다 -- 예외는 나지 않는다.
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    hits = pipeline.search(conn, "에칠헥실트리아존", top=3)
    assert [h[0] for h in hits][:1] == ["youtube_comment:c3#0"]


def test_search_on_an_empty_index_returns_nothing(conn):
    assert pipeline.search(conn, "백탁") == []
