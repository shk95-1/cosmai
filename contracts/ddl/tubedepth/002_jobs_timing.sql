-- Additive only (epic #16 사전 승인 2: DROP·타입 변경·다른 스키마 변경은 제외).
-- Applied to the throwaway test schema only by tests/conftest.py's tubedepth_schema fixture --
-- production tubedepth is untouched until the coordinator session applies this file directly (issue #8
-- 승인 경계, contracts/entrypoints.md).
--
-- #101, judged on #10 §A-2 근거 2: `created_at` is enqueue time, not start time -- the gap between the
-- two is queue wait, so collector_health's youtube arm needs a separate `started_at` to ever compute
-- p90_ms. Both columns are nullable: existing rows (created before this migration) have no started_at
-- to backfill, and the view handling that gap is #77's job, not this issue's.
ALTER TABLE tubedepth.jobs ADD COLUMN started_at timestamptz;
ALTER TABLE tubedepth.jobs ADD COLUMN elapsed_ms integer;
