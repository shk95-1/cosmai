-- One row for one stage's "state right now" (#138). collector_health/analysis_health are a *log*, one
-- row per run, and cannot answer "what's stuck right now" -- this view folds that log down to one
-- latest row per stage and checks it against pipeline_stage's expected interval.
--
-- Two facts are never folded into one. "Hasn't run" (freshness) and "ran but failed"
-- (last_run_status) are orthogonal: a stage that failed three days ago and hasn't run since would show
-- as only one or the other in a single enum. The screen colors it by whichever is worse but shows both
-- (#139).
--
-- Why freshness's scale is a multiple of expected_interval rather than an absolute value: intervals
-- range from 5 minutes (youtube work) to a month (naver datalab), and a constant margin is guaranteed
-- to be wrong on one side or the other.
--
-- never is not stalled. naver datalab/blog have zero rows in the table but a cron line exists, so they
-- are enabled (#138 user decision) -- the values are filled in by fork cosmai-import-ydc#53. The
-- screen's banner does not count never: a dashboard that is always red is a dashboard no one watches.
--
-- Premise: youtube's failed cannot be trusted yet (#112 -- a bucket that is entirely cancellations
-- reads as failed). This view passes collector_health's value straight through, and #112 owns the fix.
--
-- db/migrate.sh (f) re-applies this on every deploy. CREATE OR REPLACE only succeeds when the columns
-- stay the same, so DROP goes first -- a deploy that widens the view must not stop with exit 1.

DROP VIEW IF EXISTS needs.pipeline_health;
CREATE VIEW needs.pipeline_health AS
WITH runs AS (
    -- The collector's three arms. An old row with an empty dataset cannot say which stage it is, so it
    -- is left out (rows from before #101).
    SELECT
        collector || ':' || dataset                AS stage_key,
        coalesce(finished_at, started_at)          AS at,
        status,
        requests, ok, blocked, failed, p90_ms
    FROM needs.collector_health
    WHERE dataset IS NOT NULL AND dataset <> ''
    UNION ALL
    -- The two analysis lines. An incremental pass is told apart by missing= in the note
    -- (contracts/entrypoints.md §Analysis) -- a cron line does not tell them apart, and stage carries an
    -- implementation version and cannot be used as it stands.
    -- eval:* and trend-quarter:* are not cron stages and never reach this view.
    SELECT
        CASE WHEN stage = 'analyze:all' THEN 'analyze:all' ELSE 'analyze:polarity_missing' END,
        coalesce(finished_at, started_at),
        status,
        NULL::int, NULL::int, NULL::int, NULL::int, NULL::int
    FROM needs.analysis_health
    -- LIKE is not used: the driver running this file (psycopg) reads that wildcard character as a
    -- placeholder and dies on it -- even a single one sitting inside a comment does it. starts_with/strpos
    -- mean the same thing without using that character.
    WHERE stage = 'analyze:all'
       OR (starts_with(stage, 'analyze:polarity:') AND strpos(note, 'missing=') > 0)
),
last_run AS (
    SELECT DISTINCT ON (stage_key) * FROM runs ORDER BY stage_key, at DESC NULLS LAST
),
-- This asks "did it run", not "did it run cleanly". partial belongs here since it ran and gathered most
-- of what it should -- how well it finished is what last_run_status says alongside it, and that is why
-- the two columns are never folded into one (#154).
-- What does not belong: yielded (pushed entirely out by a source lock, gathered nothing, #78), failed,
-- blocked. Drawing this line wrong lets a stage that runs on schedule every day but is always partial
-- harden into stalled two days later and stay red forever -- the same failure mode that made #138 drop
-- never from the banner.
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
    -- If it has never succeeded, the question "how overdue is it" does not even apply -- the answer is
    -- NULL, not 0.
    CASE WHEN o.at IS NULL THEN NULL
         ELSE greatest(now() - o.at - s.expected_interval, interval '0') END AS overdue_by,
    CASE WHEN NOT s.enabled                              THEN 'disabled'
         WHEN o.at IS NULL                               THEN 'never'
         WHEN now() - o.at <= s.expected_interval        THEN 'ok'
         WHEN now() - o.at <= 2 * s.expected_interval    THEN 'late'
         ELSE 'stalled' END                                     AS freshness,
    -- The last run's request statistics. What keeps "it ran, but half were 403" from reading as ok.
    -- The analysis arm makes no outside fetch, so all five columns are NULL for it.
    r.requests, r.ok, r.blocked, r.failed, r.p90_ms
FROM needs.pipeline_stage s
LEFT JOIN last_run r USING (stage_key)
LEFT JOIN last_ran o USING (stage_key);

GRANT SELECT ON needs.pipeline_health TO needs_runtime;
-- The screen asks PostgREST as anon. This GRANT would not survive sitting in
-- db/grants/postgrest_anon_needs.sql: that file is migrate stage (d), while dropping and recreating the
-- view is stage (f), so the new object does not carry the old GRANT along with it. The view owns its
-- own grants (#158 -- the screen was returning 401).
GRANT SELECT ON needs.pipeline_health TO postgrest_anon;
-- Grants changed, so this wakes up PostgREST's schema cache. Stage (d)'s NOTIFY runs before this view
-- even exists, so by itself it never sees the new GRANT and 401 stays as it was.
NOTIFY pgrst, 'reload schema';
