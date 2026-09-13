-- The archive lineage's evidence rows and nothing else (fork #94, decision #93 D0).
-- The third of the fixed archive surfaces. Evidence points at corpus_document by FK (025), and a
-- document belongs to one snapshot, so a row of another run's evidence reaching this surface would
-- point at documents outside the archive snapshot entirely.
-- The run comes from needs.archive_run, the same as the other two.
-- db/migrate.sh re-applies this on every deploy. CREATE OR REPLACE only succeeds when the columns
-- stay the same, so DROP goes first.

DROP VIEW IF EXISTS needs.archive_topic_quarter_evidence;
CREATE VIEW needs.archive_topic_quarter_evidence AS
SELECT t.*
  FROM needs.topic_quarter_evidence t
 WHERE t.run_id = (SELECT run_id FROM needs.archive_run);

GRANT SELECT ON needs.archive_topic_quarter_evidence TO needs_runtime;
