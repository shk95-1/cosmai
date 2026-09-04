-- Asks the two invariants of the evidence table back against the stored rows. Empty means true (contracts/interfaces.md §Evidence).
-- rank's grammar, the source match and duplicate documents are already kept inside one row by 025's
-- CHECK and unique key, so they are not here. What is here is the two things a single row cannot show.
-- db/migrate.sh re-applies this on every deploy.

DROP VIEW IF EXISTS needs.topic_quarter_evidence_violation;
CREATE VIEW needs.topic_quarter_evidence_violation AS
-- (1) rank runs from 1 with no gap. If only 2nd and 3rd place are left, "this cell's rank-1 evidence"
-- quietly vanishes from the table and the card reads what's left as the top. A unique key only stops a
-- duplicate rank, not a hole in that ladder.
SELECT 'rank_not_dense'::text                                            AS violation,
       run_id, scope, source, content_type, panel_version, panel_role, quarter,
       format('topic=%s ranks=%s max=%s', topic_key, count(*), max(rank)) AS detail
  FROM needs.topic_quarter_evidence
 GROUP BY run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role
HAVING max(rank) <> count(*) OR min(rank) <> 1
UNION ALL
-- (2) Is the evidence really that cell's? An FK only keeps "that version has such a document" -- it
-- never asks whether that document actually spoke to this topic, or whether it was posted under that
-- quarter's video. Both conditions are the exact predicate that built that cell's metric
-- (the population in analysis/trend/pipeline.py's), and if they disagree the card carries another
-- cell's speech as its evidence. The quarter expression is the same expression as that pipeline's
-- QUARTER -- if they diverge, this is where it shows.
SELECT 'quote_outside_the_cell'::text,
       e.run_id, e.scope, e.source, e.content_type, e.panel_version, e.panel_role, e.quarter,
       format('topic=%s doc=%s parent=%s parent_quarter=%s mentions_topic=%s',
              e.topic_key, e.doc_id, c.parent_item_id,
              coalesce(to_char(v.published_at AT TIME ZONE 'UTC', 'YYYY"Q"Q'), '-'),
              EXISTS (SELECT 1 FROM needs.corpus_mention m
                       WHERE m.snapshot_id = e.snapshot_id AND m.doc_id = e.doc_id
                         AND m.topic_id = e.topic_key))
  FROM needs.topic_quarter_evidence e
  JOIN needs.corpus_document c
    ON (c.snapshot_id, c.doc_id) = (e.snapshot_id, e.doc_id)
  LEFT JOIN needs.corpus_document v
    ON v.snapshot_id = e.snapshot_id AND v.source = 'youtube_video'
   AND v.source_item_id = c.parent_item_id
 WHERE v.snapshot_id IS NULL
    OR to_char(v.published_at AT TIME ZONE 'UTC', 'YYYY"Q"Q') <> e.quarter
    OR NOT EXISTS (SELECT 1 FROM needs.corpus_mention m
                    WHERE m.snapshot_id = e.snapshot_id AND m.doc_id = e.doc_id
                      AND m.topic_id = e.topic_key);

GRANT SELECT ON needs.topic_quarter_evidence_violation TO needs_runtime;
