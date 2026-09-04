-- #141: what feeds what. Additive only (tests/test_ddl_additive_only.py).
--
-- 007's pipeline_stage is a *list* of stages and nothing more, with no relations. Neither the
-- diagram (#142) nor
-- state propagation (#143) nor lineage tracing (#144) can stand without them.
--
-- No separate node table. pipeline_stage.stage_key already declares the stages, and for stores the DB itself is
-- the registry -- the key is a normalised table name and tests/test_pipeline_edge.py asks
-- to_regclass whether it
-- exists. A node table would write the same fact in two places.
--
-- Both directions are held: stage -> store writes, store -> stage reads. With one direction only, lineage flows
-- one way alone and #144 cannot ride back from a metric to what was collected.
CREATE TABLE needs.pipeline_edge (
  from_key   text NOT NULL,
  from_kind  text NOT NULL,
  to_key     text NOT NULL,
  to_kind    text NOT NULL,
  note       text NOT NULL DEFAULT '',
  PRIMARY KEY (from_key, to_key),
  CONSTRAINT pipeline_edge_from_kind_check CHECK (from_kind IN ('stage', 'store')),
  CONSTRAINT pipeline_edge_to_kind_check   CHECK (to_kind IN ('stage', 'store')),
  -- Stages are never joined directly. Between two stages there is always the table one left
  -- behind, and skipping
  -- that table costs the lineage its "by way of".
  CONSTRAINT pipeline_edge_not_stage_to_stage CHECK (NOT (from_kind = 'stage' AND to_kind = 'stage'))
);
