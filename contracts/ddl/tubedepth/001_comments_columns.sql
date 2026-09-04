-- Additive only (epic #16 pre-approval 2: DROP, type changes and other schema changes are excluded).
-- Applied to the throwaway test schema only by tests/conftest.py's tubedepth_schema fixture --
-- production tubedepth is untouched by #8 (contracts/entrypoints.md approval boundary, issue #8).
--
-- published_at_resolution: what precision comments.published_at actually carries (day|month|year) --
-- YouTube's own comment timestamps are already coarse for older comments.
--
-- channel_is_brand_owner: left with no writer in #8. Filling it needs a brand<->channel mapping, and
-- entity_lexicon has no channel kind yet (kind counts: brand 950, ingredient 42, format 0, attribute 0 --
-- measured 2026-08-24). Adding one is a lexicon contract change, out of this issue's scope.
ALTER TABLE tubedepth.comments ADD COLUMN published_at_resolution text;
ALTER TABLE tubedepth.comments ADD COLUMN channel_is_brand_owner boolean;
