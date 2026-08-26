-- 근거 표의 두 불변식을 저장된 행에 대고 되묻는다. 비어 있으면 참이다 (contracts/interfaces.md §근거).
-- 자리(rank)의 문법·source 일치·중복 문서는 025 의 CHECK 과 유일키가 행 하나 안에서 지키므로 여기 없다.
-- 여기 있는 것은 행 하나로는 볼 수 없는 둘이다.
-- db/migrate.sh 가 배포마다 다시 적용한다.

DROP VIEW IF EXISTS needs.topic_quarter_evidence_violation;
CREATE VIEW needs.topic_quarter_evidence_violation AS
-- ① 자리는 1 부터 빈칸 없이 이어진다. 2·3위만 남으면 "이 셀의 1위 근거"가 표에서 조용히 사라지고,
-- 카드는 남은 것을 상위로 읽는다. 유일키는 자리의 중복만 막지 그 사다리의 구멍은 못 막는다.
SELECT 'rank_not_dense'::text                                            AS violation,
       run_id, scope, source, content_type, panel_version, panel_role, quarter,
       format('topic=%s ranks=%s max=%s', topic_key, count(*), max(rank)) AS detail
  FROM needs.topic_quarter_evidence
 GROUP BY run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role
HAVING max(rank) <> count(*) OR min(rank) <> 1
UNION ALL
-- ② 근거가 정말 그 셀의 것인가. FK 는 "그 판본에 그런 문서가 있다"까지만 지킨다 -- 그 문서가 이 주제를
-- 말했는지도, 그 분기 영상에 달렸는지도 묻지 않는다. 두 조건 다 그 셀의 지표를 만든 바로 그 술어이고
-- (analysis/trend/pipeline.py 의 모집단), 어긋나면 카드가 다른 셀의 발화를 근거로 싣는다.
-- 분기식은 그 파이프라인의 QUARTER 와 같은 식이다 -- 갈리면 여기가 말한다.
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
