-- 수집기 run 하나가 한 dataset 에 대해 낸 결과 한 줄. contracts/entrypoints.md §공통 운영 뷰의 12컬럼을
-- 이름·순서·타입 그대로 낸다 (P16 의 표가 이 뷰 하나로 나와야 한다).
--
-- Three arms feed it -- commerce (trend_radar's run+fetch_log), naver (naver_run+naver_fetch_log),
-- youtube (tubedepth.jobs). #77 added youtube: the three reasons it was left out at stage 3 (no run,
-- no column that times the lag, a different dataset vocabulary) were all removed by #100/#101/#102.
--
-- queued is NULL on the first two arms: both are batch workers a cron calls, so there is no such thing
-- as a wait queue for them. Only youtube gives a number -- 0 (the queue is empty) and NULL (there is no
-- queue) have to read differently in the table.
--
-- elapsed_ms 의 뜻이 팔마다 다르다. commerce·naver 는 fetch 한 번의 왕복이고, youtube 는 job 하나의
-- 전체 벽시계(claim→finish)다(#101). 캐시 적중 job 은 fetch 를 아예 안 해서 왕복으로는 잴 것이 없기
-- 때문이다. 그래서 youtube 의 p90_ms 는 "요청이 얼마나 느렸나" 가 아니라 "일감 하나를 처리하는 데
-- 얼마나 걸렸나" 이고, 같은 이유로 requests 도 HTTP 요청 수가 아니라 끝난 job 수다 -- 캐시로 답한
-- job 도 1 로 센다 (jobs 에 캐시 적중을 표시하는 컬럼이 없다). 계약 §공통 운영 뷰가 같은 말을 한다.
--
-- requests is every fetch_log row, and ok/blocked/failed are only the three buckets the contract defines
-- (2xx / 403,429 / error or 5xx) -- if the three sum to less than requests, the gap is a response that
-- landed in none of the buckets (404, say).
--
-- This view is created by needs_owner and runs with owner privilege. That is deliberate here:
-- needs_runtime never touches the source tables directly and only reads this view (the needs_owner
-- block of db/grants/needs_runtime_reader.sql).
--
-- db/migrate.sh (f) re-applies this on every deploy. CREATE OR REPLACE only succeeds when the column
-- names, order and types stay the same, so DROP goes first -- a deploy that widens the view must not
-- stop with exit 1.
--
-- #78: commerce writes run.status='partial' (collectors/commerce/cli.py) the same way both for a run
-- pushed entirely out by a source lock and for a run whose source genuinely errored -- the fact that
-- tells the two apart already lives in trend_radar.run_source.outcome (collectors/commerce/storage/db.py's
-- outcome_of). Rather than widen the contract's columns, it is enough to look into that table with a
-- correlated subquery: if there is at least one run_source row and every one of them is 'skipped', call
-- it yielded, otherwise keep the existing status as it is.

DROP VIEW IF EXISTS needs.collector_health;
CREATE VIEW needs.collector_health AS
SELECT
    'commerce'::text                                                     AS collector,
    -- A single run sweeps several datasets, so dataset belongs to fetch_log, not run. A run that left
    -- not a single line has no dataset to name, so it is NULL, and the row still stands (LEFT JOIN).
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
    -- naver has one dataset per run (the CHECK in contracts/ddl/needs/004_naver.sql).
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

-- #77: one youtube row is one (dataset, hour bucket). tubedepth.jobs has no run, so there is nothing to
-- give a run_id (that column sits NULL), so the view builds the "finite bundle of work" that corresponds
-- to commerce's run out of time instead. It is a fixed hour bucket rather than a "last 1 hour" window so
-- it reads side by side with the other two arms: commerce leaves every past run behind as a row forever,
-- so youtube must also leave every job in exactly one row forever, and a window would make the whole
-- youtube arm vanish from the table the moment the cron rests for an hour.
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
        -- A bucket with not a single success reads as failed. What's left is only a bucket that is
        -- entirely cancelled, and no path writes that state today -- better for the health view to be
        -- wrong on the loud side than the quiet one.
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
        -- A job never claimed (queued, plus old rows from before #101 predate started_at existing) has
        -- no started_at -- the moment that job was put on the queue is the only place it can sit.
        date_trunc('hour', coalesce(j.started_at, j.created_at))               AS bucket,
        max(j.finished_at)                                                     AS finished_at,
        count(*) FILTER (WHERE j.state IN ('queued', 'running'))::int           AS in_flight,
        count(*) FILTER (WHERE j.state IN ('succeeded', 'failed', 'cancelled'))::int AS requests,
        count(*) FILTER (WHERE j.state = 'succeeded')::int                     AS ok,
        count(*) FILTER (WHERE j.state = 'failed')::int                        AS failures,
        -- The source of truth for the error_code vocabulary is _classify_error in
        -- collectors/youtube/cli.py (#100). These four correspond to commerce fetch_log's 403/429:
        -- quota (403 + quotaExceeded), rate_limited (429), http_403 (403 that is not quotaExceeded).
        -- The classifier does not emit http_429 today, but it is listed here too so that if another
        -- transport ever hands back 429 in that shape it lands in blocked, not failed.
        count(*) FILTER (
            WHERE j.state = 'failed'
              AND j.error_code IN ('quota', 'rate_limited', 'http_403', 'http_429')
        )::int                                                                 AS blocked,
        count(*) FILTER (WHERE j.state = 'queued')::int                        AS queued,
        -- percentile_cont drops old rows with a NULL elapsed_ms on its own -- filling them with 0 would
        -- make the lag look lower than it really is. The same as how the naver arm handles a NULL
        -- elapsed_ms.
        percentile_cont(0.9) WITHIN GROUP (ORDER BY j.elapsed_ms)::int          AS p90_ms
    FROM tubedepth.jobs j
    GROUP BY j.dataset, date_trunc('hour', coalesce(j.started_at, j.created_at))
) q;

GRANT SELECT ON needs.collector_health TO needs_runtime;
