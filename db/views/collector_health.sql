-- One line per result a collector run produced for one dataset. It emits the 12 columns of
-- contracts/entrypoints.md §Common operations view with the same names, order and types (P16's table has to
-- come out of this one view).
--
-- Four arms feed it -- commerce (trend_radar's run+fetch_log), naver (naver_run+naver_fetch_log),
-- youtube's job queue (tubedepth.jobs) and youtube's passes that are not a queue
-- (tubedepth.collector_runs). #77 added youtube: the three reasons it was left out at stage 3 (no run,
-- no column that times the lag, a different dataset vocabulary) were all removed by #100/#101/#102.
--
-- #280 added the fourth. `prune` writes no job row at all, so no row of this view ever carried dataset
-- `prune` and a prune that died every night read the same as one that succeeded; and since #275
-- `flatten` writes a job row for its *failures* alone, so one failure in five hundred read as 0 percent
-- ok -- a count with no denominator (that review's N1). The run row is both: the dataset's own row, and
-- the denominator beside the failures.
--
-- queued is NULL on the first two arms: both are batch workers a cron calls, so there is no such thing
-- as a wait queue for them. Only youtube's job-queue arm gives a number -- 0 (the queue is empty) and
-- NULL (there is no queue) have to read differently in the table. The run arm is NULL for the same
-- reason the batch arms are: a `prune` or `flatten` pass has no waiting queue in front of it, and
-- writing 0 there would claim it has one and that it is empty.
--
-- elapsed_ms means something different per arm. commerce and naver mean one fetch's round trip, while
-- youtube means one job's whole wall clock (claim->finish) (#101). A job answered from cache never fetches,
-- so there is no round trip to measure. So youtube's p90_ms is not "how slow was the request" but "how long
-- did one unit of work take", and for the same reason requests is not a count of HTTP requests but of
-- finished jobs -- a job answered from cache counts as 1 (jobs has no column marking a cache hit). The
-- contract's §Common operations view says the same thing.
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
        WHEN q.failures > 0 THEN 'failed'
        -- requests counts succeeded, failed and cancelled. Reaching here with a finished job therefore
        -- means every finished job was cancelled; queued/running work exited through in_flight above.
        WHEN q.requests > 0 THEN 'cancelled'
        -- A nonempty group cannot reach this branch while jobs.state keeps its declared vocabulary.
        ELSE 'running'
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
    -- #280: flatten's failure records are not jobs. #275 put one row per unflattenable artifact in
    -- this table and this arm counted them under dataset `flatten`, where they were the only rows
    -- that dataset ever had -- so the bucket read `failed` whatever the pass had actually flattened.
    -- The pass's own row carries those failures now, beside the number they are a share of, and
    -- leaving the records here as well would count each of them twice. They stay queryable in
    -- `jobs`, which is where the reason for one artifact belongs.
    WHERE j.kind <> 'flatten.artifact'
    GROUP BY j.dataset, date_trunc('hour', coalesce(j.started_at, j.created_at))
) q

UNION ALL

-- #280: one row per `cosmai collect youtube` pass of a dataset that is not a queue of jobs -- today
-- `prune` and `flatten`. This arm is shaped like naver's rather than like the bucket above: the pass
-- has an identity, a status of its own and its own counts, so nothing has to be rebuilt out of time.
--
-- `requests` is this pass's units of work, and what a unit is belongs to the dataset (the DDL file
-- says so at the column): `prune` counts batches, `flatten` counts artifacts examined. That is the
-- same licence the header above takes for the job arm -- on this collector `requests` has never been
-- a count of HTTP requests.
--
-- `blocked` is 0 rather than NULL, and truthfully: neither pass goes near a source, so nothing can
-- refuse them. That makes the gap `requests - ok - blocked - failed` exactly the pass's `skipped` --
-- the artifacts flatten recorded as unflattenable -- which is the same slot the contract already
-- gives a 404 on the commerce arm, so the screen needs no thirteenth column to show it.
--
-- p90_ms is NULL: this collector's passes are stamped with one clock reading each
-- (collectors/youtube/cli.py's `_record_run`), so there is no per-unit duration to take a
-- percentile of. NULL is what the other arms already use for "not measured" and percentile_cont
-- drops such rows rather than reading them as zero.
SELECT
    'youtube'::text,
    r.dataset::text,
    r.identifier::text,
    r.started_at,
    r.finished_at,
    r.status::text,
    r.attempted,
    r.succeeded,
    0,
    r.failed,
    NULL::int,
    NULL::int
FROM tubedepth.collector_runs r;

GRANT SELECT ON needs.collector_health TO needs_runtime;
