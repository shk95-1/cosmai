-- 읽기 전용. postgrest_anon 이 세 스키마에서 무엇을 보는지와 그것을 여는 경로를 찍는다.
-- postgrest_anon_old_stack.sql 적용 **전과 후에 각각** 돌려 두 출력을 비교하는 것이 쓰임새다.
--
--   docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' shared-postgres \
--     psql -U platform -d app -X < db/grants/postgrest_anon_check.sql
--
-- 읽기 전용 세션에서 돌아야 한다 -- 이 파일에 SELECT 아닌 것이 섞이면 거기서 죽는다.
-- 그것이 이 파일의 안전장치이고, tests/test_anon_exposure_contract.py 가 그 성질을 지킨다.

\pset pager off

-- 1) 보이는 관계 전부와 경로. 'direct' 는 relacl 에 postgrest_anon 항목이 있다는 뜻이고,
--    'trend_radar_reader' 는 없는데도 보인다는 뜻 -- 즉 롤 멤버십으로 상속받았다.
SELECT n.nspname AS schema,
       c.relname AS relation,
       CASE c.relkind WHEN 'v' THEN 'view' WHEN 'm' THEN 'matview' ELSE 'table' END AS kind,
       CASE WHEN EXISTS (
                SELECT FROM aclexplode(c.relacl) a
                WHERE a.grantee = 'postgrest_anon'::regrole AND a.privilege_type = 'SELECT')
            THEN 'direct' ELSE 'inherited' END AS via
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('trend_radar', 'tubedepth', 'needs')
  AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
  AND has_table_privilege('postgrest_anon', c.oid, 'SELECT')
ORDER BY 1, 2;

-- 2) 스키마별 개수. 적용 후 목표는 needs 9 · trend_radar 9 · tubedepth 3 = 21 이다
--    (적용 전 실측 2026-08-27: needs 9 · trend_radar 13 · tubedepth 12 = 34).
SELECT n.nspname AS schema,
       count(*) AS visible,
       CASE n.nspname WHEN 'needs' THEN 9 WHEN 'trend_radar' THEN 9 WHEN 'tubedepth' THEN 3 END AS target_after,
       CASE n.nspname WHEN 'needs' THEN 9 WHEN 'trend_radar' THEN 13 WHEN 'tubedepth' THEN 12 END AS measured_before
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('trend_radar', 'tubedepth', 'needs')
  AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
  AND has_table_privilege('postgrest_anon', c.oid, 'SELECT')
GROUP BY 1 ORDER BY 1;

-- 3) 막기로 한 것이 정말 막혔는가. 적용 후 blocked 는 전부 t 여야 한다.
--    개수만 세면 다른 표가 대신 열려도 21 이 맞아떨어진다 -- 이름으로 물어야 한다.
SELECT t AS must_be_blocked,
       NOT has_table_privilege('postgrest_anon', t, 'SELECT') AS blocked
FROM (VALUES
    ('trend_radar.review'), ('trend_radar.run'), ('trend_radar.run_source'), ('trend_radar.fetch_log'),
    ('tubedepth.comments'), ('tubedepth.transcripts'), ('tubedepth.jobs'), ('tubedepth.artifacts'),
    ('tubedepth.worker_control'), ('tubedepth.lane_health'), ('tubedepth.source_health'),
    ('tubedepth.flatten_progress'), ('tubedepth.alembic_version'), ('tubedepth.api_keys')
) v(t)
WHERE to_regclass(t) IS NOT NULL
ORDER BY 1;

-- 4) 미래 테이블이 자동으로 열리는 문. 적용 후 이 질의는 **0행**이어야 한다.
SELECT pg_get_userbyid(d.defaclrole) AS for_role,
       n.nspname AS schema,
       array_to_string(d.defaclacl, ' ') AS default_acl
FROM pg_default_acl d
JOIN pg_namespace n ON n.oid = d.defaclnamespace
WHERE d.defaclobjtype = 'r'
  AND n.nspname IN ('trend_radar', 'tubedepth')
  AND array_to_string(d.defaclacl, ' ') ~ '(postgrest_anon|trend_radar_reader)='
ORDER BY 1, 2;

-- 5) 멤버십. 적용 후 postgrest_anon 은 자기 자신 말고 어떤 롤에도 속하지 않아야 한다.
SELECT rolname AS postgrest_anon_is_member_of
FROM pg_roles WHERE pg_has_role('postgrest_anon', oid, 'USAGE') AND rolname <> 'postgrest_anon'
ORDER BY 1;
