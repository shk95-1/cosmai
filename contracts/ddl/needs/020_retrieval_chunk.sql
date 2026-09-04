-- #28 retrieval unit: the chunk store. Additive only (tests/test_ddl_additive_only.py).
--
-- The number is 020 because this file belongs to the long-lived branch feat/ydc-import. While main keeps
-- using 00N the file names must not collide, and db/migrate.sh walks only the files in its own checkout in
-- file-name order, so a 020 row left in the ledger is simply passed over by main's deploy. Being additive,
-- the order does not change the result.
--
-- A chunk is derived from a source, not a source itself. So when a source row disappears it is simply remade,
-- and no foreign key is placed -- the sources live in other schemas (tubedepth, trend_radar) and needs holds
-- SELECT there and nothing more (db/grants/needs_runtime_reader.sql).
CREATE TABLE needs.retrieval_chunk (
  chunk_id    text PRIMARY KEY,          -- `{doc_id}#{ordinal}` (analysis/retrieval/chunks.py FIELDS)
  doc_id      text NOT NULL,             -- `{source}:{source key}` -- where combining sources is concatenation, not a join
  source      text NOT NULL,             -- youtube_comment | youtube_video | youtube_transcript | commerce_review
  ordinal     int  NOT NULL,             -- consecutive from 0 within the document
  text        text NOT NULL,             -- the body after normalize_text
  text_md5    text NOT NULL,             -- says whether re-chunking gave the same result; bodies hit the 2704B btree ceiling
  chunked_at  timestamptz NOT NULL DEFAULT now()
);

-- Used by the query that retraces one document's pieces in order (showing evidence), and by
-- per-source aggregation and evaluation.
CREATE INDEX ON needs.retrieval_chunk (doc_id, ordinal);
CREATE INDEX ON needs.retrieval_chunk (source);

GRANT SELECT, INSERT, UPDATE, DELETE ON needs.retrieval_chunk TO needs_runtime;
