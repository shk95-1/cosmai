-- 026: the query log of `cosmai retrieval ask` (fork issue #73). Additive only
-- (tests/test_ddl_additive_only.py); the 02x block is the fork's (contracts/ownership.md rule 1).
--
-- One row per **real call**. A dry run writes nothing because it called nothing, and neither does
-- the evidence-0 path (the gate blocked, or the search found nothing) -- rule 3 is applied by the
-- code, no request goes out, and a row here would say one did.
--
-- Why a log at all: `pipeline.search`'s docstring names bm25's partial answers an "unmeasured
-- loss", and issue #11's next judgement wants the real query distribution. Neither is knowable
-- without the columns below -- there is no query log anywhere else in this schema.
CREATE TABLE needs.retrieval_ask_log (
  id                bigserial PRIMARY KEY,
  called_at         timestamptz NOT NULL DEFAULT now(),
  query             text NOT NULL,   -- what the person asked, verbatim; the distribution #11 wants
  engine            text NOT NULL,   -- bm25 | vector | hybrid, the one the person named
  gate_ok           bool NOT NULL,   -- did the grounding gate pass; bm25 is never gated, so true
  -- Chunk frequency per query token, from the index this call stood on (bm25.tokenize_query +
  -- Index.postings). A token at 0 is the corpus never saying that name -- the fact prompt rule 6
  -- asks the model to read, kept here so the answer can be re-read against it later. The tokens
  -- are the query axis (tokenize_query drops query stopwords, fork #46), not the index axis the
  -- grounding gate weighs (bm25.tokenize) -- so gate_ok can turn on a token absent from this map.
  token_df          jsonb NOT NULL,
  -- The folded documents in rank order. Chunks are the retrieval unit and documents are the
  -- citation unit (fork #73 item 3), so this is the list the answer actually cited.
  doc_ids           text[] NOT NULL,
  index_fingerprint text NOT NULL,   -- pipeline.index_signature: which chunk set and tokenizer
  dictionary_stamp  text NOT NULL,   -- topics.Topics.stamp: which active lexicon version and fingerprint
  store_stamp       text,            -- vectors.VectorStore.stamp; null for bm25, which opens no store
  model             text NOT NULL,
  usd               numeric NOT NULL,  -- what needs.llm_usage settled for this call
  answer_chars      int NOT NULL       -- length of the three sections; the shape of what came back
);
-- The question asked of this table is "what was asked lately", not "what was asked by id".
CREATE INDEX ON needs.retrieval_ask_log (called_at);

-- Append and read only. The runtime never rewrites a call that already happened.
GRANT SELECT, INSERT ON needs.retrieval_ask_log TO needs_runtime;
