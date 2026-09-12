-- The archive lineage's verdicts and nothing else (fork #94, decision #93 D0).
-- The sibling of db/views/archive_metrics_topic_quarter.sql: one run's rows, that run resolved from
-- the snapshot marked 'archive' by needs.archive_run rather than written down here. A verdict is a
-- derivation of a metric row (024), so the two views must answer for the same run or the archive's
-- type distribution stops being the archive's.
-- db/migrate.sh re-applies this on every deploy. CREATE OR REPLACE only succeeds when the columns
-- stay the same, so DROP goes first.

DROP VIEW IF EXISTS needs.archive_topic_quarter_judgement;
CREATE VIEW needs.archive_topic_quarter_judgement AS
SELECT t.*
  FROM needs.topic_quarter_judgement t
 WHERE t.run_id = (SELECT run_id FROM needs.archive_run);

GRANT SELECT ON needs.archive_topic_quarter_judgement TO needs_runtime;
