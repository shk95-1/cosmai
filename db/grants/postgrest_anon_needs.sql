-- Stage 1 has no needs_reader role; postgrest_anon gets a direct SELECT whitelist instead
-- (same pattern as service/stack/init/40-postgrest-tubedepth-grants.sh for tubedepth).
-- Whitelist, not default privileges: mention/labeled_set tables must stay invisible to anon.

SELECT format('CREATE ROLE postgrest_anon NOLOGIN')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgrest_anon') \gexec

GRANT USAGE ON SCHEMA needs TO postgrest_anon;
GRANT SELECT ON needs.metrics_need, needs.metrics_wish, needs.entity_lexicon, needs.aspect_lexicon,
    needs.product_ref, needs.analysis_run TO postgrest_anon;

-- 운영 관제 화면(#139)이 읽는 뷰. 포털은 anon 으로 PostgREST 에 묻기 때문에 뷰 파일의
-- `GRANT ... TO needs_runtime` 만으로는 화면이 아무것도 못 받는다 -- 그것이 #138 구현 중
-- 드러났다. 단계 이름·주기·마지막 실행 상태이지 수집 원문이 아니므로 화이트리스트에 든다.
-- collector_health·analysis_health 는 일부러 두지 않는다: 화면이 읽는 것은 판정이 끝난
-- 이 뷰 하나이고, 원본 로그까지 여는 것은 필요 없는 노출이다.
SELECT format('GRANT SELECT ON needs.pipeline_health TO postgrest_anon')
WHERE to_regclass('needs.pipeline_health') IS NOT NULL \gexec

NOTIFY pgrst, 'reload schema';
