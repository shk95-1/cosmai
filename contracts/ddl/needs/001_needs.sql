-- app.needs -- the normalisation and analysis output schema (contract v0.1, 2026-08-23)
-- Source: architect/REBUILD.md §2 (the seven-slice requirements matrix). Columns are the union of slice-*/ CSVs.
-- Rules: natural-key upsert, a *_version on every derived row, time as observed_at + observed_at_resolution.

-- Schema and roles are db/bootstrap.sql's job (psql -v schema=needs). This file only creates tables, as the owner.

-- Four roles (the same pattern as before). The init script injects the passwords from the env.
-- needs_owner: owns the DDL / needs_migrator: migrations (SET ROLE needs_owner) / needs_runtime: DML / needs_reader: SELECT
-- The PostgREST anon role gets SELECT on metrics_* · *_lexicon · product_ref alone (the finished aggregates).

-- ---------- identity ----------
CREATE TABLE needs.product_ref (
  product_ref     text PRIMARY KEY,          -- the primary member's key, e.g. 'oy:A000000184352'
  brand           text,
  name_norm       text NOT NULL,             -- normalised name (parentheses, volume and promo wording removed)
  name            text NOT NULL,
  n_sites         int  NOT NULL DEFAULT 1,
  first_seen      date,
  linker_version  text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE needs.product_member (
  source          text NOT NULL,             -- oliveyoung | glowpick | daisomall | hwahae
  product_key     text NOT NULL,
  product_ref     text NOT NULL REFERENCES needs.product_ref,
  role            text NOT NULL CHECK (role IN ('primary','member')),
  match_score     numeric,
  PRIMARY KEY (source, product_key)
);
CREATE TABLE needs.product_variant (          -- refill, size, scent, option -- variants under one ref
  source          text NOT NULL,
  product_key     text NOT NULL,
  variant_of      text NOT NULL REFERENCES needs.product_ref,
  variant_kind    text NOT NULL CHECK (variant_kind IN ('refill','size','scent','shade','option','set')),
  variant_label   text,
  PRIMARY KEY (source, product_key)
);

-- ---------- dictionaries (versioned tables, not files) ----------
CREATE TABLE needs.entity_lexicon (
  id              bigserial PRIMARY KEY,
  kind            text NOT NULL CHECK (kind IN ('brand','format','attribute','ingredient','stopword','alias')),
  canonical       text NOT NULL,
  surface         text NOT NULL,             -- the string actually matched (Korean variants included)
  tier            text,                      -- brand: normal | cooc_required | stop ; null otherwise
  source          text,                      -- rank_snapshot.brand | category_name | paper_lexicon | manual
  version         int  NOT NULL,
  active          boolean NOT NULL DEFAULT true,
  note            text,
  UNIQUE (kind, surface, version)
);
CREATE TABLE needs.aspect_lexicon (
  id              bigserial PRIMARY KEY,
  aspect          text NOT NULL,             -- need_key
  scope           text NOT NULL CHECK (scope IN ('generic','category')),
  category        text NOT NULL DEFAULT '', -- set when scope=category; '' for generic
  pattern         text NOT NULL,             -- regular expression
  is_neutral_noun boolean NOT NULL DEFAULT false,
  version         int  NOT NULL,
  active          boolean NOT NULL DEFAULT true,
  UNIQUE (aspect, scope, category, pattern, version)
);
CREATE TABLE needs.site_axis_map (            -- the site's own topic axis <-> need_key (P1: 25 entries)
  site            text NOT NULL,
  category        text NOT NULL DEFAULT '',
  site_axis       text NOT NULL,
  need_key        text,                      -- null = no counterpart
  note            text,
  PRIMARY KEY (site, category, site_axis)
);

-- ---------- evaluation sets (a first-class asset) ----------
CREATE TABLE needs.labeled_set (
  task            text NOT NULL,             -- polarity | wish_class | brand_link | product_match | aspect
  ref             text NOT NULL,             -- a stable key naming the sentence, comment or pair
  split           text NOT NULL CHECK (split IN ('tune','holdout')),
  gold            text NOT NULL,
  text            text,
  labeler         text NOT NULL,
  labeled_at      date NOT NULL,
  extra           jsonb,
  PRIMARY KEY (task, ref)
);

-- ---------- mentions (the normalisation output) ----------
CREATE TABLE needs.need_mention (
  mention_id      bigserial PRIMARY KEY,
  src             text NOT NULL CHECK (src IN ('review','yt_comment','yt_transcript','naver_blog')),
  site            text NOT NULL,             -- oliveyoung | glowpick | daisomall | youtube | naver
  ref             text NOT NULL,             -- review: product_key/review_key ; comment: video_id/comment_id
  product_ref     text REFERENCES needs.product_ref,
  source_product_key text,
  category        text,
  need_key        text NOT NULL,
  aspect_scope    text,
  polarity        text NOT NULL CHECK (polarity IN ('불만','만족','중립')),
  strength        numeric,                   -- review: 1 - rating/5 ; comment: like_count
  rating          numeric,
  observed_at     date NOT NULL,
  observed_at_resolution text NOT NULL CHECK (observed_at_resolution IN ('day','month','year')),
  month           text NOT NULL,             -- 'YYYY-MM' (the shared aggregation grain)
  sentence        text NOT NULL,
  extractor_version text NOT NULL,
  polarity_version  text NOT NULL,           -- 'rule-v2.2' | 'llm-<model>-<date>'
  UNIQUE (src, ref, need_key, sentence)
);
CREATE TABLE needs.wish_mention (
  mention_id      bigserial PRIMARY KEY,
  src             text NOT NULL CHECK (src IN ('yt_comment','review')),
  ref             text NOT NULL,
  video_id        text,
  channel_id      text,
  channel_is_brand_owner boolean,
  product_ref     text REFERENCES needs.product_ref,
  observed_at     date NOT NULL,
  observed_at_resolution text NOT NULL,
  month           text NOT NULL,
  wish_class      text NOT NULL CHECK (wish_class IN ('a','b','c')),  -- a product or launch request, b creator request, c general wish
  brand           text,
  format          text,
  attribute       text,
  marker          text,
  sentence        text NOT NULL,
  like_count      int,
  extractor_version text NOT NULL,
  UNIQUE (src, ref)
);
CREATE TABLE needs.brand_mention (
  src             text NOT NULL CHECK (src IN ('title','transcript','comment')),
  ref_id          text NOT NULL,
  video_id        text,
  brand           text NOT NULL,             -- canonical
  count           int  NOT NULL,
  cooc_count      int,
  observed_at     date,
  observed_at_resolution text,
  linker_version  text NOT NULL,
  PRIMARY KEY (src, ref_id, brand, linker_version)
);
CREATE TABLE needs.product_line_mention (
  src             text NOT NULL,
  ref_id          text NOT NULL,
  line_key        text NOT NULL,             -- brand + line (not a SKU)
  count           int  NOT NULL,
  linker_version  text NOT NULL,
  PRIMARY KEY (src, ref_id, line_key, linker_version)
);

-- ---------- denominators and ranking derivations ----------
CREATE TABLE needs.product_denominator (
  source          text NOT NULL,
  product_key     text NOT NULL,
  captured_at     date NOT NULL,
  site_review_count int,
  low_collected   int,
  low_complete    boolean,                   -- RATING_ASC 표본에 3★이 섞임 = 1·2★ 전수
  site_low_est    numeric,
  PRIMARY KEY (source, product_key, captured_at)
);
CREATE TABLE needs.rank_daily (
  source          text NOT NULL,
  board           text NOT NULL,
  category_key    text NOT NULL,
  product_key     text NOT NULL,
  day_kst         date NOT NULL,
  n_snapshots     int  NOT NULL,
  present_share   numeric,
  rank_mean       numeric,
  rank_min        int,
  rank_max        int,
  price_mode      int,
  PRIMARY KEY (source, board, category_key, product_key, day_kst)
);
CREATE TABLE needs.price_event (
  source          text NOT NULL,
  product_key     text NOT NULL,
  board           text NOT NULL,
  t_change        timestamptz NOT NULL,
  price_before    int, price_after int, pct numeric,
  direction       text CHECK (direction IN ('drop','rise')),
  rank_pre6 numeric, rank_post6 numeric, rank_post12 numeric, rank_post24 numeric,
  PRIMARY KEY (source, product_key, board, t_change)
);

-- ---------- aggregates (what the read exit exposes) ----------
CREATE TABLE needs.analysis_run (
  run_id          bigserial PRIMARY KEY,
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  status          text NOT NULL DEFAULT 'running',
  versions        jsonb NOT NULL,            -- {linker, extractor, polarity, aggregate, lexicon}
  note            text
);
CREATE TABLE needs.metrics_need (
  run_id          bigint NOT NULL REFERENCES needs.analysis_run,
  scope           text NOT NULL,             -- a category name, or 'all'
  need_key        text NOT NULL,
  month           text NOT NULL DEFAULT '', -- '' = the whole period
  product_ref     text NOT NULL DEFAULT '', -- '' = the category total
  neg int NOT NULL, pos int NOT NULL,
  unresolved      numeric,                   -- neg/(neg+pos)
  low_share       numeric,                   -- the share within the low-rating sample
  population_share_pct numeric,              -- x the site's low-rating share
  strength_low_rating_ratio numeric,
  persist_months  int,
  persist_products int,
  PRIMARY KEY (run_id, scope, need_key, month, product_ref)
);
CREATE TABLE needs.metrics_wish (
  run_id bigint NOT NULL REFERENCES needs.analysis_run,
  scope text NOT NULL, format text NOT NULL DEFAULT '', attribute text NOT NULL DEFAULT '', brand text NOT NULL DEFAULT '',
  mentions int NOT NULL, channels int, months_present int, like_sum int, like_cap_sum numeric,
  PRIMARY KEY (run_id, scope, format, attribute, brand)
);
