-- 027: panel_channel refuses a second active version at commit (fork issue #34). Additive only
-- (tests/test_ddl_additive_only.py). 026 is taken by another issue in flight.
--
-- #31's loader swap (db/seed/panel.py) inserts a new version and then flips `active` off on every
-- other version inside one transaction -- under MVCC no outside reader ever sees both versions
-- active together. What that swap does not block is a hand `UPDATE panel_channel SET active = true`
-- that bypasses the loader (#34's Facts). A partial unique index on `(version, panel_role) WHERE
-- active` cannot express "at most one distinct version among the active rows" -- a partial index
-- constrains one row at a time, and this is a set condition over the whole table. Expressing it
-- with `EXCLUDE (version WITH <>) WHERE active` needs the btree_gist extension (#31's review). A
-- constraint trigger needs neither: it re-checks the set after every write.
--
-- The trigger must be DEFERRABLE INITIALLY DEFERRED, so the check runs at commit rather than at
-- the end of each statement (a constraint trigger is always AFTER ... FOR EACH ROW; deferral is
-- what moves it): the loader's legal swap passes through a mid-transaction state where two
-- versions are active at once (insert() defaults new rows to active = true, then activate() turns
-- the old version off in the same transaction) -- an immediate check would reject that swap even
-- though the transaction never lets an outside reader observe it. Deferring to commit lets the
-- loader's atomic swap through while still refusing a hand UPDATE that leaves two versions active
-- when the transaction actually commits.
CREATE FUNCTION needs.panel_channel_one_active_version() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  active_versions int;
BEGIN
  SELECT count(DISTINCT version) INTO active_versions FROM needs.panel_channel WHERE active;
  IF active_versions > 1 THEN
    RAISE EXCEPTION USING
      ERRCODE = 'check_violation',
      MESSAGE = 'needs.panel_channel has more than one active version';
  END IF;
  RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER panel_channel_one_active_version
  AFTER INSERT OR UPDATE ON needs.panel_channel
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION needs.panel_channel_one_active_version();

GRANT EXECUTE ON FUNCTION needs.panel_channel_one_active_version() TO needs_runtime;
