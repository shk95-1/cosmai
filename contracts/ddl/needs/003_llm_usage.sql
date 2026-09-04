-- #6 LLM polarity: the ledger of Anthropic API calls. Additive only (tests/test_ddl_additive_only.py).
-- The budget hard stop ($7.00, contracts/secrets.md) reads this table's usd sum *before* a call and blocks.
CREATE TABLE needs.llm_usage (
  id              bigserial PRIMARY KEY,
  called_at       timestamptz NOT NULL DEFAULT now(),
  model           text NOT NULL,
  purpose         text NOT NULL,             -- what the call was for: eval:polarity:<set> | analyze:polarity | probe
  input_tokens    int  NOT NULL DEFAULT 0,
  output_tokens   int  NOT NULL DEFAULT 0,
  cache_read      int  NOT NULL DEFAULT 0,
  cache_write     int  NOT NULL DEFAULT 0,
  usd             numeric NOT NULL DEFAULT 0,
  batch_id        text                       -- filled by Batches API runs only; NULL for single synchronous calls
);
-- The running total is always a full scan, but retracing the rows one run left reads them in time order.
CREATE INDEX ON needs.llm_usage (called_at);
