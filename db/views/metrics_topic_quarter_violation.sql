-- Asks the two invariants of the quarterly table back against the stored rows. Empty means true
-- (contracts/interfaces.md §Formulas, "the quarterly table's row set"). With the contract as a sentence
-- alone, nobody catches the loader (#5) deleting 0-mention cells or mixing in topics outside trend_use --
-- both make the table mean something else with no error.
-- db/migrate.sh reapplies it on every deploy. CREATE OR REPLACE only succeeds while the columns stay the
-- same, so a DROP goes first.

DROP VIEW IF EXISTS needs.metrics_topic_quarter_violation;
CREATE VIEW needs.metrics_topic_quarter_violation AS
-- (1) The grid is dense: every (trend_use topic x quarter present in that output) has a row. Dropping a
-- zero-mention cell raises persistence's baseline (that topic's all-time median), which moves every
-- topic's value.
SELECT 'sparse_grid'::text                                              AS violation,
       run_id, scope, source, content_type, panel_version, panel_role,
       NULL::text                                                       AS quarter,
       format('rows=%s topics=%s quarters=%s',
              count(*), count(DISTINCT topic_key), count(DISTINCT quarter))
                                                                        AS detail
  FROM needs.metrics_topic_quarter
 GROUP BY run_id, scope, source, content_type, panel_version, panel_role
HAVING count(*) <> count(DISTINCT topic_key) * count(DISTINCT quarter)
UNION ALL
-- (2) The denominator closes: a quarter's sum of mentions equals the quarter_mentions that every row of
-- that quarter holds in common.
-- trend_use=false 주제(추천_재구매·선크림)의 행이 하나라도 섞이면 이 등식이 깨진다.
SELECT 'quarter_mentions_not_closed'::text,
       run_id, scope, source, content_type, panel_version, panel_role,
       quarter,
       format('sum(mentions)=%s quarter_mentions=%s..%s',
              sum(mentions), min(quarter_mentions), max(quarter_mentions))
  FROM needs.metrics_topic_quarter
 GROUP BY run_id, scope, source, content_type, panel_version, panel_role, quarter
HAVING min(quarter_mentions) <> max(quarter_mentions)
    OR sum(mentions) <> min(quarter_mentions);

GRANT SELECT ON needs.metrics_topic_quarter_violation TO needs_runtime;
