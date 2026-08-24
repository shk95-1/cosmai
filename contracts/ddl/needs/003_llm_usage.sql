-- #6 LLM 극성: Anthropic API 호출 원장. 추가만 (tests/test_ddl_additive_only.py).
-- 예산 하드스톱($7.00, contracts/secrets.md)은 이 표의 usd 합을 호출 *전에* 읽고 막는다.
CREATE TABLE needs.llm_usage (
  id              bigserial PRIMARY KEY,
  called_at       timestamptz NOT NULL DEFAULT now(),
  model           text NOT NULL,
  purpose         text NOT NULL,             -- eval:polarity:<셋> | analyze:polarity | probe 등 호출 목적
  input_tokens    int  NOT NULL DEFAULT 0,
  output_tokens   int  NOT NULL DEFAULT 0,
  cache_read      int  NOT NULL DEFAULT 0,
  cache_write     int  NOT NULL DEFAULT 0,
  usd             numeric NOT NULL DEFAULT 0,
  batch_id        text                       -- Batches API 실행만 채운다; 단건 동기 호출은 NULL
);
-- 누적 합계는 항상 전체 스캔이지만, 실행 하나가 남긴 행을 되짚는 조회는 시각순이다.
CREATE INDEX ON needs.llm_usage (called_at);
