-- A path from one cell of the judgement grid all the way to the evidence's original text, without
-- hand-joining anything (the fork's #6 completion criterion). One line -- WHERE topic_key = ... AND
-- quarter = ... -- brings out the type and score together with the consumer speech backing that cell.
--
-- This view exists for more than saving one join. An evidence row does not copy the body text, it
-- points at a corpus document (025), so if a person hand-joins corpus_document every time, that join's
-- predicate (whether snapshot_id is also matched) will vary from person to person -- a join that leaves
-- out the version pulls in the same doc_id from a re-collection (#38) too.
-- db/migrate.sh re-applies this on every deploy. CREATE OR REPLACE only succeeds when the columns stay
-- the same, so DROP goes first.

DROP VIEW IF EXISTS needs.topic_quarter_evidence_quote;
CREATE VIEW needs.topic_quarter_evidence_quote AS
SELECT j.run_id, j.scope, j.topic_key, j.quarter, j.source, j.content_type,
       j.panel_version, j.panel_role,
       j.trend_type, j.judged, j.evidence_strength, j.opportunity_score, j.gap_pp, j.hold_reason,
       e.rank, e.like_count, e.matched_term,
       e.snapshot_id, e.doc_id,
       -- The text as it stands. Truncating it is the reader's job (card rendering) -- truncating it
       -- here would make the truncated version read as the original.
       c.text,
       -- A comment's url is the video that comment was posted under (that is how the corpus records
       -- it). The column name itself has to say it is not a link to the comment itself -- leaving it
       -- named `url` would read as "click through and find that speech".
       c.url          AS parent_video_url,
       c.parent_item_id,
       c.channel_id,
       c.published_at AS commented_at
  FROM needs.topic_quarter_judgement j
  JOIN needs.topic_quarter_evidence e
    ON (e.run_id, e.scope, e.topic_key, e.quarter, e.source, e.content_type,
        e.panel_version, e.panel_role)
     = (j.run_id, j.scope, j.topic_key, j.quarter, j.source, j.content_type,
        j.panel_version, j.panel_role)
  JOIN needs.corpus_document c
    ON (c.snapshot_id, c.doc_id) = (e.snapshot_id, e.doc_id);

GRANT SELECT ON needs.topic_quarter_evidence_quote TO needs_runtime;
