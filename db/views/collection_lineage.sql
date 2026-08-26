-- 원문 한 줄에서 그것을 걷은 수집분과 요청 근거까지 (#144 경로 6a · 6b · 7).
--
-- 리뷰 갈래의 마지막 한 칸이 이 사다리의 **진짜 손실 지점**이다. `trend_radar.review` 에는 run_id 가
-- 없고 그 표는 archive 된 남의 것이라 upstream 이 넣을 수 없다. 이어 주는 것은 `captured_at` 하나인데
-- 그것은 run 의 시간 버킷이고(collectors/commerce/models.py: "the run's hour bucket, not the wall clock"),
-- 같은 버킷에 두 번 시도한 run 은 두 행이다(collectors/commerce/storage/db.py 의 RunLog: "two attempts
-- at the same hour are two rows"). 실측은 단일 확정 31,640 · 후보 2~5개 19,024 · 0개 5,838 이다
-- (56 대 34 대 10).
--
-- 그래서 이 뷰는 **후보를 후보 그대로** 낸다. match 가 single/candidate/unknown 셋으로 갈리고 화면이
-- 그것을 그대로 보인다(사용자 결정 2026-08-27). 하나로 찍으면 화면이 없는 사실을 주장하고, 숨기면
-- "수집분에 못 닿았다" 와 "그 문서가 없다" 가 같아 보인다 -- 후보가 없는 문서도 행 하나를 남긴다.
--
-- 유튜브 갈래는 끝까지 닿는다: 댓글 -> 그 영상의 `video.comments` 판(artifacts) -> 그 판을 만든 일감
-- (jobs). 한 영상에 판이 여럿이라(실측 3,378/3,922) 갈라 주는 것은 fetched_at 뿐이고, 1순위는 그
-- 댓글을 처음 본 시각(first_seen_at)에 가장 가까운 판이다.
--
-- 비용: tubedepth.jobs 에는 (target, kind) 인덱스가 없다(app.tubedepth.sql). 아래 LATERAL 은 문서
-- 하나로 좁혀진 뒤의 판 몇 개에 대해서만 도는 것을 전제로 한다 -- 이 뷰는 필터 없이 부르는 자리가 아니다.
--
-- LIKE 를 쓰지 않는 이유는 db/views/mention_lineage.sql 머리말과 같다(psycopg 플레이스홀더).
-- db/migrate.sh (f) 가 배포마다 DROP + CREATE 한다.

DROP VIEW IF EXISTS needs.collection_lineage;
CREATE VIEW needs.collection_lineage AS
WITH review_candidate AS (
    SELECT
        r.source                                                  AS site,
        r.product_key                                             AS doc_parent,
        r.review_key                                              AS doc_key,
        r.captured_at                                             AS doc_at,
        run.id                                                    AS run_id,
        run.captured_at                                           AS collected_at,
        run.started_at,
        run.finished_at,
        run.status,
        run.sources,
        run.datasets,
        -- count(run.id) 는 매칭된 run 만 센다 -- LEFT JOIN 이 만든 빈 짝은 0 이고 그것이 '미상' 이다.
        count(run.id) OVER (PARTITION BY r.source, r.review_key)::int   AS candidate_count,
        row_number() OVER (PARTITION BY r.source, r.review_key
                           ORDER BY run.started_at, run.id)::int        AS candidate_rank
    FROM trend_radar.review r
    -- 버킷만 보면 그 시각에 돈 모든 run 이 후보가 된다. sources 로 좁혀야 그 리뷰의 사이트를 실제로
    -- 걷은 run 만 남는다 -- run.sources 는 콤마로 이은 목록이다(storage/db.py 의 RunLog.start).
    LEFT JOIN trend_radar.run run
           ON run.captured_at = r.captured_at
          AND r.source = ANY (string_to_array(run.sources, ','))
),
comment_candidate AS (
    SELECT
        c.video_id                                                AS doc_parent,
        c.comment_id                                              AS doc_key,
        c.first_seen_at                                           AS doc_at,
        a.identifier                                              AS artifact_id,
        a.kind                                                    AS artifact_kind,
        a.fetched_at,
        a.byte_count,
        count(a.identifier) OVER (PARTITION BY c.video_id, c.comment_id)::int AS candidate_count,
        row_number() OVER (PARTITION BY c.video_id, c.comment_id
                           ORDER BY abs(extract(epoch FROM a.fetched_at - c.first_seen_at)),
                                    a.identifier)::int            AS candidate_rank
    FROM tubedepth.comments c
    -- kind 를 걸지 않으면 같은 영상의 video.metadata 판이 댓글을 걷은 판으로 셈해진다.
    LEFT JOIN tubedepth.artifacts a
           ON a.target = c.video_id AND a.kind = 'video.comments'
)
SELECT
    'review'::text                                                AS src,
    rc.site,
    rc.doc_parent,
    rc.doc_key,
    rc.doc_at,
    CASE WHEN rc.candidate_count = 0 THEN 'unknown'
         WHEN rc.candidate_count = 1 THEN 'single'
         ELSE 'candidate' END                                     AS match,
    rc.candidate_count,
    rc.candidate_rank,
    CASE WHEN rc.run_id IS NOT NULL THEN 'commerce_run' END       AS collection_kind,
    rc.run_id::text                                               AS collection_id,
    rc.collected_at,
    rc.started_at,
    rc.finished_at,
    rc.status,
    CASE WHEN rc.run_id IS NOT NULL THEN rc.sources || ' / ' || rc.datasets END AS scope_note,
    fl.requests,
    fl.ok,
    fl.sample_url,
    NULL::int                                                     AS bytes
FROM review_candidate rc
-- 경로 7: 그 run 이 그 사이트에 실제로 무엇을 요청했나. 소스를 안 좁히면 한 run 이 걷은 다른 사이트의
-- 요청까지 이 리뷰의 근거로 읽힌다. 2xx 만 ok 로 세는 것은 collector_health 와 같은 선이다.
LEFT JOIN LATERAL (
    SELECT count(*)::int                                                   AS requests,
           count(*) FILTER (WHERE f.status >= 200 AND f.status < 300)::int AS ok,
           min(f.url)                                                      AS sample_url
    FROM trend_radar.fetch_log f
    WHERE f.run_id = rc.run_id AND f.source = rc.site
) fl ON rc.run_id IS NOT NULL
UNION ALL
SELECT
    'yt_comment'::text,
    'youtube'::text,
    cc.doc_parent,
    cc.doc_key,
    cc.doc_at,
    CASE WHEN cc.candidate_count = 0 THEN 'unknown'
         WHEN cc.candidate_count = 1 THEN 'single'
         ELSE 'candidate' END,
    cc.candidate_count,
    cc.candidate_rank,
    CASE WHEN cc.artifact_id IS NOT NULL THEN 'youtube_artifact' END,
    cc.artifact_id,
    cc.fetched_at,
    jb.started_at,
    jb.finished_at,
    jb.state,
    cc.artifact_kind,
    NULL::int,
    NULL::int,
    NULL::text,
    cc.byte_count
FROM comment_candidate cc
-- 그 판을 만든 일감. artifacts 에 job 을 가리키는 컬럼이 없어 이어 주는 것은 (target, kind) 와 시각뿐이라,
-- 그 판이 굳기 전에 끝난 가장 가까운 일감을 고른다. 못 고르면 NULL 이고, 판 자체는 그대로 남는다.
LEFT JOIN LATERAL (
    SELECT j.state, j.started_at, j.finished_at
    FROM tubedepth.jobs j
    WHERE j.target = cc.doc_parent
      AND j.kind = 'video.comments'
      AND j.finished_at IS NOT NULL
      AND j.finished_at <= cc.fetched_at
    ORDER BY j.finished_at DESC
    LIMIT 1
) jb ON cc.artifact_id IS NOT NULL;

GRANT SELECT ON needs.collection_lineage TO needs_runtime;
-- 뷰의 권한은 뷰가 소유한다(#158) -- db/views/mention_lineage.sql 머리말과 같은 이유.
GRANT SELECT ON needs.collection_lineage TO postgrest_anon;
NOTIFY pgrst, 'reload schema';
