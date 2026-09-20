-- 029: the verification sample that maps an author hash back to the channel id it was made from
-- (fork issue #92, from fork #91 decision 3). Additive only (tests/test_ddl_additive_only.py).
--
-- The one reason these rows exist: to prove that db/corpus/author.py's
-- sha256('youtube:' || channel_id)[:24] is the function ydc's collector already used before the
-- 2026-08-19 handover, and that the retroactive pass over tubedepth.comments reproduces it. Tens of
-- rows, loaded once by the coordinator and read once. Nothing in the pipeline reads this table and
-- no module imports it -- a working store would be a second place the raw identifier lives, which
-- is the thing #91 decided against.
--
-- It is a re-identification table, and that is why it is the only object this repo puts outside
-- `needs`. The schema and its reader role are made by db/bootstrap_needs_verify.sql at
-- db/migrate.sh step (a2), above the DDL loop, because needs_owner has neither CREATE on the
-- database nor CREATEROLE (upstream shk95-1/cosmai#258).
--
-- So this file creates a table and grants nothing. needs_verify_reader's SELECT arrives through
-- that bootstrap file's ALTER DEFAULT PRIVILEGES, which reaches objects created after it runs --
-- this table is one of them. A GRANT written here would be a second grant path for the same
-- privilege, and the two would drift; a GRANT to any role that reads `needs` would undo the
-- boundary the schema exists to draw.
--
-- Retention. The rows are destroyed within 30 days of the retroactive hash pass being verified.
-- Destruction is the schema and the role, not a DELETE:
--     DROP SCHEMA needs_verify CASCADE; DROP ROLE needs_verify_reader;
-- It is a coordinator step, run from the upstream checkout against production (#192 D7,
-- `STATE.md` §3), and its date is recorded on fork issue #92. Written as a step with an owner
-- rather than as a trigger or a job: a scheduled deletion that stops firing is a retention limit
-- nobody can tell has lapsed, while a step that is owed shows up as an open issue.
--
-- CREATE TABLE IF NOT EXISTS, which no neighbouring file here needs. db/migrate.sh applies each
-- file once and needs.schema_migration keeps it that way, but tests/conftest.py's `needs_schema`
-- fixture re-applies every contracts/ddl/needs/*.sql into a per-test schema by rewriting `needs.`
-- to that schema's name. A table in needs_verify is not rewritten by that substitution, so it is
-- the one real table every time; without this clause the second test to build a schema fails with
-- "relation already exists".
CREATE TABLE IF NOT EXISTS needs_verify.author_sample (
  -- The stored value, exactly as needs.corpus_document.source_metadata carries it: 24 lower-case
  -- hex characters. It is the key because one commenter is one row -- a second capture of the same
  -- author proves nothing the first did not, and the sample is tens of rows by design.
  author_hash text NOT NULL,
  -- The raw identifier, and the only reason this schema is closed: beside the column above it is
  -- exactly the re-identification the hash is meant to cost. No other table in this repository
  -- stores it.
  channel_id  text NOT NULL,
  captured_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (author_hash)
);

-- The comments are on the objects and not only in this file: the coordinator who runs the
-- destruction step reads the database, not the checkout, and a schema found later with no
-- explanation attached is a schema nobody dares remove.
COMMENT ON TABLE needs_verify.author_sample IS
    'Verification sample for the author hash (fork issue #92): tens of rows mapping author_hash '
    'back to the channel id it was made from. Re-identifying by construction: '
    'needs_verify_reader is the only role given SELECT. needs_owner owns it, and the deploy login '
    'that can SET ROLE to needs_owner reaches it too, so it is closed against the application and '
    'the screen, not against the deploy (contracts/anon_exposure.md). Destroyed with the schema and the role within 30 days of the '
    'retroactive hash pass being verified; the statements are in '
    'contracts/ddl/needs/029_author_verify.sql.';
COMMENT ON COLUMN needs_verify.author_sample.author_hash IS
    'sha256(''youtube:'' || channel_id) truncated to 24 lower-case hex characters, the value '
    'needs.corpus_document.source_metadata carries (db/corpus/author.py).';
COMMENT ON COLUMN needs_verify.author_sample.channel_id IS
    'The raw YouTube channel identifier the hash was made from. Kept nowhere else.';
COMMENT ON COLUMN needs_verify.author_sample.captured_at IS
    'When the row was loaded -- what the 30-day retention limit is counted from.';
