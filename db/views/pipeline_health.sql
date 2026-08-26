-- 단계 하나의 "지금 상태" 한 줄 (#138). collector_health·analysis_health 는 run 하나당 한 줄인
-- *로그*라 "지금 무엇이 막혔나" 에 답하지 못한다 -- 이 뷰가 그 로그를 단계별 최신 한 줄로 접고
-- pipeline_stage 의 기대 주기와 견준다.
--
-- 두 사실을 하나로 접지 않는다. "안 돌았다"(freshness)와 "돌았는데 실패했다"(last_run_status)는
-- 직교한다: 3일 전에 실패하고 그 뒤로 안 돈 단계는 한 enum 으로는 둘 중 하나로만 보인다. 화면은
-- 둘 중 나쁜 쪽으로 색을 칠하되 둘 다 표시한다(#139).
--
-- freshness 의 눈금이 절대값이 아니라 expected_interval 의 배수인 이유: 주기가 5분(youtube work)
-- 부터 한 달(naver datalab)까지 벌어져 있어 상수 여유를 두면 어느 한쪽에서 반드시 틀린다.
--
-- never 는 stalled 가 아니다. naver datalab·blog 는 표가 0행이지만 크론 줄이 있어 enabled 다
-- (#138 사용자 결정) -- 값은 포크 cosmai-import-ydc#53 이 채운다. 화면의 배너는 never 를 세지
-- 않는다: 항상 빨간 대시보드는 아무도 안 보게 된다.
--
-- 전제: youtube 의 failed 는 아직 못 믿는다(#112 -- 취소만 든 버킷이 failed 로 읽힌다). 이 뷰는
-- collector_health 가 주는 값을 그대로 통과시키고, 해결은 #112 가 진다.
--
-- db/migrate.sh (f) 가 배포마다 다시 적용한다. CREATE OR REPLACE 는 컬럼이 그대로일 때만 성공하므로
-- DROP 을 앞세운다 -- 뷰를 넓히는 배포가 exit 1 로 멈추지 않게.

DROP VIEW IF EXISTS needs.pipeline_health;
CREATE VIEW needs.pipeline_health AS
WITH runs AS (
    -- 수집기 세 팔. dataset 이 빈 옛 행은 어느 단계인지 말하지 못하므로 뺀다(#101 이전 행).
    SELECT
        collector || ':' || dataset                AS stage_key,
        coalesce(finished_at, started_at)          AS at,
        status,
        requests, ok, blocked, failed, p90_ms
    FROM needs.collector_health
    WHERE dataset IS NOT NULL AND dataset <> ''
    UNION ALL
    -- 분석 두 줄. 증분 패스는 note 의 missing= 으로 갈린다(contracts/entrypoints.md §분석) --
    -- 크론 줄로는 안 갈리고 stage 는 구현 판본을 달고 있어 그대로 쓸 수 없다.
    -- eval:*·trend-quarter:* 는 크론 단계가 아니라 여기 오지 않는다.
    SELECT
        CASE WHEN stage = 'analyze:all' THEN 'analyze:all' ELSE 'analyze:polarity_missing' END,
        coalesce(finished_at, started_at),
        status,
        NULL::int, NULL::int, NULL::int, NULL::int, NULL::int
    FROM needs.analysis_health
    -- LIKE 를 쓰지 않는다: 그 와일드카드 문자를 이 파일을 드라이버로 실행하는 쪽(psycopg)이
    -- 플레이스홀더로 읽어 죽는다 -- 주석에 한 글자만 있어도 그렇다. starts_with/strpos 는
    -- 같은 뜻이면서 그 문자를 쓰지 않는다.
    WHERE stage = 'analyze:all'
       OR (starts_with(stage, 'analyze:polarity:') AND strpos(note, 'missing=') > 0)
),
last_run AS (
    SELECT DISTINCT ON (stage_key) * FROM runs ORDER BY stage_key, at DESC NULLS LAST
),
-- "돌았나" 이지 "깨끗하게 돌았나" 가 아니다. partial 은 돌았고 대부분을 걷은 run 이라 여기 든다 --
-- 얼마나 잘 끝났는지는 last_run_status 가 나란히 말하고, 그것이 두 컬럼을 안 접는 이유다(#154).
-- 넣지 않는 것: yielded(소스 락에 전부 밀려 물러나 아무것도 안 걷었다, #78) · failed · blocked.
-- 이 선을 잘못 그으면 매일 정시에 돌지만 늘 partial 인 단계가 이틀 뒤 stalled 로 굳어 영원히
-- 빨갛다 -- #138 이 never 를 배너에서 뺀 것과 같은 실패 모드다.
last_ran AS (
    SELECT DISTINCT ON (stage_key) stage_key, at
    FROM runs WHERE status IN ('ok', 'partial') ORDER BY stage_key, at DESC NULLS LAST
)
SELECT
    s.stage_key,
    s.arm,
    s.dataset,
    s.enabled,
    s.expected_interval,
    o.at                                                        AS last_success_at,
    r.at                                                        AS last_run_at,
    r.status                                                    AS last_run_status,
    -- 성공한 적이 없으면 "얼마나 늦었나" 라는 질문 자체가 성립하지 않는다 -- 0 이 아니라 NULL 이다.
    CASE WHEN o.at IS NULL THEN NULL
         ELSE greatest(now() - o.at - s.expected_interval, interval '0') END AS overdue_by,
    CASE WHEN NOT s.enabled                              THEN 'disabled'
         WHEN o.at IS NULL                               THEN 'never'
         WHEN now() - o.at <= s.expected_interval        THEN 'ok'
         WHEN now() - o.at <= 2 * s.expected_interval    THEN 'late'
         ELSE 'stalled' END                                     AS freshness,
    -- 마지막 run 의 요청 통계. "돌긴 했는데 403 이 절반" 을 ok 로 읽지 않게 하는 재료다.
    -- 분석 팔은 외부 fetch 가 없어 다섯 칸이 모두 NULL 이다.
    r.requests, r.ok, r.blocked, r.failed, r.p90_ms
FROM needs.pipeline_stage s
LEFT JOIN last_run r USING (stage_key)
LEFT JOIN last_ran o USING (stage_key);

GRANT SELECT ON needs.pipeline_health TO needs_runtime;
-- 화면은 PostgREST 에 anon 으로 묻는다. 이 GRANT 가 db/grants/postgrest_anon_needs.sql 에 있으면
-- 살아남지 못한다: 그 파일은 migrate 단계 (d) 이고 뷰를 DROP 하고 다시 만드는 것은 (f) 라, 새
-- 객체에 옛 GRANT 가 따라오지 않는다. 뷰의 권한은 뷰가 소유한다(#158 -- 화면이 401 이었다).
GRANT SELECT ON needs.pipeline_health TO postgrest_anon;
-- 권한이 바뀌었으니 PostgREST 의 스키마 캐시를 깨운다. (d) 의 NOTIFY 는 이 뷰가 만들어지기
-- 전에 돌아서, 그것만으로는 새 GRANT 를 보지 못한 채 401 이 그대로 남는다.
NOTIFY pgrst, 'reload schema';
