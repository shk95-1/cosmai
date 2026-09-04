-- 분기 표의 두 불변식을 저장된 행에 대고 되묻는다. 비어 있으면 참이다 (contracts/interfaces.md §수식,
-- "분기 표의 행 집합"). 계약이 문장으로만 있으면 적재기(#5)가 언급 0 셀을 지우거나 trend_use 밖 주제를
-- 섞어도 아무도 못 잡는다 -- 둘 다 표를 오류 없이 다른 뜻으로 만든다.
-- db/migrate.sh 가 배포마다 다시 적용한다. CREATE OR REPLACE 는 컬럼이 그대로일 때만 성공하므로 DROP 을
-- 앞세운다.

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
