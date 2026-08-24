-- 수집기 run 하나가 한 dataset 에 대해 낸 결과 한 줄. contracts/entrypoints.md §공통 운영 뷰의 12컬럼을
-- 이름·순서·타입 그대로 낸다 (P16 의 표가 이 뷰 하나로 나와야 한다).
--
-- 팔은 둘이다 -- commerce(trend_radar 의 run+fetch_log)와 naver(naver_run+naver_fetch_log). youtube 는
-- 3단계에서 빠졌다: 그 수집기에는 run 개념도 지연 컬럼도 없고 error_code 가 차단을 구분하지 않아
-- blocked·p90_ms 가 통째로 NULL 이 되기 때문이다 (근거는 계약 §공통 운영 뷰에 적혀 있다).
--
-- queued 는 두 팔 다 NULL 이다: 둘 다 크론이 부르는 배치 워커라 대기 큐라는 것이 아예 없다. 컬럼을
-- 지우지 않는 것은 큐를 가진 youtube 팔이 돌아올 자리이기 때문이다.
--
-- requests 는 fetch_log 줄 수 전부이고 ok·blocked·failed 는 계약이 정한 세 통(2xx / 403·429 /
-- error 나 5xx)뿐이다 -- 셋의 합이 requests 보다 작으면 그 차이가 어느 통에도 안 들어간 응답(예: 404)이다.
--
-- 이 뷰는 needs_owner 가 만들고 소유자 권한으로 돈다. 그것이 여기서는 의도다: needs_runtime 은 원천
-- 테이블에 직접 닿지 않고 이 뷰만 읽는다 (db/grants/needs_runtime_reader.sql 의 needs_owner 블록).
--
-- db/migrate.sh (f) 가 배포마다 다시 적용한다. CREATE OR REPLACE 는 컬럼 이름·순서·타입이 그대로일 때만
-- 성공하므로 DROP 을 앞세운다 -- 뷰를 넓히는 배포가 exit 1 로 멈추지 않게.

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
    r.status                                                             AS status,
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
GROUP BY r.id;

GRANT SELECT ON needs.collector_health TO needs_runtime;
