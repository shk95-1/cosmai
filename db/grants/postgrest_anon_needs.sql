-- Stage 1 has no needs_reader role; postgrest_anon gets a direct SELECT whitelist instead
-- (same pattern as service/stack/init/40-postgrest-tubedepth-grants.sh for tubedepth).
-- Whitelist, not default privileges: mention/labeled_set tables must stay invisible to anon.

SELECT format('CREATE ROLE postgrest_anon NOLOGIN')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgrest_anon') \gexec

GRANT USAGE ON SCHEMA needs TO postgrest_anon;
GRANT SELECT ON needs.metrics_need, needs.metrics_wish, needs.entity_lexicon, needs.aspect_lexicon,
    needs.product_ref, needs.analysis_run TO postgrest_anon;

-- 뷰는 여기 적지 않는다. 이 파일은 migrate 단계 (d) 이고 db/views/*.sql 을 DROP + CREATE 하는
-- 것은 (f) 다 -- 여기서 준 GRANT 는 새로 만들어진 뷰에 따라오지 않아 어느 배포에서도 살아남지
-- 못한다(#158: 운영 관제 화면이 그래서 401 이었다). 뷰의 권한은 뷰 파일이 진다, needs_runtime
-- GRANT 가 거기 있는 것과 같은 이유로.

NOTIFY pgrst, 'reload schema';
