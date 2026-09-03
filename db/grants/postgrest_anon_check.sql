-- 읽기 전용. postgrest_anon 이 세 스키마에서 무엇을 보는지와 그것을 여는 경로를 찍는다.
-- postgrest_anon_old_stack.sql 적용 뒤 현행이 계약과 같은지 되묻는 것이 쓰임새다.
--
--   docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' cosmai-postgres \
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

-- 2) 스키마별 개수. 현행 기대는 needs 11 · trend_radar 9 · tubedepth 3 = 23 이다
--    (적용 전 실측 2026-08-27: needs 11 · trend_radar 13 · tubedepth 12 = 36).
--    needs 는 좁히기가 건드리지 않아 전후가 같다 -- #144 의 계보 뷰 둘을 포함해 11 이다.
--    **이 개수는 USAGE 를 모른다.** has_table_privilege 는 스키마 권한과 무관하게 t 를 내므로,
--    USAGE 가 없으면 여기서 23 이 맞아떨어져도 API 는 전부 401 이다. 절 6 을 함께 봐야 한다.
SELECT n.nspname AS schema,
       count(*) AS visible,
       CASE n.nspname WHEN 'needs' THEN 11 WHEN 'trend_radar' THEN 9 WHEN 'tubedepth' THEN 3 END AS expected,
       CASE n.nspname WHEN 'needs' THEN 11 WHEN 'trend_radar' THEN 13 WHEN 'tubedepth' THEN 12 END AS before_narrowing
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname IN ('trend_radar', 'tubedepth', 'needs')
  AND c.relkind IN ('r', 'v', 'm', 'p', 'f')
  AND has_table_privilege('postgrest_anon', c.oid, 'SELECT')
GROUP BY 1 ORDER BY 1;

-- 3) 막기로 한 것이 정말 막혔는가. 현행 blocked 는 전부 t 여야 한다.
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

-- 4) 미래 테이블이 자동으로 열리는 문. 현행 남아야 하는 것은 **trend_radar 한 행뿐**이다
--    (`trend_radar_reader=r`). 그 롤은 trend-radar-dashboard 가 직접 로그인하는 롤이라
--    기본권한을 살려 두고, anon 은 멤버가 아니게 되어 거기 닿지 못한다. tubedepth 행은
--    anon 에게 직접 걸려 있어 사라져야 한다 -- 적용 전 2행 -> 적용 후 1행.
SELECT pg_get_userbyid(d.defaclrole) AS for_role,
       n.nspname AS schema,
       array_to_string(d.defaclacl, ' ') AS default_acl,
       array_to_string(d.defaclacl, ' ') !~ 'postgrest_anon=' AS ok_after
FROM pg_default_acl d
JOIN pg_namespace n ON n.oid = d.defaclnamespace
WHERE d.defaclobjtype = 'r'
  AND n.nspname IN ('trend_radar', 'tubedepth')
  AND array_to_string(d.defaclacl, ' ') ~ '(postgrest_anon|trend_radar_reader)='
ORDER BY 1, 2;

-- 5) 멤버십. 현행 postgrest_anon 은 자기 자신 말고 어떤 롤에도 속하지 않아야 한다.
SELECT rolname AS postgrest_anon_is_member_of
FROM pg_roles WHERE pg_has_role('postgrest_anon', oid, 'USAGE') AND rolname <> 'postgrest_anon'
ORDER BY 1;

-- 6) 스키마 USAGE. **세 스키마 모두 usable 이 t 여야 한다** -- 적용 전에도, 후에도.
--    표를 아무리 GRANT 해도 USAGE 가 없으면 그 스키마는 0개와 같다(PostgREST 는 401).
--    이것을 안 재서 2026-08-27 적용 직후 trend_radar 9개가 전부 401 이었다: anon 은 USAGE 도
--    trend_radar_reader 멤버십으로 물려받고 있었고, 멤버십을 끊자 SELECT 와 함께 사라졌다.
--    direct 열이 갈라 준다 -- trend_radar 만 f 에서 t 로 바뀌어야 하는 칸이다.
SELECT n.nspname AS schema,
       has_schema_privilege('postgrest_anon', n.nspname, 'USAGE') AS usable,
       coalesce(array_to_string(n.nspacl, ' ') ~ 'postgrest_anon=[^/]*U', false) AS direct
FROM pg_namespace n
WHERE n.nspname IN ('trend_radar', 'tubedepth', 'needs')
ORDER BY 1;

-- 7) 컬럼 층과 PUBLIC. 권한이 오는 **경로**가 셋이고(직접 GRANT · 롤 멤버십 · DEFAULT
--    PRIVILEGES) 그것이 걸리는 **층**도 셋이다(스키마 · 표 · 컬럼). 위 절들은 앞의 두 층만
--    재므로 컬럼 GRANT 하나가 표 권한 없이 열려 있으면 아무 절도 울지 않는다. PUBLIC 도 같다 --
--    grantee 가 롤 이름이 아니라서 postgrest_anon 을 물어보는 어떤 질의에도 안 잡힌다.
--    (#168 의 일반형: 권한을 재되 그 권한이 어느 경로로 어느 층에 오는지는 안 잰다.)

-- 7a) 컬럼 단위 SELECT 의 총량. **기대값으로 박지 않는다** -- 표 권한이 줄면 이 숫자도 같이
--     준다(2026-08-27 좁히기 후 실측 259). 불변식은 7b 다.
SELECT count(*) AS column_level_select_rows
FROM information_schema.column_privileges
WHERE grantee = 'postgrest_anon' AND privilege_type = 'SELECT'
  AND table_schema IN ('trend_radar', 'tubedepth', 'needs');

-- 7b) 그중 **표 단위 SELECT 로 설명되지 않는 것. 0행이어야 한다.**
--     information_schema.column_privileges 는 표 GRANT 의 그림자 행도 함께 내므로, 위 총량은
--     대부분 그림자다. 표 권한 없이 컬럼만 열린 자리가 있다면 그것만 여기 남는다.
SELECT table_schema, table_name, column_name
FROM information_schema.column_privileges
WHERE grantee = 'postgrest_anon' AND privilege_type = 'SELECT'
  AND table_schema IN ('trend_radar', 'tubedepth', 'needs')
  AND NOT has_table_privilege('postgrest_anon', format('%I.%I', table_schema, table_name)::regclass, 'SELECT')
ORDER BY 1, 2, 3;

-- 7c) PUBLIC 에 걸린 SELECT. **0행이어야 한다.** PUBLIC 은 aclexplode 에서 grantee = 0 이고,
--     걸려 있으면 anon 뿐 아니라 이 database 에 붙는 모든 롤이 읽는다.
SELECT n.nspname AS schema, c.relname AS relation
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace,
     LATERAL aclexplode(c.relacl) a
WHERE n.nspname IN ('trend_radar', 'tubedepth', 'needs')
  AND a.grantee = 0 AND a.privilege_type = 'SELECT'
ORDER BY 1, 2;
