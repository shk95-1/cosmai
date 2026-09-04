-- The naver collector's source tables (issue #9, coordinator ruling (a)). Additive only -- 001 is
-- untouched (test_ddl_additive_only.py).
--
-- A pragmatic choice: needs is the analysis-output schema, not a source store. But collectors/commerce and
-- collectors/youtube each own a trend_radar / tubedepth schema, while naver is the first collector to appear
-- in step 5 and has no schema of its own (this repo decided not to inherit cosmai-old's job-queue
-- and source-row model --
-- the REBUILD premise, #18). Rather than one more schema, it sits in needs under a naver_ prefix.
-- If a second collector
-- needs source storage (say #10's live cutover adds another source), the split gets looked at again then.
--
-- naver_blog_post is the source table the ref grammar in formats.md (`naver_blog` -> `post_id`) points at
-- (the gap #17 T15 named), and analysis.types.TextUnit(src='naver_blog') is filled by reading it.

CREATE TABLE needs.naver_run (
  id                 uuid PRIMARY KEY,
  dataset            text NOT NULL CHECK (dataset IN ('datalab','blog')),
  captured_at        timestamptz NOT NULL,
  started_at         timestamptz NOT NULL,
  finished_at        timestamptz,
  status             text NOT NULL
                       CHECK (status IN ('running','ok','partial','blocked','failed')),
  note               text,
  collector_version  text
);

CREATE TABLE needs.naver_fetch_log (
  id          bigserial PRIMARY KEY,
  run_id      uuid NOT NULL REFERENCES needs.naver_run(id) ON DELETE CASCADE,
  at          timestamptz NOT NULL,
  dataset     text NOT NULL,
  query       text NOT NULL,                 -- what was requested: a datalab group name, or a blog search term
  status      int,
  attempt     int NOT NULL,
  elapsed_ms  int,
  bytes       int,
  error       text
);
CREATE INDEX ix_naver_fetch_log_run_at ON needs.naver_fetch_log (run_id, at);

CREATE TABLE needs.naver_datalab_point (
  category     text NOT NULL,                -- lexicon_category 어휘 (예: 선블록)
  group_key    text NOT NULL,                 -- keywords.json 의 그룹 이름 (예: 백탁)
  month        text NOT NULL,                 -- 'YYYY-MM'
  ratio        numeric,
  terms        jsonb NOT NULL,                -- the search terms actually put in the request (for audit)
  captured_at  timestamptz NOT NULL,
  PRIMARY KEY (category, group_key, month)
);

CREATE TABLE needs.naver_blog_post (
  post_id                 text PRIMARY KEY,   -- ref = post_id (formats.md). The source's stable link.
  url                     text NOT NULL,
  category                text,
  group_key               text,
  query                   text,               -- the search term that found this post
  title                   text NOT NULL,
  excerpt                 text NOT NULL,
  author                  text,
  published_at            date,               -- NULL when naver gives no postdate
  observed_at_resolution  text NOT NULL CHECK (observed_at_resolution IN ('day','month','year')),
  captured_at             timestamptz NOT NULL
);
