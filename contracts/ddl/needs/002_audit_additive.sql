-- app.needs contract reinforcement -- the 51 findings of the 2026-08-23 contract audit (issue #17,
-- coordinator ruling). Each line carries its audit id.
-- Additive only: CREATE TABLE / ADD COLUMN alone, and 001 is untouched
-- (tests/test_ddl_additive_only.py enforces it).

-- ---------- dictionaries ----------
-- B4: the two rule dictionaries (suncare-v2.2 / p1-v2.2) were mixed in one table, so neither
-- could be reproduced.
-- Values: suncare-v2.2 | p1-v2.2 | shared (rows identical in both dictionaries). The loader reads
-- ruleset IN (<requested>, 'shared').
-- No CHECK: the values grow with every dictionary version (suncare-v2.3 ...), so nailing the
-- vocabulary into the DDL would demand a migration per dictionary revision.
ALTER TABLE needs.aspect_lexicon ADD COLUMN ruleset text NOT NULL DEFAULT '';
-- B5: the rule output depends on the matching order. Ascending, ties broken by id (formats.md).
ALTER TABLE needs.aspect_lexicon ADD COLUMN priority int NOT NULL DEFAULT 0;

-- A17: the same concept arrived under two names from two slices, splitting the scope='all' rollup in two.
CREATE TABLE needs.need_key (
  need_key        text PRIMARY KEY,
  canonical       text NOT NULL,             -- the synonym group's representative; itself when there is none
  note            text
);

-- A18: the rule deriving a review's category existed only as a constant in slice code.
CREATE TABLE needs.category_map (
  site            text NOT NULL,             -- a site name, or '*' = every site
  source_category text NOT NULL,             -- rank_snapshot: the site's category leaf / name_keyword: a product-name regex
  lexicon_category text NOT NULL,            -- the aspect_lexicon.category vocabulary
  method          text NOT NULL CHECK (method IN ('rank_snapshot','name_keyword')),
  -- B5 와 같은 이유: name_keyword 정규식은 서로 겹치고(선크림 vs 크림) 먼저 맞는 것이 이긴다. 테이블은 파일 순서를 모른다.
  priority        int  NOT NULL DEFAULT 0,
  PRIMARY KEY (site, source_category)
);

-- ---------- product identity ----------
-- A13: the 230 candidate pairs (a re-review asset) had nowhere to live, and this is where
-- product_member.match_score comes from. B3: the variant review queue is this too.
CREATE TABLE needs.product_ref_candidate (
  src_a           text NOT NULL,
  key_a           text NOT NULL,
  src_b           text NOT NULL,
  key_b           text NOT NULL,
  brand           text,
  shared_tok      int,
  shared_sig      int,
  dice            numeric,
  mutual          boolean,
  linker_version  text NOT NULL,
  PRIMARY KEY (src_a, key_a, src_b, key_b, linker_version)
);

-- A14: the slice emits SKU hits only. line_key = brand || ' ' || line_tokens (formats.md).
ALTER TABLE needs.product_line_mention ADD COLUMN brand text;
ALTER TABLE needs.product_line_mention ADD COLUMN line_tokens text;

-- ---------- mentions ----------
-- B9: the reason behind a polarity verdict. Under the rules it is the lead for error analysis;
-- under the LLM (#6) it is the only audit trace the money bought.
ALTER TABLE needs.need_mention ADD COLUMN polarity_reason text;
-- B10: category 는 사이트 원문이라 '선블록'으로 조회되지 않는다 — 어느 사전 카테고리로 판정했는지.
ALTER TABLE needs.need_mention ADD COLUMN lexicon_category text;
-- A9: the marker wish_mention had and need_mention alone lacked. complaint | wish | low_rating.
ALTER TABLE needs.need_mention ADD COLUMN kind text;
ALTER TABLE needs.need_mention ADD COLUMN marker text;

-- ---------- denominators and ranking derivations ----------
-- B6: a category denominator that includes products with zero mentions cannot be rebuilt from need_mention.
ALTER TABLE needs.product_denominator ADD COLUMN category text;
-- A19: these three tables alone carried neither run_id nor version, so which run made a row was
-- unknowable (versioning.md).
ALTER TABLE needs.product_denominator ADD COLUMN aggregate_version text NOT NULL DEFAULT '';
ALTER TABLE needs.rank_daily ADD COLUMN aggregate_version text NOT NULL DEFAULT '';
ALTER TABLE needs.price_event ADD COLUMN aggregate_version text NOT NULL DEFAULT '';
-- A16: present_share is rounded to two places, so it cannot be back-computed from n_snapshots.
ALTER TABLE needs.rank_daily ADD COLUMN n_present int;
-- A15: the sample-size guard that tells a rank_post24 averaged over 2 snapshots from one averaged over 24.
ALTER TABLE needs.price_event ADD COLUMN n_pre int;
ALTER TABLE needs.price_event ADD COLUMN n_post24 int;

-- ---------- aggregates ----------
-- A1: the whole complaint/satisfaction aggregate on the YouTube comment side.
ALTER TABLE needs.metrics_need ADD COLUMN yt_neg int;
ALTER TABLE needs.metrics_need ADD COLUMN yt_pos int;
-- A2: the denominator of persist_*. Absolute counts alone do not compare across categories.
ALTER TABLE needs.metrics_need ADD COLUMN persist_months_total int;
ALTER TABLE needs.metrics_need ADD COLUMN persist_products_total int;
-- A3: the mean complaint strength (yt_like_mean is not added, the slice recommended dropping it itself).
ALTER TABLE needs.metrics_need ADD COLUMN strength_mean numeric;
-- A4: "did the 2026 new products resolve this complaint" -- the central check of the goal hypothesis.
ALTER TABLE needs.metrics_need ADD COLUMN unresolved_new numeric;
-- A5: scope is a category name, so the generic/category axis is written separately (the same
-- vocabulary as need_mention.aspect_scope).
ALTER TABLE needs.metrics_need ADD COLUMN aspect_scope text;
-- A6: the raw numerator and denominator of the ratio. Needed to tell a ratio over 3 rows from one over 300.
ALTER TABLE needs.metrics_need ADD COLUMN low_mentioning int;
ALTER TABLE needs.metrics_need ADD COLUMN denom_low int;
ALTER TABLE needs.metrics_need ADD COLUMN denom_site int;
-- A7: the single-video concentration and one-row dominance verdicts, the span present inside the
-- window, and an example sentence for a human to read.
ALTER TABLE needs.metrics_wish ADD COLUMN videos int;
ALTER TABLE needs.metrics_wish ADD COLUMN max_like int;
ALTER TABLE needs.metrics_wish ADD COLUMN first_month text;
ALTER TABLE needs.metrics_wish ADD COLUMN last_month text;
ALTER TABLE needs.metrics_wish ADD COLUMN example text;
