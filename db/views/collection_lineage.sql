-- 원문 한 줄에서 그것을 걷은 수집분과 요청 근거까지 (#144 경로 6a · 6b · 7).
--
-- 리뷰 갈래의 마지막 한 칸이 이 사다리의 **진짜 손실 지점**이다. `trend_radar.review` 에는 run_id 가
-- 없고 그 표는 archive 된 남의 것이라 upstream 이 넣을 수 없다. 이어 주는 것은 세 값뿐이다:
-- `captured_at`(run 의 시간 버킷, collectors/commerce/models.py -- "the run's hour bucket, not the
-- wall clock") · `run.sources` · `run.datasets`.
--
-- 세 술어가 모두 필요하고, 그중 `datasets` 는 **사이트마다 다르다**. 버킷과 소스만 걸면 매시 도는
-- `ranking` run 이 모든 버킷을 채워 모든 리뷰의 후보가 된다(contracts/entrypoints.md 의 commerce
-- 크론). 그렇다고 dataset 을 사이트와 무관하게 `{review, review_low}` 로 좁히면 반대편으로 틀린다.
-- 운영 실측(리뷰 30,043건):
--   sources 만                single  9,327 · candidate 20,716 · unknown 0  <- unknown 이 도달 불가
--   dataset 을 통짜로 좁힘     unknown 2,284 가 **전부 glowpick**(그 사이트 3,597건의 63.5퍼센트)
-- 뒤쪽은 진짜 단일 매치를 조용히 '미상' 으로 오분류한 것이다 -- 앞쪽과 방향만 다른 같은 크기의 거짓.
--
-- 갈리는 이유는 `parse()` 의 게이트다. oliveyoung 은 `_parse_ranking` 이 follow 를 dataset 으로
-- 가르고(`wants_reviews`/`wants_low`/`wants_stats`) daisomall 도 `wants_reviews` 로 가르지만,
-- **glowpick 은 게이트가 없다**: `payload.fetch.dataset` 을 NEW_PRODUCT 갈라내는 데만 쓰고 그 뒤로는
-- 조건 없이 `_reviews(...)` 를 부른다 -- ranking 과 review 가 같은 카테고리 페이지이기 때문이고,
-- 그 사실은 클래스 주석에 적혀 있다. 크론이 ranking 매시 / review 하루 한 번이고 리뷰 upsert 가
-- DO NOTHING(storage/repository.py)이라 **첫 기록자가 대개 hourly ranking 런**이다.
-- `review_stats` 는 어느 사이트에서도 안 든다: `_stats_fetch`/`_summary_fetch` 만 따라가고 리뷰
-- 본문 fetch 를 만들지 않는다 -- strpos 로 'review' 를 찾으면 그것까지 후보가 된다.
--
-- 그래서 아래 `review_body_dataset` 목록은 이 파일이 지어낸 것이 아니라 **수집기의 선언을 비춘 것**
-- 이다(각 소스의 `review_body_datasets: ClassVar[frozenset[Dataset]]`). 둘이 갈리면
-- tests/test_collection_lineage_view.py 가 울고, 그 선언이 `parse()` 와 갈리면
-- tests/collectors/commerce/test_review_body_datasets.py 가 녹화 픽스처를 재생해서 운다. 사이트가
-- 하나 늘면 등록 자체가 거절한다(collectors/commerce/registry.py 의 `_REQUIRED_ATTRS`).
--
-- 목록을 배열로 푸는 이유: 지금은 run 하나가 dataset 하나지만(models.py 의 Dataset docstring)
-- 컬럼의 형식은 `",".join(...)` 이다(collectors/commerce/storage/db.py 의 RunLog.start) -- IN 으로
-- 적으면 여러 dataset 을 담은 run 이 언젠가 조용히 후보에서 빠진다. 쉼표 둘레의 공백까지 한 번에
-- 떼는 regexp_split_to_array 를 쓰는 것은 그 형식이 공백을 금하지 않아서이고, 원소마다 btrim 을
-- 부르는 상관 서브쿼리보다 싸서다(그 형태는 run 마다 서브플랜을 하나씩 돌려 질의당 2ms 를 더 썼다).
--
-- 그래서 이 뷰는 **후보를 후보 그대로** 낸다. match 가 single/candidate/unknown 셋으로 갈리고 화면이
-- 그것을 그대로 보인다(사용자 결정 2026-08-27). 하나로 찍으면 화면이 없는 사실을 주장하고, 숨기면
-- "수집분에 못 닿았다" 와 "그 문서가 없다" 가 같아 보인다 -- 후보가 없는 문서도 행 하나를 남긴다.
--
-- 유튜브 갈래는 끝까지 닿는다: 댓글 -> 그 영상의 `video.comments` 판(artifacts) -> 그 판을 만든 일감
-- (jobs). 한 영상에 판이 여럿이라(실측 3,378/3,922) 갈라 주는 것은 fetched_at 뿐이고, 1순위는 그
-- 댓글을 처음 본 시각(first_seen_at)에 가장 가까운 판이다.
--
-- **필터가 문서 한 건까지 내려가야 한다** -- 그러지 않으면 아래 fetch_log 집계가 살아남을 몇 행이
-- 아니라 문서 전부에 대해 돈다(운영 EXPLAIN: 한 번 누를 때 1.3초 · 20.8만 버퍼, 집계 loops=45,255).
-- 그것을 막는 것이 둘이었고 둘 다 여기서 고쳐져 있다:
--   1. 윈도를 바깥에 두면 그 위에 필터가 남는다 -> 후보 계산을 리뷰/댓글 **한 건 안의 LATERAL** 로
--      내렸다. 그래야 review_pkey·comments_pkey 가 산다.
--   2. UNION ALL 의 두 갈래에서 **타입이 다른 컬럼은 술어가 갈래 안으로 안 내려간다**
--      (compare_tlist_datatypes). tubedepth 는 varchar, trend_radar 는 text 라 doc_parent·doc_key 가
--      정확히 그 경우였다 -> 유튜브 갈래에 ::text 를 붙였다. 그 캐스트를 지우면 성능만 조용히 죽는다.
--
-- 비용: tubedepth.jobs 에는 (target, kind) 인덱스가 없다(app.tubedepth.sql). 아래 LATERAL 은 문서
-- 하나로 좁혀진 뒤의 판 몇 개에 대해서만 도는 것을 전제로 한다 -- 이 뷰는 필터 없이 부르는 자리가 아니다.
--
-- LIKE 를 쓰지 않는 이유는 db/views/mention_lineage.sql 머리말과 같다(psycopg 플레이스홀더).
-- db/migrate.sh (f) 가 배포마다 DROP + CREATE 한다.

DROP VIEW IF EXISTS needs.collection_lineage;
CREATE VIEW needs.collection_lineage AS
-- 수집기 선언의 거울. 한 줄 = "이 사이트의 이 dataset 런은 trend_radar.review 에 본문을 쓴다".
-- 정본은 collectors/commerce/sources/*.py 의 `review_body_datasets` 이고, 테스트가 둘을 맞춰 본다.
WITH review_body_dataset (site, dataset) AS (
    VALUES ('daisomall', 'review'),
           ('glowpick', 'ranking'),
           ('glowpick', 'review'),
           ('oliveyoung', 'review'),
           ('oliveyoung', 'review_low')
),
-- 리뷰 본문을 실제로 쓴 run 만, 그리고 **어느 사이트에 대해** 썼는지까지. body_sites 가 비면 그
-- run 은 어떤 리뷰의 후보도 아니다. MATERIALIZED 는 이 좁히기가 리뷰마다 다시 돌지 않게 한다 --
-- 아래 LATERAL 은 그 결과(운영에서 240 run 중 일부)만 훑는다.
review_run AS MATERIALIZED (
    SELECT * FROM (
        SELECT
            run.id,
            run.captured_at,
            run.started_at,
            run.finished_at,
            run.status,
            run.sources,
            run.datasets,
            ARRAY(
                SELECT DISTINCT w.site
                FROM review_body_dataset w
                WHERE w.site = ANY (regexp_split_to_array(btrim(run.sources), '\s*,\s*'))
                  AND w.dataset = ANY (regexp_split_to_array(btrim(run.datasets), '\s*,\s*'))
            ) AS body_sites
        FROM trend_radar.run run
    ) scoped
    WHERE cardinality(scoped.body_sites) > 0
)
SELECT
    'review'::text                                                AS src,
    r.source                                                      AS site,
    r.product_key                                                 AS doc_parent,
    r.review_key                                                  AS doc_key,
    r.captured_at                                                 AS doc_at,
    CASE WHEN coalesce(rc.candidate_count, 0) = 0 THEN 'unknown'
         WHEN rc.candidate_count = 1 THEN 'single'
         ELSE 'candidate' END                                     AS match,
    coalesce(rc.candidate_count, 0)                               AS candidate_count,
    -- 후보가 없어도 그 문서는 한 행이다 -- 순위는 1 이고 수집분 칸이 비어 '미상' 이 된다.
    coalesce(rc.candidate_rank, 1)                                AS candidate_rank,
    CASE WHEN rc.id IS NOT NULL THEN 'commerce_run' END           AS collection_kind,
    rc.id::text                                                   AS collection_id,
    rc.captured_at                                                AS collected_at,
    rc.started_at,
    rc.finished_at,
    rc.status,
    CASE WHEN rc.id IS NOT NULL THEN rc.sources || ' / ' || rc.datasets END AS scope_note,
    fl.requests,
    fl.ok,
    fl.sample_url,
    NULL::int                                                     AS bytes
FROM trend_radar.review r
LEFT JOIN LATERAL (
    SELECT
        run.id, run.captured_at, run.started_at, run.finished_at, run.status,
        run.sources, run.datasets,
        count(*) OVER ()::int                                     AS candidate_count,
        row_number() OVER (ORDER BY run.started_at, run.id)::int  AS candidate_rank
    FROM review_run run
    WHERE run.captured_at = r.captured_at
      -- 사이트별이다: 같은 run 이 glowpick 리뷰의 후보이면서 oliveyoung 리뷰의 후보가 아닐 수 있다.
      AND r.source = ANY (run.body_sites)
) rc ON true
-- 경로 7: 그 run 이 그 사이트에 실제로 무엇을 요청했나. 소스를 안 좁히면 한 run 이 걷은 다른 사이트의
-- 요청까지 이 리뷰의 근거로 읽힌다. 2xx 만 ok 로 세는 것은 collector_health 와 같은 선이다.
LEFT JOIN LATERAL (
    SELECT count(*)::int                                                   AS requests,
           count(*) FILTER (WHERE f.status >= 200 AND f.status < 300)::int AS ok,
           min(f.url)                                                      AS sample_url
    FROM trend_radar.fetch_log f
    WHERE f.run_id = rc.id AND f.source = r.source
) fl ON rc.id IS NOT NULL
UNION ALL
SELECT
    'yt_comment'::text,
    'youtube'::text,
    c.video_id::text,
    c.comment_id::text,
    c.first_seen_at,
    CASE WHEN coalesce(cc.candidate_count, 0) = 0 THEN 'unknown'
         WHEN cc.candidate_count = 1 THEN 'single'
         ELSE 'candidate' END,
    coalesce(cc.candidate_count, 0),
    coalesce(cc.candidate_rank, 1),
    CASE WHEN cc.identifier IS NOT NULL THEN 'youtube_artifact' END,
    -- ::text 는 장식이 아니다. UNION ALL 의 두 갈래에서 **타입이 다른 컬럼은 술어가 갈래 안으로
    -- 내려가지 않는다**(PostgreSQL 의 compare_tlist_datatypes 가 그 컬럼만 unsafe 로 표시한다).
    -- tubedepth 쪽은 varchar 이고 trend_radar 쪽은 text 라, 캐스트가 없으면 doc_parent·doc_key 의
    -- eq 필터가 Append 위에 남아 두 갈래를 통째로 훑은 뒤에야 걸린다 -- 그것이 F2 의 나머지 절반이다.
    cc.identifier::text,
    cc.fetched_at,
    jb.started_at,
    jb.finished_at,
    jb.state::text,
    cc.kind::text,
    NULL::int,
    NULL::int,
    NULL::text,
    cc.byte_count
FROM tubedepth.comments c
LEFT JOIN LATERAL (
    -- kind 를 걸지 않으면 같은 영상의 video.metadata 판이 댓글을 걷은 판으로 셈해진다.
    SELECT
        a.identifier, a.kind, a.fetched_at, a.byte_count,
        count(*) OVER ()::int                                     AS candidate_count,
        row_number() OVER (ORDER BY abs(extract(epoch FROM a.fetched_at - c.first_seen_at)),
                           a.identifier)::int                     AS candidate_rank
    FROM tubedepth.artifacts a
    WHERE a.target = c.video_id AND a.kind = 'video.comments'
) cc ON true
-- 그 판을 만든 일감. artifacts 에 job 을 가리키는 컬럼이 없어 이어 주는 것은 (target, kind) 와 시각뿐이라,
-- 그 판이 굳기 전에 끝난 가장 가까운 일감을 고른다. 못 고르면 NULL 이고, 판 자체는 그대로 남는다.
LEFT JOIN LATERAL (
    SELECT j.state, j.started_at, j.finished_at
    FROM tubedepth.jobs j
    WHERE j.target = c.video_id
      AND j.kind = 'video.comments'
      AND j.finished_at IS NOT NULL
      AND j.finished_at <= cc.fetched_at
    ORDER BY j.finished_at DESC
    LIMIT 1
) jb ON cc.identifier IS NOT NULL;

GRANT SELECT ON needs.collection_lineage TO needs_runtime;
-- 뷰의 권한은 뷰가 소유한다(#158) -- db/views/mention_lineage.sql 머리말과 같은 이유.
GRANT SELECT ON needs.collection_lineage TO postgrest_anon;
NOTIFY pgrst, 'reload schema';
