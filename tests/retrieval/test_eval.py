"""Pins down the definitions of literal and heldout. Blur the difference between the two modes and the
heldout number stops being "the line the vectors have to beat", and the adoption criterion of #28 step 4
loses its ground."""

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
    # They have no discriminating power and were left out of the judgement. They have to be out of the
    # evaluation as well.
    assert "선크림" not in topics and "추천_재구매" not in topics
    assert ("백탁", "하얗게") in literal


def test_heldout_drops_a_topic_that_has_only_one_alias():
    # With one alias there is nothing to take out, so the heldout question does not stand at all.
    assert any(t == "혼합자차" for t, _ in retrieval_eval.queries("literal"))
    assert not any(t == "혼합자차" for t, _ in retrieval_eval.queries("heldout"))


def test_chunks_of_one_document_count_once():
    # One long document filling the top 10 makes P@10 measure pieces rather than documents.
    ranked = retrieval_eval.to_docs(["d1#0", "d1#1", "d2#0", "d1#2", "d3#0"], k=10)
    assert ranked == ["d1", "d2", "d3"]


def test_scoring_counts_rank_positions():
    p, mrr, hit = retrieval_eval.score(["a", "b", "c"], {"b"})
    assert p == pytest.approx(1 / 3)
    assert mrr == pytest.approx(1 / 2)
    assert hit is True
    assert retrieval_eval.score([], {"b"}) == (0.0, 0.0, False)


def test_held_out_docs_are_removed_by_token_not_by_substring():
    # Their characters do not overlap but Kiwi gives them the same stem. They have to be taken out by token.
    index = Index(["d1#0", "d2#0"], ["하얘서 별로다", "발림성이 좋다"])
    assert retrieval_eval.docs_with_tokens(index, "하얗게") == {"d1"}


@pytest.fixture
def loaded(needs_schema: str, needs_runtime_url: str):
    """The chunks are written by needs_runtime, as in production -- migrator cannot write to a table owned by
    needs_owner.

    The topic dictionary is set up in the same schema: the answers are made by that DB's active dictionary
    (#8)."""
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
    """A corpus with two sources -- an evaluation narrowed to one source gives a wrong score only on this
    corpus."""
    with loaded.cursor() as cur:
        cur.execute(
            "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
            "VALUES ('r1#0', 'r1', 'commerce_review', 0, %s, 'y')",
            ("백탁이 남는다",),
        )
    loaded.commit()
    return loaded


def test_the_gold_is_narrowed_to_the_evaluated_sources(mixed_sources):
    # With indexing and search narrowed to youtube_comment, r1 cannot come out of any engine.
    gold = retrieval_eval.gold_from_chunks(mixed_sources, ("youtube_comment",))
    assert gold["백탁"] == {"d1", "d2", "d3"}
    # Without sources passed, every source is the answer set -- narrowing happens only when asked for.
    assert retrieval_eval.gold_from_chunks(mixed_sources)["백탁"] == {"d1", "d2", "d3", "r1"}


def test_gold_size_counts_only_the_evaluated_sources(mixed_sources):
    # An unreachable document left in the answers cuts P@10 and Hit@10 and makes gold_size lie.
    rows = {
        r.query: r
        for r in retrieval_eval.run(mixed_sources, "literal", sources=("youtube_comment",), cache_dir=None)
    }
    assert rows["백탁"].gold_size == 3


def test_the_gold_pages_through_the_chunks_narrowed_by_source(mixed_sources, monkeypatch):
    """Walking 380k rows in one transaction on a server cursor makes needs_runtime's transaction_timeout
    (60s, db/bootstrap.sql:48) cut the **total lifetime** of the transaction -- so it is cut per page with a
    keyset and the topic matching runs after the commit. The source narrowing (#16) has to stay alive
    alongside that paging (#17 S4)."""
    from analysis.retrieval import topics

    seen: list = []
    matched = topics.match_topics

    def watching(text, **kw):
        seen.append(mixed_sources.info.transaction_status)
        return matched(text, **kw)

    monkeypatch.setattr(topics, "match_topics", watching)
    monkeypatch.setattr(retrieval_eval, "GOLD_PAGE", 2)  # cut the chunks into several pages
    gold = retrieval_eval.gold_from_chunks(mixed_sources, ("youtube_comment",))
    assert gold["백탁"] == {"d1", "d2", "d3"}  # commerce_review 의 r1 은 좁힘 밖이다
    assert len(seen) == 4  # a cut page must not drop a chunk
    assert set(seen) == {psycopg.pq.TransactionStatus.IDLE}
    # Without narrowing, every source is the answer set -- paging does not change that meaning.
    assert retrieval_eval.gold_from_chunks(mixed_sources)["백탁"] == {"d1", "d2", "d3", "r1"}


def test_literal_finds_the_documents_that_carry_the_query_word(loaded):
    rows = retrieval_eval.run(loaded, "literal", cache_dir=None)
    by_query = {r.query: r for r in rows}
    assert by_query["백탁"].hit is True
    assert by_query["백탁"].p_at_k > 0


def test_heldout_asks_for_the_documents_the_query_word_is_missing_from(loaded):
    rows = {r.query: r for r in retrieval_eval.run(loaded, "heldout", cache_dir=None)}
    row = rows["백탁"]
    # d1, which holds it, drops out of the answers, and only d2 and d3 of the same topic are left.
    assert row.gold_size == 2
    # BM25 being near 0 here is normal -- that 0 is the line the vectors have to beat.
    assert row.p_at_k == 0.0


def test_the_summary_reports_the_query_count(loaded):
    assert "질의" in retrieval_eval.summary(retrieval_eval.run(loaded, "literal", cache_dir=None))
    assert retrieval_eval.summary([]).startswith("질의 0개")


def test_an_unknown_engine_is_refused(loaded):
    with pytest.raises(ValueError):
        retrieval_eval.run(loaded, "literal", engine="lucene", cache_dir=None)


def test_cache_dir_none_leaves_no_files_behind(loaded, tmp_path, monkeypatch):
    """Read `None` as "the default" and there is no way to turn the cache off, and the tests really did leave
    three indexes in the repo's var/retrieval/bm25 (2026-08-25)."""
    from analysis.retrieval import pipeline

    monkeypatch.setattr(pipeline, "CACHE_DIR", tmp_path)
    retrieval_eval.run(loaded, "literal", cache_dir=None)
    assert list(tmp_path.iterdir()) == []
    # Used with the default it stays in that place -- that the two are distinguished is the point of this
    # test.
    retrieval_eval.run(loaded, "literal")
    assert list(tmp_path.glob("index-*.pkl"))


def test_building_the_gold_leaves_no_open_transaction(loaded):
    """Without closing the transaction a server cursor opened, needs_runtime cuts the connection while the
    1.2GB vectors and the model are read next (2026-08-25, literal/vector died here after 9 minutes)."""
    retrieval_eval.gold_from_chunks(loaded)
    assert loaded.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


def test_the_vector_store_and_encoder_are_opened_once(loaded, monkeypatch, tmp_path):
    """Opened per query it reads a 1.2GB matrix and the model 61 times. The result is the same and only the
    time disappears, so the numbers do not show it."""
    import numpy as np

    from analysis.retrieval import embed, vectors

    # The store has to carry the same chunk_id as the chunks for to_docs to mesh with the answers.
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
    """A store that does not cover the chunks still gives a perfectly normal-looking score -- unless the
    score sheet says which corpus it came out on, in that very place, nobody notices (#12, completion
    criterion 2)."""
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
    # One chunk was added after the store was baked. The vectors cannot see this row, with no error.
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
    assert "note" in retrieval_eval.FIELDS  # it has to be a column so it lands in the CSV
    assert all(row.note for row in scored)
    assert scored[0].note in retrieval_eval.summary(scored)
    # bm25 has no store to compare against -- a warning that does not exist is not invented.
    assert all(not row.note for row in retrieval_eval.run(loaded, "literal", cache_dir=None))


def _covering_store(conn, out):
    """A store that covers the current chunks as they are. Covering them, `coverage_note` is None and the
    warning slot is empty -- with the revision riding on the warning, that is exactly when it is left
    nowhere."""
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
    """Left only when something is off, the revision is not left **when everything is normal** -- in ydc a
    delta labelled "first pass -> second pass" was really "no MFDS vectors -> second pass" and it overwrote
    the second-pass output (2026-08-26, #49)."""
    from analysis.retrieval import embed, vectors

    out = _covering_store(loaded, tmp_path / "e5base")

    class FakeEncoder:
        def encode(self, texts, **_kw):
            return [[1.0] + [0.0] * (vectors.DIM - 1) for _ in texts]

    monkeypatch.setattr(embed, "load_encoder", lambda *_a, **_kw: FakeEncoder())
    scored = retrieval_eval.run(loaded, "literal", engine="vector", store=out, cache_dir=None)
    assert scored, "질의가 하나도 채점되지 않았다"
    # This store covers every chunk. With a warning, this test is not measuring "when everything is normal".
    assert all(not row.note for row in scored), scored[0].note
    assert "store" in retrieval_eval.FIELDS  # it has to be a column so it lands in the CSV
    stamped = vectors.load(out).stamp
    assert all(row.store == stamped for row in scored)
    assert "model=intfloat/multilingual-e5-base" in stamped and "vectors=4" in stamped
    assert stamped in retrieval_eval.summary(scored)
    # Two engines open the store -- carry only vector and the hybrid rows are left with no revision.
    fused = retrieval_eval.run(loaded, "literal", engine="hybrid", store=out, cache_dir=None)
    assert fused and all(row.store == stamped for row in fused)
    # bm25 does not open the store -- a revision that does not exist is not invented.
    assert all(not row.store for row in retrieval_eval.run(loaded, "literal", cache_dir=None))


def test_every_row_carries_the_dictionary_it_was_scored_on(loaded, monkeypatch, tmp_path):
    """The store revision alone is half the story -- the topic dictionary makes both the answers and the
    queries, so a different dictionary gives a different table on the same store. A bm25 row has no store but
    it does have a dictionary (#62)."""
    from analysis.retrieval import embed, topics, vectors

    stamped = topics.load(loaded).stamp
    assert "ruleset=retrieval-topic" in stamped and "fingerprint=" in stamped
    assert "dictionary" in retrieval_eval.FIELDS  # it has to be a column so it lands in the CSV

    lexical = retrieval_eval.run(loaded, "literal", cache_dir=None)
    assert lexical, "질의가 하나도 채점되지 않았다"
    # An engine that opens no store -- `store` is empty and `dictionary` is filled. That is what it means for
    # the two columns to be on different axes.
    assert all(not row.store for row in lexical)
    assert all(row.dictionary == stamped for row in lexical)
    assert stamped in retrieval_eval.summary(lexical)

    out = _covering_store(loaded, tmp_path / "e5base")

    class FakeEncoder:
        def encode(self, texts, **_kw):
            return [[1.0] + [0.0] * (vectors.DIM - 1) for _ in texts]

    monkeypatch.setattr(embed, "load_encoder", lambda *_a, **_kw: FakeEncoder())
    for engine in ("vector", "hybrid"):
        scored = retrieval_eval.run(loaded, "literal", engine=engine, store=out, cache_dir=None)
        assert scored and all(row.dictionary == stamped for row in scored), engine


def test_the_dictionary_stamp_follows_the_dictionary_the_run_actually_read(loaded):
    """Carry only the number and a run that added rows to a version already switched on claims the same
    revision -- the fingerprint is what stops that. If the row revision does not move when the dictionary is
    widened and it is run again, this column says nothing."""
    from analysis.retrieval import topics
    from db.lexicon import activate, insert_aspects
    from tests.retrieval.conftest import csv_rows

    before = {row.dictionary for row in retrieval_eval.run(loaded, "literal", cache_dir=None)}
    assert len(before) == 1
    with loaded.cursor() as cur:
        more = ("백탁", "generic", "", "허옇", False, topics.RULESET, 1, {"term_kind": "ko"})
        insert_aspects(cur, [*csv_rows(), more], 2, active=False)
        activate(cur, "aspect", 2)
    loaded.commit()
    after = {row.dictionary for row in retrieval_eval.run(loaded, "literal", cache_dir=None)}
    assert len(after) == 1
    assert after != before
    after_one = after.pop()
    assert "version=2" in after_one

    # **Rows are added to a version that is switched on.** And on a family that moves neither the alias count
    # nor the topic count (mfds_inci) -- in the revision string **only the fingerprint** sees this change.
    # Without the fingerprint this run claims the same revision as the previous one, and that lie comes out
    # as a plausible table rather than an error.
    with loaded.cursor() as cur:
        spare = {"term_kind": "mfds_inci"}
        insert_aspects(cur, [("백탁", "generic", "", "티타늄디옥사이드", False, topics.RULESET, 1, spare)], 2)
    loaded.commit()
    widened = {row.dictionary for row in retrieval_eval.run(loaded, "literal", cache_dir=None)}
    assert len(widened) == 1
    stamped = widened.pop()
    assert stamped != after_one
    # Neither the number nor the alias count moved -- that the one column that differs is the fingerprint is
    # the point of this test.
    assert stamped.rsplit(" · ", 1)[0] == after_one.rsplit(" · ", 1)[0]
