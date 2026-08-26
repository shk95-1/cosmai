-- 수집기 run 하나가 한 dataset 에 대해 낸 결과 한 줄. contracts/entrypoints.md §공통 운영 뷰의 12컬럼을
-- 이름·순서·타입 그대로 낸다 (P16 의 표가 이 뷰 하나로 나와야 한다).
--
-- 팔은 셋이다 -- commerce(trend_radar 의 run+fetch_log), naver(naver_run+naver_fetch_log),
-- youtube(tubedepth.jobs). #77 이 youtube 를 붙였다: 3단계에서 뺐던 이유 셋(run 이 없다 · 지연을 잰
-- 컬럼이 없다 · dataset 어휘가 다르다)이 #100·#101·#102 로 다 없어졌다.
--
-- queued 는 앞의 두 팔에서 NULL 이다: 둘 다 크론이 부르는 배치 워커라 대기 큐라는 것이 아예 없다.
-- youtube 에서만 숫자다 -- 0(큐가 비었다)과 NULL(큐라는 것이 없다)이 표에서 갈려야 한다.
--
-- elapsed_ms 의 뜻이 팔마다 다르다. commerce·naver 는 fetch 한 번의 왕복이고, youtube 는 job 하나의
-- 전체 벽시계(claim→finish)다(#101). 캐시 적중 job 은 fetch 를 아예 안 해서 왕복으로는 잴 것이 없기
-- 때문이다. 그래서 youtube 의 p90_ms 는 "요청이 얼마나 느렸나" 가 아니라 "일감 하나를 처리하는 데
-- 얼마나 걸렸나" 이고, 같은 이유로 requests 도 HTTP 요청 수가 아니라 끝난 job 수다 -- 캐시로 답한
-- job 도 1 로 센다 (jobs 에 캐시 적중을 표시하는 컬럼이 없다). 계약 §공통 운영 뷰가 같은 말을 한다.
--
-- requests 는 fetch_log 줄 수 전부이고 ok·blocked·failed 는 계약이 정한 세 통(2xx / 403·429 /
-- error 나 5xx)뿐이다 -- 셋의 합이 requests 보다 작으면 그 차이가 어느 통에도 안 들어간 응답(예: 404)이다.
--
-- 이 뷰는 needs_owner 가 만들고 소유자 권한으로 돈다. 그것이 여기서는 의도다: needs_runtime 은 원천
-- 테이블에 직접 닿지 않고 이 뷰만 읽는다 (db/grants/needs_runtime_reader.sql 의 needs_owner 블록).
--
-- db/migrate.sh (f) 가 배포마다 다시 적용한다. CREATE OR REPLACE 는 컬럼 이름·순서·타입이 그대로일 때만
-- 성공하므로 DROP 을 앞세운다 -- 뷰를 넓히는 배포가 exit 1 로 멈추지 않게.
--
-- #78: commerce 는 소스 락에 밀려 전부 물러난 run 도, 소스가 실제로 에러 난 run 도 똑같이
-- run.status='partial' 로 쓴다(collectors/commerce/cli.py) -- 둘을 가르는 사실은 이미
-- trend_radar.run_source.outcome 에 있다(collectors/commerce/storage/db.py 의 outcome_of). 계약
-- 컬럼을 늘리지 않고 그 표를 상관 서브쿼리로 들여다보는 것으로 족하다: run_source 행이 하나라도
-- 있고 전부 'skipped' 면 양보고, 그렇지 않으면 기존 status 그대로다.

DROP VIEW IF EXISTS needs.collector_health;
CREATE VIEW needs.collector_health AS
SELECT
    'commerce'::text                                                     AS collector,
    -- 한 run 이 여러 dataset 을 훑으므로 dataset 은 run 이 아니라 fetch_log 가 갖는다. 한 줄도
    -- 남기지 못한 run 은 이름댈 dataset 이 없어 NULL 이고, 그래도 행은 남는다 (LEFT JOIN).
    f.dataset                                                            AS dataset,
    r.id::text                                                           AS run_id,
    r.started_at                                                         AS started_at,
    r.finished_at                                                        AS finished_at,
    CASE
        WHEN r.status = 'partial'
             AND EXISTS (SELECT 1 FROM trend_radar.run_source rs WHERE rs.run_id = r.id)
             AND NOT EXISTS (
                 SELECT 1 FROM trend_radar.run_source rs
                 WHERE rs.run_id = r.id AND rs.outcome <> 'skipped'
             )
        THEN 'yielded'
        ELSE r.status
    END                                                                   AS status,
    count(f.id)::int                                                     AS requests,
    count(*) FILTER (WHERE f.status BETWEEN 200 AND 299)::int             AS ok,
    count(*) FILTER (WHERE f.status IN (403, 429))::int                   AS blocked,
    count(*) FILTER (WHERE f.error IS NOT NULL OR f.status >= 500)::int   AS failed,
    NULL::int                                                            AS queued,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY f.elapsed_ms)::int        AS p90_ms
FROM trend_radar.run r
LEFT JOIN trend_radar.fetch_log f ON f.run_id = r.id
GROUP BY r.id, f.dataset

UNION ALL

SELECT
    'naver'::text,
    -- naver 는 run 하나가 dataset 하나다 (contracts/ddl/needs/004_naver.sql 의 CHECK).
    r.dataset,
    r.id::text,
    r.started_at,
    r.finished_at,
    r.status,
    count(f.id)::int,
    count(*) FILTER (WHERE f.status BETWEEN 200 AND 299)::int,
    count(*) FILTER (WHERE f.status IN (403, 429))::int,
    count(*) FILTER (WHERE f.error IS NOT NULL OR f.status >= 500)::int,
    NULL::int,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY f.elapsed_ms)::int
FROM needs.naver_run r
LEFT JOIN needs.naver_fetch_log f ON f.run_id = r.id
GROUP BY r.id

UNION ALL

-- #77: youtube 한 행 = (dataset, 시간 버킷) 하나다. tubedepth.jobs 에는 run 이 없어 run_id 를 낼 것이
-- 없고(그 자리는 NULL 이다), 그래서 commerce 의 run 에 해당하는 "유한한 일감 묶음" 을 뷰가 시간으로
-- 만든다. 한 시간 버킷이지 "최근 1시간" 창이 아닌 것은 두 팔과 나란히 읽히기 위해서다: commerce 는 지난
-- run 을 전부 행으로 남기므로 youtube 도 모든 job 이 정확히 한 행에 영원히 남아야 하고, 창이면 크론이
-- 한 시간 쉰 순간 youtube 팔이 표에서 통째로 사라진다.
SELECT
    'youtube'::text,
    q.dataset,
    NULL::text,
    q.bucket,
    q.finished_at,
    CASE
        WHEN q.in_flight > 0 THEN 'running'::text
        WHEN q.ok > 0 AND q.failures > 0 THEN 'partial'
        WHEN q.ok > 0 THEN 'ok'
        WHEN q.failures > 0 AND q.failures = q.blocked THEN 'blocked'
        -- 성공이 하나도 없는 버킷은 실패로 읽는다. 남는 경우는 전부 cancelled 인 버킷뿐이고 지금은
        -- 어느 경로도 그 상태를 쓰지 않는다 -- 건강 뷰에서 조용한 쪽으로 틀리지 않는 편이 낫다.
        ELSE 'failed'
    END,
    q.requests,
    q.ok,
    q.blocked,
    q.failures - q.blocked,
    q.queued,
    q.p90_ms
FROM (
    SELECT
        j.dataset::text                                                       AS dataset,
        -- claim 된 적 없는 job(queued, 그리고 started_at 이 생기기 전 #101 이전의 옛 행)은 started_at
        -- 이 없다 -- 그 job 을 큐에 넣은 시각이 그것이 앉을 유일한 자리다.
        date_trunc('hour', coalesce(j.started_at, j.created_at))               AS bucket,
        max(j.finished_at)                                                     AS finished_at,
        count(*) FILTER (WHERE j.state IN ('queued', 'running'))::int           AS in_flight,
        count(*) FILTER (WHERE j.state IN ('succeeded', 'failed', 'cancelled'))::int AS requests,
        count(*) FILTER (WHERE j.state = 'succeeded')::int                     AS ok,
        count(*) FILTER (WHERE j.state = 'failed')::int                        AS failures,
        -- error_code 어휘는 collectors/youtube/cli.py 의 _classify_error 가 정본이다(#100). 이 넷이
        -- commerce fetch_log 의 403·429 에 해당한다: quota(403 + quotaExceeded)·rate_limited(429)·
        -- http_403(quotaExceeded 아닌 403). http_429 는 지금 분류기가 내지 않지만, 다른 전송이 429 를
        -- 그 모양으로 주더라도 failed 가 아니라 blocked 에 앉게 함께 적는다.
        count(*) FILTER (
            WHERE j.state = 'failed'
              AND j.error_code IN ('quota', 'rate_limited', 'http_403', 'http_429')
        )::int                                                                 AS blocked,
        count(*) FILTER (WHERE j.state = 'queued')::int                        AS queued,
        -- elapsed_ms 가 NULL 인 옛 행은 percentile_cont 가 알아서 뺀다 -- 0 으로 채우면 지연이
        -- 실제보다 낮게 보인다. naver 팔이 NULL elapsed_ms 를 다루는 방식과 같다.
        percentile_cont(0.9) WITHIN GROUP (ORDER BY j.elapsed_ms)::int          AS p90_ms
    FROM tubedepth.jobs j
    GROUP BY j.dataset, date_trunc('hour', coalesce(j.started_at, j.created_at))
) q;

GRANT SELECT ON needs.collector_health TO needs_runtime;
