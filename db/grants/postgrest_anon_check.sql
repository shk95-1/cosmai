-- Read-only. Prints what postgrest_anon can see across the three schemas and the path that opened it.
-- The use case is asking, after applying postgrest_anon_old_stack.sql, whether the current state
-- matches the contract.
--
--   docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' cosmai-postgres \
--     psql -U platform -d app -X < db/grants/postgrest_anon_check.sql
--
-- This must run inside a read-only session -- if anything other than SELECT ever got mixed into this
-- file, it would die right there. That is this file's own safeguard, and
-- tests/test_anon_exposure_contract.py protects that property.

\pset pager off

-- 1) Every visible relation and its path. 'direct' means relacl carries a postgrest_anon entry, and
--    'trend_radar_reader' means it is visible with no such entry -- meaning it was inherited through
--    role membership.
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

-- 2) Count per schema. Currently expected: needs 11, trend_radar 9, tubedepth 3 = 23
--    (measured before applying, 2026-08-27: needs 11, trend_radar 13, tubedepth 12 = 36).
--    needs is untouched by the narrowing, so before and after are the same -- 11, including #144's
--    two lineage views.
--    **This count knows nothing about USAGE.** has_table_privilege returns t regardless of schema
--    privilege, so even if 23 checks out here, the API returns 401 across the board without USAGE.
--    This has to be read together with section 6.
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

-- 3) Whether what was decided to be blocked really is blocked. blocked must currently be t across the
--    board.
--    Counting alone would still check out at 21 even if a different table opened up in its place --
--    this has to be asked by name.
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

-- 4) The door that lets a future table open automatically. What must currently remain is **exactly
--    one trend_radar row** (`trend_radar_reader=r`). That role is the one trend-radar-dashboard logs
--    into directly, so its default privileges are kept alive, and anon can no longer reach it since it
--    stops being a member. tubedepth's row is granted to anon directly and must disappear -- 2 rows
--    before applying -> 1 row after.
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

-- 5) Membership. postgrest_anon must currently belong to no role but itself.
SELECT rolname AS postgrest_anon_is_member_of
FROM pg_roles WHERE pg_has_role('postgrest_anon', oid, 'USAGE') AND rolname <> 'postgrest_anon'
ORDER BY 1;

-- 6) Schema USAGE. **All three schemas must have usable = t** -- both before and after applying.
--    No matter how many tables are granted, a schema without USAGE is the same as having zero
--    (PostgREST returns 401).
--    Missing this meant trend_radar's 9 tables were all returning 401 right after applying on
--    2026-08-27: anon had also inherited USAGE through trend_radar_reader membership, and cutting that
--    membership took it away along with SELECT.
--    The direct column is what tells the two apart -- trend_radar is the only cell that must flip from
--    f to t.
SELECT n.nspname AS schema,
       has_schema_privilege('postgrest_anon', n.nspname, 'USAGE') AS usable,
       coalesce(array_to_string(n.nspacl, ' ') ~ 'postgrest_anon=[^/]*U', false) AS direct
FROM pg_namespace n
WHERE n.nspname IN ('trend_radar', 'tubedepth', 'needs')
ORDER BY 1;

-- 7) The column layer and PUBLIC. There are three **paths** a privilege can arrive by (direct GRANT,
--    role membership, DEFAULT PRIVILEGES) and three **layers** it can land on (schema, table, column).
--    The sections above only measure the first two layers, so a single column GRANT sitting open with
--    no table privilege would trip no section at all. PUBLIC is the same case -- its grantee is not a
--    role name, so no query that asks about postgrest_anon ever catches it.
--    (The general shape of #168: measuring that a privilege exists without measuring which path and
--    which layer it arrived through.)

-- 7a) The total row count of column-level SELECT. **Not pinned to an expected value** -- this number
--     shrinks along with table privilege (measured 259 after the 2026-08-27 narrowing). The invariant
--     is 7b.
SELECT count(*) AS column_level_select_rows
FROM information_schema.column_privileges
WHERE grantee = 'postgrest_anon' AND privilege_type = 'SELECT'
  AND table_schema IN ('trend_radar', 'tubedepth', 'needs');

-- 7b) The part of that **not explained by table-level SELECT. Must be 0 rows.**
--     information_schema.column_privileges also emits a shadow row for every table GRANT, so most of
--     the total above is just shadow. If a spot exists where only a column is open with no table
--     privilege, this is the only place it shows up.
SELECT table_schema, table_name, column_name
FROM information_schema.column_privileges
WHERE grantee = 'postgrest_anon' AND privilege_type = 'SELECT'
  AND table_schema IN ('trend_radar', 'tubedepth', 'needs')
  AND NOT has_table_privilege('postgrest_anon', format('%I.%I', table_schema, table_name)::regclass, 'SELECT')
ORDER BY 1, 2, 3;

-- 7c) SELECT granted to PUBLIC. **Must be 0 rows.** PUBLIC shows up as grantee = 0 in aclexplode, and
--     if it is set, every role that connects to this database reads it, not just anon.
SELECT n.nspname AS schema, c.relname AS relation
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace,
     LATERAL aclexplode(c.relacl) a
WHERE n.nspname IN ('trend_radar', 'tubedepth', 'needs')
  AND a.grantee = 0 AND a.privilege_type = 'SELECT'
ORDER BY 1, 2;
