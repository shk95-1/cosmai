-- Asks the two invariants of the verdict table back against the stored rows. Empty means true (contracts/interfaces.md §Verdict).
-- The type vocabulary, judged and the score's definition are already kept inside one row by 024's
-- CHECK, so they are not here. What is here is two things a single row cannot show -- a 1:1 with the
-- metrics row, and gap_pp, which the two source rows must carry the same value for.
-- db/migrate.sh re-applies this on every deploy. CREATE OR REPLACE only succeeds when the columns stay
-- the same, so DROP goes first.

DROP VIEW IF EXISTS needs.topic_quarter_judgement_violation;
CREATE VIEW needs.topic_quarter_judgement_violation AS
-- (1) A judgement is 1:1 with the metrics row. An FK only keeps "every judgement row has a metrics row"
-- and cannot keep the reverse -- if a judgement quietly goes missing on some cells, the type
-- distribution stops being the population and becomes the distribution of whatever is left.
SELECT 'unjudged_cell'::text                                            AS violation,
       m.run_id, m.scope, m.source, m.content_type, m.panel_version, m.panel_role,
       m.quarter,
       format('topic=%s', m.topic_key)                                  AS detail
  FROM needs.metrics_topic_quarter m
  LEFT JOIN needs.topic_quarter_judgement j
         ON (j.run_id, j.scope, j.topic_key, j.quarter, j.source, j.content_type,
             j.panel_version, j.panel_role)
          = (m.run_id, m.scope, m.topic_key, m.quarter, m.source, m.content_type,
             m.panel_version, m.panel_role)
 WHERE j.run_id IS NULL
   AND EXISTS (SELECT 1 FROM needs.topic_quarter_judgement k WHERE k.run_id = m.run_id)
UNION ALL
-- (2) gap_pp is a (topic, quarter) fact, so both source rows carry the same value. If the two rows
-- disagree, one of them subtracted a different share, and this cell instantly stops being "how much
-- more comments say than video" and becomes a different number.
SELECT 'gap_pp_disagrees'::text,
       run_id, scope, NULL::text, content_type, panel_version, panel_role,
       quarter,
       format('topic=%s sources=%s distinct_gap=%s',
              topic_key, count(*), count(DISTINCT gap_pp))
  FROM needs.topic_quarter_judgement
 GROUP BY run_id, scope, topic_key, quarter, content_type, panel_version, panel_role
HAVING count(DISTINCT gap_pp) > 1
    -- With a row from only one source, there is nothing to subtract from, so it must be NULL; with
    -- both, it must have a value.
    OR (count(*) = 1) <> (count(gap_pp) = 0);

GRANT SELECT ON needs.topic_quarter_judgement_violation TO needs_runtime;
