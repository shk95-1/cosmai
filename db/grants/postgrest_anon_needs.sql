-- Stage 1 has no needs_reader role; postgrest_anon gets a direct SELECT whitelist instead
-- (same pattern as service/stack/init/40-postgrest-tubedepth-grants.sh for tubedepth).
-- Whitelist, not default privileges: mention/labeled_set tables must stay invisible to anon.

SELECT format('CREATE ROLE postgrest_anon NOLOGIN')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgrest_anon') \gexec

GRANT USAGE ON SCHEMA needs TO postgrest_anon;
GRANT SELECT ON needs.metrics_need, needs.metrics_wish, needs.entity_lexicon, needs.aspect_lexicon,
    needs.product_ref, needs.analysis_run TO postgrest_anon;

NOTIFY pgrst, 'reload schema';
