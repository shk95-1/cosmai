-- 010: the launch-evidence ledger (#282). Additive only (tests/test_ddl_additive_only.py).
--
-- "New product" means launch time (user decision 2026-09-20, #125). No single source decides it:
-- several observation axes each make a claim about when the product launched, and the conclusion
-- keeps the claims that made it. This file is the ledger of those claims and nothing else -- the
-- launch interval is the view db/views/product_launch.sql and the verdict is a rule table in
-- analysis/launch, so **a rule change is never a migration**, which is the whole reason the
-- derivation is not a column here.
--
-- What is deliberately absent: a score, a weight, a confidence and a resolved launch date. The
-- rule table answers with a tier or with `unknown`/`conflict`, and a number that ranks two pieces
-- of evidence against each other is the design this issue was told to avoid.
--
-- Nothing writes this table yet; the first-round axes are #283.
--
-- The number 010 is in upstream's block (006~019, contracts/versioning.md; the fork holds 020~),
-- and 001..009 are already in the production ledger needs.schema_migration, so changing a number
-- or editing an earlier file makes it try to apply again. db/migrate.sh step (c) applies this file
-- inside one transaction under SET ROLE needs_owner, so the table is owned by needs_owner like
-- every other table in this schema and no ownership statement belongs here.

CREATE TABLE needs.product_launch_evidence (
  -- The canonical product, not a site listing. The verdict is read on the same axis
  -- needs.metrics_need.product_ref carries (#128), and an axis that observes one site resolves its
  -- observation through needs.product_member before writing the claim -- which site row it saw is
  -- kept in source_ref below, so nothing is lost by folding. The FK is what keeps a claim from
  -- naming a product the catalogue does not hold; the linker only ever upserts product_ref rows
  -- (analysis/linker/pipeline.py REF_SQL), so no claim is orphaned by a re-clustering.
  product_ref       text        NOT NULL REFERENCES needs.product_ref,
  -- Which observation this is. The vocabulary belongs to the axes issue (#283) rather than to this
  -- file, so there is no CHECK on it: a new axis is a new value, and adding one must not be a
  -- migration. Empty is refused because an empty axis name would fold two axes into one key.
  axis              text        NOT NULL CHECK (axis <> ''),
  -- What the claim says about the launch, and this one IS closed: a bound on a point in time is
  -- lower, upper or exact, and a fourth value would not be a direction. Widening it would need a
  -- human-approved DROP CONSTRAINT (pre-approval 2), which is the right price for changing what a
  -- claim can mean.
  --   not_before  the launch cannot precede it (an MFDS report date, a vendor new-product board)
  --   not_after   the launch is no later than it (an old held review, a DataLab onset)
  --   at          both at once
  direction         text        NOT NULL CHECK (direction IN ('not_before', 'not_after', 'at')),
  -- The instant claimed, at the precision the axis actually has. MFDS files on a day; DataLab
  -- answers by month. A month claim is widened to its whole month by the reader (the view and
  -- analysis.launch.claim_edges both), never by the axis -- storing an already-widened pair would
  -- put that rule in as many places as there are axes.
  claimed_on        date        NOT NULL,
  -- Closed too, and for a reason the additive-only rule does not usually justify: a precision the
  -- reader does not understand would drop out of the interval in silence, and a narrower interval
  -- built out of less evidence is the one direction this rule table may not move in. A third
  -- precision is a decision, not an insert.
  claimed_precision text        NOT NULL CHECK (claimed_precision IN ('day', 'month')),
  -- The source row this claim was read off, in the axis's own spelling (an MFDS report_seq, a
  -- DataLab request_key, a site's product key). It is in the natural key, so a product keeps every
  -- registration an axis matched rather than the last one -- and when those claims cross, the
  -- `conflict` verdict can be traced back to the exact registration that caused it, which is the
  -- measured trap of 2026-09-20 (a later variant matched to an older line). An empty value would
  -- collapse an axis's claims into one and take that trace with it.
  source_ref        text        NOT NULL CHECK (source_ref <> ''),
  -- The axis implementation's version (contracts/versioning.md's `rule-vX.Y`). Not in the key: a
  -- claim is a statement about one source row, and a new axis version restates that same row --
  -- two rows would both be live with nothing to say which is current.
  axis_version      text        NOT NULL,
  observed_at       timestamptz NOT NULL,
  note              text,
  PRIMARY KEY (product_ref, axis, source_ref)
);
-- "What does this axis reach, and how well" -- the coverage question #283 owes per axis, and the
-- one query this table is asked that does not start from a product.
CREATE INDEX ON needs.product_launch_evidence (axis);

-- The axes run as needs_runtime, and they re-state their claims (INSERT ... ON CONFLICT DO UPDATE)
-- rather than appending, so UPDATE is part of writing this table rather than a repair path. DELETE
-- is what withdrawing an axis's claims is. db/bootstrap.sql's DEFAULT PRIVILEGES already grant all
-- four to needs_runtime for anything needs_owner creates; this line is here so the table says who
-- writes it, the same way 009 and 028 do.
GRANT SELECT, INSERT, UPDATE, DELETE ON needs.product_launch_evidence TO needs_runtime;
-- Not granted to postgrest_anon: contracts/anon_exposure.md's `needs` section is a whitelist, so a
-- relation nobody names is closed, and no screen reads this one.
