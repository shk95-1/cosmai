-- origin: #258, for the fork's re-identification sample (shk95/cosmai-import-ydc#92). The third
-- member of the db/bootstrap_*.sql family: bootstrap.sql is the generic three-role template keyed
-- on :schema, bootstrap_source.sql is the two collector schemas, and this is the one schema that
-- fits neither -- one reader role, no migrator, no runtime, owned by another schema's owner.
-- reuse: psql < db/bootstrap_needs_verify.sql, by the database owner, no parameters.
--        Run by db/migrate.sh step (a2), after (a) has made needs_owner. Safe to re-run.
--
-- Not db/grants/: that directory holds GRANT and REVOKE, and a file there creating a role and a
-- schema would be misfiled -- and it is applied after the DDL loop, which is exactly what this file
-- must not be (db/migrate.sh, step (a2)'s comment).
--
-- What the schema is for is who cannot read it: it maps author_hash back to channel_id, so no role
-- that reads `needs` may reach it -- neither the application's nor the screen's anonymous one, and
-- not PUBLIC. Nothing here alters a role, a grant or a default privilege that already exists.

-- NOLOGIN: the sample is read under SET ROLE by the one job that verifies it, never connected to.
SELECT 'CREATE ROLE needs_verify_reader NOLOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'needs_verify_reader') \gexec

-- AUTHORIZATION needs_owner is what lets the DDL loop's `SET ROLE needs_owner` create tables in
-- here: needs_owner has neither CREATE on the database nor CREATEROLE, so a migration cannot make
-- this schema or this role itself -- which is why they are made by the superuser, above the loop.
SELECT 'CREATE SCHEMA needs_verify AUTHORIZATION needs_owner'
WHERE NOT EXISTS (SELECT FROM pg_namespace WHERE nspname = 'needs_verify') \gexec

-- Without this the schema is a naming convention rather than a boundary: PUBLIC is every role on
-- the server, including the ones added after this file last ran.
REVOKE ALL ON SCHEMA needs_verify FROM PUBLIC;

GRANT USAGE ON SCHEMA needs_verify TO needs_verify_reader;
-- ON TABLES, and only SELECT: the reader verifies the sample, it does not write it. A default
-- privilege reaches objects created *after* it is set, so this line is worth nothing unless the
-- whole file runs before the DDL loop -- see step (a2) in db/migrate.sh.
ALTER DEFAULT PRIVILEGES FOR ROLE needs_owner IN SCHEMA needs_verify
    GRANT SELECT ON TABLES TO needs_verify_reader;
