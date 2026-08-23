-- origin: service/yt-scrapper/deploy/postgres-bootstrap.sql + service/stack/init/50-cosmai-bootstrap.sh (idempotent form)
-- reuse: psql -v schema=<name> -v database=<db> -v migrator_password="'...'" -v runtime_password="'...'" -f db/bootstrap.sql
--        Run once per schema by the database owner, never by the app, never from a migration. Safe to re-run.
--
-- Three roles, not one: owner (NOLOGIN, owns everything), migrator (deploy only, SET ROLE owner),
-- runtime (DML only). The database enforces the boundary; tests/test_runtime_role_cannot_ddl.py proves it.
-- Passwords are used only when a role is first created -- re-running must not rewrite a live credential.

SELECT format('CREATE ROLE %I NOLOGIN', :'schema' || '_owner')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'schema' || '_owner') \gexec

SELECT format('CREATE ROLE %I LOGIN NOINHERIT CONNECTION LIMIT 2 PASSWORD %L', :'schema' || '_migrator', :'migrator_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'schema' || '_migrator') \gexec

SELECT format('CREATE ROLE %I LOGIN NOINHERIT CONNECTION LIMIT 12 PASSWORD %L', :'schema' || '_runtime', :'runtime_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'schema' || '_runtime') \gexec

-- Idempotent: GRANT of an existing membership succeeds quietly.
SELECT format('GRANT %I TO %I', :'schema' || '_owner', :'schema' || '_migrator') \gexec

SELECT format('CREATE SCHEMA %I AUTHORIZATION %I', :'schema', :'schema' || '_owner')
WHERE NOT EXISTS (SELECT FROM pg_namespace WHERE nspname = :'schema') \gexec

SELECT format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', :'schema') \gexec
-- Without this the schema split is a naming convention, not a boundary.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Runtime: DML only. DEFAULT PRIVILEGES are not optional -- without them a table the NEXT migration
-- creates is unreachable by runtime, and it fails at the first request after the deploy.
SELECT format('GRANT USAGE ON SCHEMA %I TO %I', :'schema', :'schema' || '_runtime') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO %I', :'schema', :'schema' || '_runtime') \gexec
SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO %I', :'schema', :'schema' || '_runtime') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
              :'schema' || '_owner', :'schema', :'schema' || '_runtime') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT ON SEQUENCES TO %I',
              :'schema' || '_owner', :'schema', :'schema' || '_runtime') \gexec

-- search_path: runtime sees its schema; migrator too when migrations are schema-unqualified (pick ONE
-- strategy for the whole repo -- yt-scrapper and cosmai-old differed here).
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path = %I, pg_catalog', :'schema' || '_runtime', :'database', :'schema') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET search_path = %I, pg_catalog', :'schema' || '_migrator', :'database', :'schema') \gexec

-- Role-scoped limits, sized per statement not per job. lock_timeout < statement_timeout.
-- An open idle transaction blocks autovacuum database-wide, so idle_in_transaction is short.
SELECT format('ALTER ROLE %I IN DATABASE %I SET statement_timeout = ''30s''', :'schema' || '_runtime', :'database') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET lock_timeout = ''5s''', :'schema' || '_runtime', :'database') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET idle_in_transaction_session_timeout = ''15s''', :'schema' || '_runtime', :'database') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET transaction_timeout = ''60s''', :'schema' || '_runtime', :'database') \gexec

-- UTC on every connecting role: a timestamptz->timestamp cast under a non-UTC session shifted every
-- stored instant once (yt-scrapper Task 4). Migrator too -- it runs the casts.
SELECT format('ALTER ROLE %I IN DATABASE %I SET TimeZone = ''UTC''', :'schema' || '_runtime', :'database') \gexec
SELECT format('ALTER ROLE %I IN DATABASE %I SET TimeZone = ''UTC''', :'schema' || '_migrator', :'database') \gexec
