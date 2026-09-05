-- origin: db/bootstrap.sql, narrowed to the two schemas the collectors own -- trend_radar
-- (collectors/commerce) and tubedepth (collectors/youtube). The old repos' init scripts made them
-- and those repos are archived, so this file is what stands them up on an empty database (#178).
-- reuse: psql -v schema=<name> -v reader=<role name or ''> -v runtime_limit=<n or ''> and, from
--        stdin, `\set runtime_password` / `\set reader_password` -- a password must never reach an
--        argument, where the host's `ps` reads it for the length of the call (#20).
--        Run once per schema by the database owner. Safe to re-run.
--
-- Two roles, not three: owner (NOLOGIN, owns everything) and runtime (DML only). There is no
-- migrator, because these schemas have no ledger of their own -- the dump is their baseline and
-- contracts/ddl/<schema>/NNN_*.sql are added on top of it by db/migrate.sh step (0), as the
-- superuser under SET ROLE <schema>_owner. trend_radar carries a third, `reader`: the role
-- trend-radar-dashboard logs in with and the one the schema's default privileges name
-- (contracts/anon_exposure.md, measured 2026-08-27).
-- Passwords are used only when a role is first created -- re-running must not rewrite a live credential.

SELECT format('CREATE ROLE %I NOLOGIN', :'schema' || '_owner')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'schema' || '_owner') \gexec

-- CONNECTION LIMIT comes in as a parameter rather than as a constant: 8 is trend_radar_runtime's
-- production value, read out of pg_db_role_setting on 2026-08-24 and depended on by
-- collectors/commerce/storage/db.py's ROLE_CONNECTION_LIMIT. tubedepth_runtime's was never
-- measured, so it is passed empty and this repo declines to invent one.
SELECT format('CREATE ROLE %I LOGIN NOINHERIT PASSWORD %L', :'schema' || '_runtime', :'runtime_password')
    -- No ::int cast: an unused CASE branch is still constant-folded, so a cast there would fail on
    -- the empty value it exists to skip. The regexp is the validation instead.
    || CASE WHEN :'runtime_limit' ~ '^[0-9]+$' THEN ' CONNECTION LIMIT ' || :'runtime_limit' ELSE '' END
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'schema' || '_runtime') \gexec

SELECT format('CREATE ROLE %I LOGIN NOINHERIT PASSWORD %L', :'reader', :'reader_password')
WHERE :'reader' <> '' AND NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'reader') \gexec

SELECT format('CREATE SCHEMA %I AUTHORIZATION %I', :'schema', :'schema' || '_owner')
WHERE NOT EXISTS (SELECT FROM pg_namespace WHERE nspname = :'schema') \gexec

SELECT format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', :'schema') \gexec

-- Runtime: DML only. The DEFAULT PRIVILEGES matter more here than in db/bootstrap.sql -- every table
-- of these schemas is created after this file runs (the dump is applied next), so without them the
-- runtime role would reach none of them.
SELECT format('GRANT USAGE ON SCHEMA %I TO %I', :'schema', :'schema' || '_runtime') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO %I', :'schema', :'schema' || '_runtime') \gexec
SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I', :'schema', :'schema' || '_runtime') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
              :'schema' || '_owner', :'schema', :'schema' || '_runtime') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO %I',
              :'schema' || '_owner', :'schema', :'schema' || '_runtime') \gexec

-- Reader: SELECT only, and by default privileges as well as on today's tables -- production's
-- pg_default_acl carries exactly one row for trend_radar and its beneficiary is this role, not
-- postgrest_anon (contracts/anon_exposure.md, user decision 2). anon gets nothing here: the one
-- door it goes through is db/grants/postgrest_anon_old_stack.sql, which narrows rather than opens.
SELECT format('GRANT USAGE ON SCHEMA %I TO %I', :'schema', :'reader') WHERE :'reader' <> '' \gexec
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', :'schema', :'reader') WHERE :'reader' <> '' \gexec
SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I', :'schema', :'reader') WHERE :'reader' <> '' \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON TABLES TO %I',
              :'schema' || '_owner', :'schema', :'reader') WHERE :'reader' <> '' \gexec

-- The four role-scoped limits production carries on trend_radar_runtime, read out of
-- pg_db_role_setting on 2026-08-24 (collectors/commerce/storage/locks.py records that reading).
-- idle_session_timeout stays unset: the lock connection sits `idle` for a whole walk, and setting it
-- ends the walk's lock in the middle -- tests/collectors/commerce/test_source_lock.py measures that.
SELECT format('ALTER ROLE %I IN DATABASE %I SET statement_timeout = ''30s''', :'schema' || '_runtime', :'database') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET lock_timeout = ''5s''', :'schema' || '_runtime', :'database') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET idle_in_transaction_session_timeout = ''15s''', :'schema' || '_runtime', :'database') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET transaction_timeout = ''60s''', :'schema' || '_runtime', :'database') \gexec
