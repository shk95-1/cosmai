-- 028: the MFDS cosmetic registration ledger as a reference table (fork issue #55). Additive only
-- (tests/test_ddl_additive_only.py).
--
-- cosmai#73 decided on 2026-08-26 that ydc's external ingredient CSV is not imported, because the
-- full ingredient list is something the collector gathers. This ledger is different in kind: it is
-- not an observation of a site, it is the **official filing record** -- an authority we cross-check
-- against and could not re-collect by scraping. That is why it gets a table and the ingredient CSV
-- did not.
--
-- What a rerun does **not** do, since both of the loader's INSERTs are ON CONFLICT DO NOTHING: a
-- filing whose values changed under the same report_seq is neither re-entered nor updated. That is a
-- written-down assumption, not a guarantee -- this table takes it that MFDS does not re-file under a
-- report number it has already used. If that ever breaks, the repair is a new snapshot rather than a
-- rerun, and the loader refuses to load a file whose measured row count or newest report date differs
-- from the stored snapshot row precisely so that the merge cannot happen quietly.
--
-- The number 028 is in the fork's block (020~, contracts/versioning.md); 001..027 are already in the
-- production ledger needs.schema_migration, so changing a number or editing an earlier file makes it
-- try to apply again.

-- ---------- the snapshot ----------
-- A one-row parent table rather than a `snapshot_label`/`loaded_at` pair of columns on the ledger,
-- for the reason issue #55 work item 3 exists: this copy is **not updated**, and "not updated" is a
-- statement about the load (which tag it came from, how many rows, the newest report date it
-- reaches), not about a registration. Written per row it would be repeated 4,735 times with nothing
-- forcing the copies to agree; written here it is one row an FK can point at, and the shape is the
-- one 022/023 already use.
CREATE TABLE needs.mfds_snapshot (
  snapshot_id     int  NOT NULL,
  label           text NOT NULL UNIQUE,       -- mfds-ydc-v0.4.0
  source_tag      text NOT NULL,              -- the origin as a name a person can go back to: ydc v0.4.0 (76db718)
  source_file     text NOT NULL,              -- eval/mfds/mfds_items_v1.csv
  -- Measured off the file by the loader, not typed in. "not updated" is only a real statement if the
  -- boundary it names is the file's own boundary.
  source_rows     int  NOT NULL CHECK (source_rows > 0),
  max_report_date date NOT NULL,              -- the newest filing this copy reaches; the ledger itself keeps growing past it
  -- What happens next: 'not_updated' today. No CHECK, deliberately -- the vocabulary is the
  -- contract's to grow (contracts/formats.md) and DDL here is additive only, so a CHECK written now
  -- could not be widened later without a human-approved DROP CONSTRAINT (the same reasoning as 025's
  -- rank bound).
  update_policy   text NOT NULL,
  note            text,
  loaded_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_id)
);

-- ---------- the ledger ----------
-- 023 puts snapshot_id in front of the natural key so that a re-observation does not overwrite an
-- observation. **This table does the opposite on purpose.** A YouTube view count is a value as of a
-- moment, so two observations of one video are two facts; a filed registration is closed history --
-- report_seq is MFDS's own identifier and the same number always means the same filing. So report_seq
-- alone is the key, a second snapshot cannot enter the same registration twice, and snapshot_id says
-- which load first carried the row.
CREATE TABLE needs.mfds_registration (
  report_seq  bigint NOT NULL,                -- COSMETIC_REPORT_SEQ, MFDS's own 10-digit report number
  item_name   text   NOT NULL,                -- ITEM_NAME: the registered product name, not a retail listing name
  entp_name   text   NOT NULL,                -- ENTP_NAME: the reporting company (often a contract manufacturer, not the brand)
  report_date date   NOT NULL,
  -- The join surface, kept on the row. Measured on production before it was chosen (#55 work item 1,
  -- `uv run tool/measure-mfds-join`, measured 2026-09-04): normalised item_name against
  -- trend_radar.product.name joins **0** products, because a registered name is a legal name and a
  -- listing name is marketing copy; normalised entp_name against
  -- needs.entity_lexicon(kind='brand').surface joins 233 of these 4,735 rows on 40 brands. Counts of
  -- the commerce side are not repeated here -- that table grows every day and a number frozen in a
  -- migration is wrong by the next collection; the tool is where the live count lives.
  -- The value is the corporate form removed and the rest folded -- db/seed/mfds.py normalize_company
  -- is the single implementation, and it is filled by the loader rather than GENERATED because the
  -- folding needs NFKC plus a corporate-form vocabulary, and a second copy of that in SQL is exactly
  -- the drift 023's generated doc_id was avoiding. An empty key would join every other empty key, so
  -- the CHECK refuses one; db/seed/mfds.py `rows` refuses the same row earlier and can name the
  -- company.
  entp_key    text   NOT NULL CHECK (entp_key <> ''),
  snapshot_id int    NOT NULL REFERENCES needs.mfds_snapshot,
  PRIMARY KEY (report_seq)
);
-- The join the contract names, and the only path into this table that is not a report number:
-- entity_lexicon surface -> these rows. Nothing reads it yet -- the answer layer is what will, and the
-- index is here because adding one later is additive but a sequential scan over a growing reference
-- table is the kind of thing nobody goes back to fix.
CREATE INDEX ON needs.mfds_registration (entp_key);
-- "What was filed in this window" -- the question a growing ledger is asked, and the one that shows
-- how far behind this copy has fallen.
CREATE INDEX ON needs.mfds_registration (report_date);

-- The seed runs as needs_runtime (db/seed/__main__.py takes db.runtime.runtime_url). All four, as in
-- 022/023/025, because entp_key is **stored** rather than generated: a change to
-- db/seed/mfds.py normalize_company leaves every loaded row on the old folding, and a rerun of
-- `uv run python -m db.seed --only mfds` cannot repair it -- both INSERTs are ON CONFLICT DO NOTHING,
-- so a rerun touches nothing. The repair path is db/seed/mfds.py `rekey`, which recomputes the column
-- for every row and needs UPDATE. Without that grant the only repair would be a hand statement by the
-- coordinator against production, which is the thing this schema is trying not to require.
GRANT SELECT, INSERT, UPDATE, DELETE
  ON needs.mfds_snapshot, needs.mfds_registration TO needs_runtime;
