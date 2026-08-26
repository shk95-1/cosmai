-- 판정 표의 두 불변식을 저장된 행에 대고 되묻는다. 비어 있으면 참이다 (contracts/interfaces.md §판정).
-- 유형 어휘·judged·점수의 정의는 024 의 CHECK 이 행 하나 안에서 지키므로 여기 없다. 여기 있는 것은
-- 행 하나로는 볼 수 없는 둘 -- 지표 행과의 1:1, 그리고 두 source 행이 같은 값을 들어야 하는 gap_pp 다.
-- db/migrate.sh 가 배포마다 다시 적용한다. CREATE OR REPLACE 는 컬럼이 그대로일 때만 성공하므로 DROP 을
-- 앞세운다.

DROP VIEW IF EXISTS needs.topic_quarter_judgement_violation;
CREATE VIEW needs.topic_quarter_judgement_violation AS
-- ① 판정이 지표 행과 1:1 이다. FK 는 "판정 행마다 지표 행이 있다"만 지키고 그 반대는 못 지킨다 --
-- 판정이 일부 셀에서 조용히 빠지면 유형 분포가 모집단이 아니라 남은 것들의 분포가 된다.
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
-- ② gap_pp 는 (주제, 분기) 단위 사실이라 두 source 행이 같은 값을 든다. 두 행이 갈리면 한쪽이 다른
-- 구성비를 뺀 것이고, 그 순간 이 칸은 "댓글이 영상보다 얼마나 많이 말하는가"가 아닌 다른 수가 된다.
SELECT 'gap_pp_disagrees'::text,
       run_id, scope, NULL::text, content_type, panel_version, panel_role,
       quarter,
       format('topic=%s sources=%s distinct_gap=%s',
              topic_key, count(*), count(DISTINCT gap_pp))
  FROM needs.topic_quarter_judgement
 GROUP BY run_id, scope, topic_key, quarter, content_type, panel_version, panel_role
HAVING count(DISTINCT gap_pp) > 1
    -- 한 source 에만 행이 있으면 뺄 상대가 없으므로 NULL 이어야 하고, 둘이면 값이 있어야 한다.
    OR (count(*) = 1) <> (count(gap_pp) = 0);

GRANT SELECT ON needs.topic_quarter_judgement_violation TO needs_runtime;
