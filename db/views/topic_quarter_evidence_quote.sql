-- 판정 격자의 셀 하나에서 근거 원문까지 손으로 조인하지 않고 닿는 길 (포크 #6 의 완료 기준).
-- WHERE topic_key = ... AND quarter = ... 한 줄이면 유형·점수와 그 셀을 받치는 소비자 발화가 같이 나온다.
--
-- 이 뷰가 있는 이유는 표를 하나 아끼려는 것이 아니다. 근거 행은 본문을 베끼지 않고 코퍼스 문서를
-- 가리키므로(025), 사람이 매번 corpus_document 를 손으로 조인하면 그 조인의 술어(snapshot_id 를 같이
-- 걸었는가)가 사람마다 갈린다 -- 판본을 빠뜨린 조인은 재수집분(#38)의 같은 doc_id 를 함께 끌어온다.
-- db/migrate.sh 가 배포마다 다시 적용한다. CREATE OR REPLACE 는 컬럼이 그대로일 때만 성공하므로 DROP 을
-- 앞세운다.

DROP VIEW IF EXISTS needs.topic_quarter_evidence_quote;
CREATE VIEW needs.topic_quarter_evidence_quote AS
SELECT j.run_id, j.scope, j.topic_key, j.quarter, j.source, j.content_type,
       j.panel_version, j.panel_role,
       j.trend_type, j.judged, j.evidence_strength, j.opportunity_score, j.gap_pp, j.hold_reason,
       e.rank, e.like_count, e.matched_term,
       e.snapshot_id, e.doc_id,
       -- 원문 그대로다. 자르는 것은 읽는 쪽(카드 렌더)의 일이고, 여기서 자르면 잘린 것이 원문으로 읽힌다.
       c.text,
       -- 댓글의 url 은 그 댓글이 달린 영상이다(코퍼스가 그렇게 싣는다). 댓글 고유 링크가 아니라는
       -- 사실은 컬럼 이름이 말해야 한다 -- `url` 로 두면 클릭해서 그 발화를 찾을 수 있다고 읽힌다.
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
