-- 025: the evidence comments attached to a verdict cell (fork issue #6). Additive only
-- (tests/test_ddl_additive_only.py).
--
-- ydc evidence_comments.py drops per-topic evidence comments into a CSV and cards.py matches that
-- against the verdict CSV by hand. This table is that match: the eight columns are
-- needs.topic_quarter_judgement's primary key as they stand, so an evidence row cannot stand
-- without the verdict cell it supports, and the path from cell to evidence is one FK.
--
-- For the same reason as 024 its name does not start with metrics_ and it has no line in the
-- "canonical table per aggregate grain" table of contracts/formats.md §Time -- it has not one counting
-- column. One thing does differ from 024: a verdict is a derivation that emits one row from one metric
-- row, while evidence is a **pointer back at the documents that made that cell**.
CREATE TABLE needs.topic_quarter_evidence (
  -- The eight columns are topic_quarter_judgement's primary key as it stands. Pointing at the
  -- verdict table rather than metrics_topic_quarter is meaning -- evidence is asked for by whoever
  -- reads the verdict, not by whoever reads the metrics.
  run_id        bigint NOT NULL,
  scope         text NOT NULL,
  topic_key     text NOT NULL,
  quarter       text NOT NULL,
  source        text NOT NULL,
  content_type  text NOT NULL,
  panel_version int  NOT NULL,
  panel_role    text NOT NULL,
  -- The like-count descending slot inside that cell. The ceiling (how many per cell) is not written
  -- here -- that number is a handle of the report and moves, and the DDL is additive only so a CHECK
  -- cannot be taken back (contracts/interfaces.md §Evidence is that place).
  rank          int  NOT NULL CHECK (rank >= 1),
  -- The evidence body is not copied here. The corpus is canonical, and a copy makes it unknowable
  -- which side is the original (the same sentence as analysis/retrieval/corpus.py keeping no second
  -- set of CSVs). It points at the document instead, and the view
  -- needs.topic_quarter_evidence_quote joins cell to original text in one line.
  snapshot_id   int  NOT NULL,
  doc_id        text NOT NULL,
  -- Evidence comes only from documents that cell's source produced. Since doc_id is
  -- source || ':' || source_item_id (023's generated column), the rule stands inside a single row --
  -- attaching a comment as evidence for a video cell is blocked here.
  CHECK (split_part(doc_id, ':', 1) = source),
  -- Why it was chosen stays on the row. A like count is a snapshot as of collected_at
  -- (interfaces.md §Limitations of the population), so counting later gives a different number -- and
  -- then this stored value is the only thing that can explain this ordering.
  like_count    int  NOT NULL CHECK (like_count >= 0),
  -- The expression by which this document matched that topic. corpus_mention already recorded it
  -- and nothing is matched again here.
  matched_term  text,
  picked_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role, rank),
  -- If one comment takes two slots in a cell, the evidence set is really one. Uniqueness
  -- on the slot (rank) alone cannot stop that.
  UNIQUE (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role, doc_id),
  FOREIGN KEY (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role)
    REFERENCES needs.topic_quarter_judgement,
  FOREIGN KEY (snapshot_id, doc_id) REFERENCES needs.corpus_document (snapshot_id, doc_id)
);
-- The screen asks for "this quarter's consumer speech on this topic", not "this run's" (the same
-- reason as 024 and 022).
CREATE INDEX ON needs.topic_quarter_evidence (topic_key, quarter);

GRANT SELECT, INSERT, UPDATE, DELETE ON needs.topic_quarter_evidence TO needs_runtime;
