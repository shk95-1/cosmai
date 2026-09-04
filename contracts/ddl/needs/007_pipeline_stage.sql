-- #138: the expected period of a pipeline stage, as a contract. Additive only
-- (tests/test_ddl_additive_only.py).
--
-- Why a table -- the expected period lived in stack/crontab.d/ alone, and the portal reads the DB
-- through PostgREST only. The reason to declare it here rather than parse the crontab in is
-- enabled: youtube watch *has* a cron line but does not run, being behind a compose profile
-- (STATE.md §2, restart in #39). Neither the crontab nor the DB knows that, so someone has to
-- declare it, and automation removes only half the problem. tests/test_pipeline_stage.py guards
-- the drift against the crontab.
--
-- db/seed/pipeline.py puts the values in. The verdict is carried by needs.pipeline_health, the
-- view that reads this table --
-- for the screen, tool/status and a later alert to answer the same, the verdict has to live in one place.
CREATE TABLE needs.pipeline_stage (
  stage_key         text PRIMARY KEY,          -- '<arm>:<dataset>'. _missing suffixes the analyze incremental pass alone
  arm               text NOT NULL,
  dataset           text NOT NULL,
  expected_interval interval NOT NULL,         -- the period the cron line means; the late/stalled scale comes from it
  enabled           boolean NOT NULL DEFAULT true,
  note              text NOT NULL DEFAULT '',
  -- collector_health's three arms plus analysis. A new arm has to grow the view's UNION too, so
  -- it is blocked here.
  CONSTRAINT pipeline_stage_arm_check CHECK (arm IN ('commerce', 'naver', 'youtube', 'analyze'))
);
