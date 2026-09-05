-- Stage 1 has no needs_reader role; postgrest_anon gets a direct SELECT whitelist instead
-- (same pattern as service/stack/init/40-postgrest-tubedepth-grants.sh for tubedepth).
-- Whitelist, not default privileges: mention/labeled_set tables must stay invisible to anon.
-- This sentence is only true for the `needs` slice. The same anon role also reads 13 tables of
-- `trend_radar` (trend_radar_reader membership) and 12 of `tubedepth` (direct GRANT + DEFAULT
-- PRIVILEGES), and both of those were opened by the old stack's init, out of this file's reach --
-- the measurement, the path and the pending decision live in contracts/anon_exposure.md (#168).

SELECT format('CREATE ROLE postgrest_anon NOLOGIN')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgrest_anon') \gexec

GRANT USAGE ON SCHEMA needs TO postgrest_anon;
GRANT SELECT ON needs.metrics_need, needs.metrics_wish, needs.entity_lexicon, needs.aspect_lexicon,
    needs.product_ref, needs.analysis_run TO postgrest_anon;

-- Two tables the structure map (#142) reads. Being tables, this is the right spot for them -- unlike
-- a view, a deploy never recreates them, so this GRANT survives (a view is where #158 learned the
-- opposite: a view's own file carries its grants).
-- They belong on the whitelist because they are stage names and the relationships among them, not the
-- collected original text.
GRANT SELECT ON needs.pipeline_stage, needs.pipeline_edge TO postgrest_anon;

-- A view is never written here. This file is migrate stage (d), while DROP + CREATE over db/views/*.sql
-- is stage (f) -- a GRANT given here does not carry over to the newly created view and survives no
-- deploy at all (#158: that is why the operations dashboard was returning 401). A view's own file
-- carries its grants, for the same reason the needs_runtime GRANT lives there.

NOTIFY pgrst, 'reload schema';
