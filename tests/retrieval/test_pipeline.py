"""Chunk loading and search. The needs schema and a source schema have to stand together inside one test, so
the source tables are built directly in the schema the needs_schema fixture made -- the source fixtures use
the same schema name, so both cannot be requested at once."""

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

# The boundary where a row inserted with `now()` is caught and a 2020 row is not. With `date.today()` the
# container is UTC, so in the Korean morning a row inserted today looks like yesterday and the scan comes
# out empty (measured).
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
    """The side that builds and writes the source tables. It corresponds to where a collector writes to its
    own schema in production."""
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
        # In production this is what db/grants/needs_runtime_reader.sql does to the source schema.
        cur.execute("GRANT SELECT ON comments TO needs_runtime")
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def conn(owner, needs_runtime_url: str):
    """The role the pipeline runs as. Only needs_runtime, as in production, makes a missing GRANT show up
    here.

    The topic dictionary is set up in this schema too -- the index does not stand without an active dictionary
    (#8)."""
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
    # An equal text_md5 skips the UPDATE -- otherwise every rerun turns 300k rows into dead tuples.
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
    # Contract: contiguous from 0. A gap and check_rows catches it as a violation.
    assert len(ordinals) > 1
    assert ordinals == list(range(len(ordinals)))


def test_a_shrunken_document_drops_its_stale_ordinals(conn, owner, _schema_name):
    """When the source gets shorter the old ordinal stays forever and the contract ("contiguous from 0",
    contracts/ddl/needs/020_retrieval_chunk.sql:15) breaks at the table level -- check_rows, which sees only a
    batch, takes it that it saw that whole document again and reports no violation (#17 S9)."""
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
    # A rerun with no tail to delete touches no row -- otherwise every run piles up dead tuples.
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
    """A body gone entirely empty has 0 pieces, so that document entered neither the batch nor the tail
    delete -- what S9 covered was only "shortened" documents, and chunks pointing at a source that is gone
    kept coming up in the search (#23)."""
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
        assert cur.fetchone()[0] == 3  # the other documents are unchanged
    assert outcome.emptied > 0
    # The empty body was seen directly in the scan; it was not taken as vanished because it did not come up.
    assert outcome.vanished == 0
    assert "본문이 빈 문서의 청크" in outcome.note


def test_a_row_that_vanished_from_the_source_loses_its_chunks(conn, owner, _schema_name):
    # A body going empty and the row itself disappearing are different events at the source -- the latter
    # does not come up in the scan at all.
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
    """The most dangerous place. `--since` does not scan documents outside its range, so "it did not come up"
    cannot be evidence that "it vanished" -- read that way, one incremental run erases most of the corpus."""
    with owner.cursor() as cur:
        cur.execute(
            "INSERT INTO comments (video_id, comment_id, text, published_at) "
            "VALUES ('v4', 'c5', '오래된 댓글이다', '2020-01-01')"
        )
    owner.commit()
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,), since=SINCE)
    assert outcome.documents == 3  # the old comment is out of range and this run did not see it
    assert (outcome.vanished, outcome.pruned, outcome.emptied) == (0, 0, 0)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM retrieval_chunk WHERE doc_id = 'youtube_comment:c5'")
        assert cur.fetchone()[0] == 1
    assert "사라진 문서" in outcome.note  # 안 찾았다는 사실은 말한다


def test_an_incremental_run_still_clears_a_body_it_scanned_as_empty(conn, owner, _schema_name):
    # The evidence for an empty body is inside the scan -- there is no reason to defer it for being
    # incremental, and deferred, that chunk keeps coming up.
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
    # A rerun with nothing to delete touching rows piles up dead tuples on every run (the same place as #17).
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
    """For a source scanned to 0 documents, "they all vanished" and "it could not be read" (an empty schema, a
    collector that did not run) are indistinguishable. A delete cannot be undone, so it leans to the safe side
    and says what it skipped."""
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
    """Measured (2026-08-25, 381,950 chunks in production), 58 cases came out this way -- one transcript runs
    to 155 pieces, so it straddles a batch boundary, and seen from the later batch alone the ordinal looks
    like it starts at 5."""
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
    """split_text guarantees 500 or less, so it does not happen on our own output, but it can on a path that
    passes already-chunked text straight through (external chunks). In ydc v0.2.0 this is where 27 of them
    were buried behind `[pass]` (#2). 600 characters is over the target (500) but under the hard stop (1000),
    so it does not touch problems (M11: the validator speaks at 500 while the run's exit code stays)."""
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
    # The second comes from the pickle. It has to give the same answer for the cache not to drift from the
    # canonical one.
    second, _ = pipeline.load_index(conn, cache_dir=tmp_path)
    assert second.search("백탁") == first.search("백탁")
    assert second.n == first.n


def test_the_topic_dictionary_is_not_a_file_in_the_cache_key():
    """A topic alias is a Kiwi user word and an entry in expand()'s expansion list, so it decides tokens. Once
    its source moved to `needs.aspect_lexicon`, hashing `topics.py` leaves **a hash that does not cover the
    topic content** in the cache key and cannot stop an old index being reused after the dictionary changed
    (#17 S3 -> #8)."""
    from analysis.retrieval import topics

    files = {p.resolve() for p in pipeline.TOKENIZER_INPUTS}
    assert Path(topics.__file__).resolve() not in files
    assert topics.DICTIONARY_CSV.resolve() not in files  # 적재 원본이지 런타임 입력이 아니다


def test_a_changed_topic_dictionary_invalidates_the_index_cache(conn, _schema_name, tmp_path):
    """The place where completion criterion 3 was breaking for topics -- unless the signature follows the
    active dictionary (version + content fingerprint), the day an alias is added the 96MB old index answers as
    it is (#8)."""
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
    """Without the dictionary switched on the index stands with no error and only the search goes quietly
    empty -- that green is the worst of them."""
    bare = _connect(needs_runtime_url, _schema_name)
    try:
        with pytest.raises(LookupError, match="cosmai lexicon"):
            pipeline.load_index(bare, cache_dir=None)
    finally:
        bare.close()


def test_a_changed_tokenizer_input_invalidates_the_signature(conn, tmp_path, monkeypatch):
    # When an input that decides the tokens changes, the same body becomes different tokens -- if the
    # signature does not move, the old index answers.
    spare = tmp_path / "user_dictionary.tsv"
    spare.write_text("백탁\tNNG\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "TOKENIZER_INPUTS", (spare,))
    before = pipeline.index_signature(conn, None)
    spare.write_text("백탁\tNNG\n허옇\tVA\n", encoding="utf-8")
    assert pipeline.index_signature(conn, None) != before


def test_new_chunks_invalidate_the_cache(conn, owner, _schema_name, tmp_path):
    # If the signature does not move, today's search runs on yesterday's index, and that is not an error but
    # missing results.
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
    # Without the ingredient dictionary on it this goes quietly empty -- no exception is raised.
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    hits = pipeline.search(conn, "에칠헥실트리아존", top=3, cache_dir=None)
    assert [h[0] for h in hits][:1] == ["youtube_comment:c3#0"]


def test_search_leaves_no_open_transaction(conn, _schema_name):
    """Without a commit after the last SELECT the connection stays idle in transaction -- that transaction
    blocks vacuum, and the only thing that cuts it is the 15-second idle_in_transaction_session_timeout
    (#18 M13)."""
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert pipeline.search(conn, "백탁", top=3, cache_dir=None)
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_search_on_an_empty_index_returns_nothing(conn):
    assert pipeline.search(conn, "백탁", cache_dir=None) == []


class _FakeEncoder:
    """Only the place that burns the query is filled -- what is measured here is not the ranking but "does the
    store cover the chunks"."""

    def encode(self, texts, **_kw):
        return [[1.0] + [0.0] * (vectors.DIM - 1) for _ in texts]


@pytest.fixture
def encoded(conn, _schema_name, monkeypatch, tmp_path):
    """Loads the chunks and bakes a store on top of them. Chunks growing after this is the mismatch of #12."""
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
    """When rechunking grows the chunks, BM25 follows through the cache key but the vectors answer, with no
    error, only on the old corpus -- unchecked, that mismatch does not even show up as a wrong ranking
    (#12)."""
    _one_more_chunk(conn)
    hits = pipeline.ranked_chunks(conn, "백탁", engine="vector", store=encoded, cache_dir=None)
    assert hits  # it does not stop -- searching an old corpus on purpose is normal and must not be blocked
    err = capsys.readouterr().err
    assert "경고" in err and "1건" in err


def test_hybrid_search_warns_on_the_same_drift(encoded, conn, capsys):
    # hybrid uses the same store -- fusing in the lexical side does not fill in the missing vectors.
    _one_more_chunk(conn)
    pipeline.ranked_chunks(conn, "백탁", engine="hybrid", store=encoded, cache_dir=None)
    assert "경고" in capsys.readouterr().err


def test_a_store_that_covers_the_corpus_says_nothing(encoded, conn, capsys):
    # A warning printed every time is read by nobody. Covering it, it has to stay quiet.
    pipeline.ranked_chunks(conn, "백탁", engine="vector", store=encoded, cache_dir=None)
    assert capsys.readouterr().err == ""


def test_the_same_count_with_changed_text_is_caught_by_chunked_at_max(encoded, conn, owner, _schema_name):
    """`count` alone cannot catch "same number, different set" -- the manifest's chunked_at_max is that place
    (#12, completion criterion 3)."""
    with owner.cursor() as cur:
        cur.execute("UPDATE comments SET text = '백탁이 아주 심하다' WHERE comment_id = 'c1'")
    owner.commit()
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    store = vectors.load(encoded)
    assert len(store.chunk_ids) == pipeline.chunk_census(conn, (corpus.YOUTUBE_COMMENT,))[0]
    assert pipeline.coverage_note(conn, store) is not None


def test_a_store_made_before_chunked_at_max_still_searches_and_says_so(encoded, conn, capsys):
    """A production store (encoded 2026-08-24) does not have this key. Raised to a required key, every vector
    and hybrid search running today is refused wholesale -- missing it is a place to say so, not to stop."""
    _, _, manifest_path = vectors.paths(encoded)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["chunked_at_max"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    hits = pipeline.ranked_chunks(conn, "백탁", engine="vector", store=encoded, cache_dir=None)
    assert hits
    assert "chunked_at_max" in capsys.readouterr().err


def test_the_sample_cap_holds_across_the_whole_run(conn, owner, _schema_name, monkeypatch):
    """The 3-per-kind cap of check_rows applies only inside one batch -- at the measured scale (381,950 chunks
    = 382 batches), reset per batch, one kind piles past a thousand lines and the report stops being
    readable (#18 M12)."""
    monkeypatch.setattr(pipeline, "split_text", lambda text: [text])  # let a hard-stop overrun through
    monkeypatch.setattr(pipeline, "WRITE_BATCH", 1)  # one document is one batch
    with owner.cursor() as cur:
        cur.executemany(
            "INSERT INTO comments (video_id, comment_id, text, published_at) VALUES (%s,%s,%s,now())",
            [("v9", f"c1{i}", "백" * 1100) for i in range(10)],
        )
    owner.commit()
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    assert sum(1 for p in outcome.problems if p.startswith("너무 긺")) == chunks.SAMPLES_PER_KIND


def test_the_note_counts_kinds_not_samples(conn, owner, _schema_name, monkeypatch):
    # Reading 3 samples of one kind as "3 kinds" gets the breadth of the violation wrong.
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
    """The row number was counted inside one batch, so "row 2" pointed at a different document per batch.
    After the sample cap was carried across the whole run (#18 M12b), which document a remaining sample is
    cannot be read -- an ambiguous coordinate defeats the purpose of a message telling a person to go find the
    original (#27)."""
    monkeypatch.setattr(pipeline, "WRITE_BATCH", 1)  # one document is one batch
    real = pipeline.document_rows

    def blank_source(documents):
        # A missing source is the kind where nothing but the row's own coordinate tells them apart.
        for document, rows in real(documents):
            yield document, [row | {"source": ""} for row in rows]

    monkeypatch.setattr(pipeline, "document_rows", blank_source)
    outcome = pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    missing = [p for p in outcome.problems if p.startswith("source 없음")]
    # The three documents were split into three batches and each is read by its own chunk_id -- before, all
    # three were "row 2" and were folded into the same message before the sample cap was even reached, leaving
    # only one.
    assert {p.split(": ", 1)[1] for p in missing} == {
        f"{corpus.YOUTUBE_COMMENT}:c{i}#0" for i in (1, 2, 3)
    }, outcome.problems


# ---------- the MFDS ledger as a fifth source (#77) ----------
FILING_TEXT = "sun cream 0 · acme labs · report no. 2018008612 · registered 2026-08-20 · mfds-ydc-v0.4.0"


def _seed_filings(conn: psycopg.Connection) -> None:
    """The ledger stands in the needs schema the pipeline already writes to, so the rows go in as
    needs_runtime -- 028 grants it INSERT for exactly the load path that put them in production."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mfds_snapshot (snapshot_id, label, source_tag, source_file, source_rows, "
            "max_report_date, update_policy) VALUES (1, 'mfds-ydc-v0.4.0', 'ydc v0.4.0', "
            "'eval/mfds/x.csv', 1, '2026-08-20', 'not_updated')"
        )
        cur.execute(
            "INSERT INTO mfds_registration (report_seq, item_name, entp_name, report_date, entp_key, "
            "snapshot_id) VALUES (2018008612, 'sun cream 0', 'acme labs', '2026-08-20', 'acmelabs', 1)"
        )
    conn.commit()


def test_a_filing_becomes_one_chunk_under_the_ledger_source(conn, _schema_name):
    _seed_filings(conn)
    outcome = pipeline.run(conn, mfds_schema=_schema_name, sources=(corpus.MFDS,))
    assert (outcome.documents, outcome.chunks, outcome.written) == (1, 1, 1)
    assert outcome.problems == []
    with conn.cursor() as cur:
        cur.execute("SELECT chunk_id, doc_id, source, ordinal, text FROM retrieval_chunk")
        assert cur.fetchall() == [
            ("mfds:2018008612#0", "mfds:2018008612", "mfds", 0, FILING_TEXT),
        ]


def test_the_index_fingerprint_moves_with_the_source_set(conn, _schema_name):
    """A source added without the fingerprint moving means the cached index from before it existed
    answers today's query, and the new source is simply absent -- no error anywhere (#8's rule, #77's
    case)."""
    pipeline.run(conn, youtube_schema=_schema_name, sources=(corpus.YOUTUBE_COMMENT,))
    text_only = corpus.SOURCES[: corpus.SOURCES.index(corpus.MFDS)]
    assert pipeline.index_signature(conn, corpus.SOURCES) != pipeline.index_signature(conn, text_only)
