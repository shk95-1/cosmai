-- Additive only (epic #16 pre-approval 2: DROP, type changes and other schema changes are excluded).
-- Part of this schema's canonical form since #178: the baseline dump
-- contracts/ddl/current/app.tubedepth.sql plus every file in this directory, applied in filename
-- order. db/migrate.sh step (0) composes that on a database where tubedepth is absent,
-- tests/conftest.py composes it into a throwaway schema, and tool/checks/ddl-drift calls it
-- production's expected state. Production already carries this file (issue #8 approval boundary,
-- contracts/entrypoints.md); step (0) skips a schema that is there, so nothing re-applies it.
--
-- #101, judged on #10 §A-2 rationale 2: `created_at` is enqueue time, not start time -- the gap between the
-- two is queue wait, so collector_health's youtube arm needs a separate `started_at` to ever compute
-- p90_ms. Both columns are nullable: existing rows (created before this migration) have no started_at
-- to backfill, and the view handling that gap is #77's job, not this issue's.
ALTER TABLE tubedepth.jobs ADD COLUMN started_at timestamptz;
ALTER TABLE tubedepth.jobs ADD COLUMN elapsed_ms integer;
