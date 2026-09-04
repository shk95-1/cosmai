-- Additive only (epic #16 pre-approval 2: DROP, type changes and other schema changes are excluded).
-- Part of this schema's canonical form since #178: the baseline dump
-- contracts/ddl/current/app.tubedepth.sql plus every file in this directory, applied in filename
-- order. db/migrate.sh step (0) composes that on a database where tubedepth is absent,
-- tests/conftest.py composes it into a throwaway schema, and tool/checks/ddl-drift calls it
-- production's expected state. Production already carries this file (issue #8 approval boundary,
-- contracts/entrypoints.md); step (0) skips a schema that is there, so nothing re-applies it.
--
-- published_at_resolution: what precision comments.published_at actually carries (day|month|year) --
-- YouTube's own comment timestamps are already coarse for older comments.
--
-- channel_is_brand_owner: left with no writer in #8. Filling it needs a brand<->channel mapping, and
-- entity_lexicon has no channel kind yet (kind counts: brand 950, ingredient 42, format 0, attribute 0 --
-- measured 2026-08-24). Adding one is a lexicon contract change, out of this issue's scope.
ALTER TABLE tubedepth.comments ADD COLUMN published_at_resolution text;
ALTER TABLE tubedepth.comments ADD COLUMN channel_is_brand_owner boolean;
