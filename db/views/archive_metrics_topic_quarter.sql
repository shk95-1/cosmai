-- The archive lineage's quarterly metrics and nothing else (fork #94, decision #93 D0).
-- The archive is read, never recomputed, so its reading surface has to be pinned to one run -- a
-- plain SELECT over needs.metrics_topic_quarter answers with every lineage's rows at once, and the
-- live lineage's matcher is a different matcher (#93's Facts), so one series spanning both would
-- read the matcher change as a trend.
-- Which run that is comes from needs.archive_run (contracts/ddl/needs/030_corpus_lineage.sql), so
-- the note grammar that resolves it lives in one place rather than three.
-- db/migrate.sh re-applies this on every deploy. CREATE OR REPLACE only succeeds when the columns
-- stay the same, so DROP goes first.

DROP VIEW IF EXISTS needs.archive_metrics_topic_quarter;
CREATE VIEW needs.archive_metrics_topic_quarter AS
SELECT t.*
  FROM needs.metrics_topic_quarter t
 WHERE t.run_id = (SELECT run_id FROM needs.archive_run);

GRANT SELECT ON needs.archive_metrics_topic_quarter TO needs_runtime;
