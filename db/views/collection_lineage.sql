-- From one raw line to the collection run that gathered it and the fetch it rests on (#144 paths 6a, 6b, 7).
--
-- The review branch's last hop is this ladder's **real point of loss**. `trend_radar.review` carries no
-- run_id, and that table is someone else's archived one so upstream cannot add it. Only three values
-- bridge the gap: `captured_at` (the run's hour bucket, collectors/commerce/models.py -- "the run's hour
-- bucket, not the wall clock"), `run.sources`, `run.datasets`.
--
-- All three predicates are needed, and `datasets` among them **differs per site**. Filtering on bucket
-- and source alone lets the hourly `ranking` run fill every bucket and become a candidate for every
-- review (the commerce cron in contracts/entrypoints.md). Narrowing `dataset` to `{review, review_low}`
-- irrespective of site is wrong in the opposite direction.
-- Measured in production (30,043 reviews):
--   sources only              single  9,327, candidate 20,716, unknown 0  <- unknown is unreachable
--   dataset narrowed flatly   unknown 2,284 is **entirely glowpick** (63.5 percent of that site's 3,597)
-- The second reading quietly misclassifies real single matches as 'unknown' -- a lie of the same size,
-- just pointed the other way from the first.
--
-- The split comes from the gate inside `parse()`. oliveyoung's `_parse_ranking` splits follow by dataset
-- (`wants_reviews`/`wants_low`/`wants_stats`) and daisomall also splits on `wants_reviews`, but
-- **glowpick has no gate**: it only uses `payload.fetch.dataset` to carve out NEW_PRODUCT, and after
-- that it calls `_reviews(...)` unconditionally -- because ranking and review are the same category
-- page there, a fact written right in the class comment. The cron runs ranking hourly and review once
-- a day, and review upsert is DO NOTHING (storage/repository.py), so **the first writer is usually the
-- hourly ranking run**.
-- `review_stats` never carries one on any site: `_stats_fetch`/`_summary_fetch` only follow that path
-- and never build a review-body fetch -- strpos matching on 'review' would pull that in as a candidate too.
--
-- So the `review_body_dataset` list below is not something this file invented -- it **mirrors the
-- collector's own declaration** (each source's `review_body_datasets: ClassVar[frozenset[Dataset]]`).
-- If the two disagree, tests/test_collection_lineage_view.py fails, and if that declaration disagrees
-- with `parse()`, tests/collectors/commerce/test_review_body_datasets.py fails by replaying its recorded
-- fixtures. Adding a site without registering it is rejected outright
-- (collectors/commerce/registry.py's `_REQUIRED_ATTRS`).
--
-- Why the list unrolls into an array: today one run has one dataset (the Dataset docstring in models.py),
-- but the column's format is `",".join(...)` (collectors/commerce/storage/db.py's RunLog.start) -- writing
-- it as IN would someday quietly drop a run that carries several datasets. regexp_split_to_array is used
-- because it strips the whitespace around commas in one pass -- that format does not forbid whitespace --
-- and because it is cheaper than a correlated subquery calling btrim per element (that shape ran one
-- subplan per run and cost an extra 2ms per query).
--
-- So this view reports **candidates as candidates**. match splits into single/candidate/unknown and the
-- screen shows exactly that (user decision 2026-08-27). Collapsing it to one value would assert a fact
-- the screen does not have, and hiding it would make "the collection never reached it" look the same as
-- "that document doesn't exist" -- a document with no candidate still leaves one row behind.
--
-- The youtube branch reaches all the way through: comment -> that video's `video.comments` artifact ->
-- the job that made that artifact (jobs). A video can have several artifacts (measured 3,378/3,922), so
-- the only thing that tells them apart is fetched_at, and first priority goes to the artifact closest to
-- when the comment was first seen (first_seen_at).
--
-- **The filter must reach down to a single document** -- otherwise the fetch_log aggregate below runs
-- over every document instead of the handful of rows that survive (production EXPLAIN: 1.3 seconds and
-- 208k buffers per hit, aggregate loops=45,255). Two things caused that and both are fixed here:
--   1. A window kept outside leaves the filter sitting above it -> candidate computation was pushed down
--      into a **LATERAL scoped to one review/comment**. That is what keeps review_pkey/comments_pkey alive.
--   2. Across the two arms of a UNION ALL, **a predicate does not push down into a branch whose column
--      has a different type there** (compare_tlist_datatypes). tubedepth is varchar and trend_radar is
--      text, and doc_parent/doc_key were exactly that case -> the youtube branch got a ::text cast.
--      Removing that cast kills only the performance, quietly.
--
-- Cost note: tubedepth.jobs has no (target, kind) index (app.tubedepth.sql). The LATERAL below assumes
-- it only ever runs over the handful of artifacts left after narrowing to one document -- this view is
-- not meant to be called without a filter.
--
-- LIKE is avoided for the same reason as the header of db/views/mention_lineage.sql (the psycopg
-- placeholder).
-- db/migrate.sh (f) does DROP + CREATE on every deploy.

DROP VIEW IF EXISTS needs.collection_lineage;
CREATE VIEW needs.collection_lineage AS
-- A mirror of the collector's own declaration. One row = "this site's run of this dataset writes a
-- body into trend_radar.review". The source of truth is `review_body_datasets` in
-- collectors/commerce/sources/*.py, and a test checks the two against each other.
WITH review_body_dataset (site, dataset) AS (
    VALUES ('daisomall', 'review'),
           ('glowpick', 'ranking'),
           ('glowpick', 'review'),
           ('oliveyoung', 'review'),
           ('oliveyoung', 'review_low')
),
-- Only runs that actually wrote a review body, and **for which site**. If body_sites is empty, that
-- run is not a candidate for any review. MATERIALIZED keeps this narrowing from re-running per review --
-- the LATERAL below only scans that result (a slice of the 240 runs in production).
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
    -- The document still gets one row even with no candidate -- rank is 1 and the collection columns
    -- are empty, which reads as 'unknown'.
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
      -- Per site: the same run can be a candidate for a glowpick review and not a candidate for an
      -- oliveyoung review.
      AND r.source = ANY (run.body_sites)
) rc ON true
-- Path 7: what that run actually requested for that site. Without narrowing by source, another site's
-- requests collected by the same run would read as evidence for this review. Counting only 2xx as ok
-- follows the same line as collector_health.
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
    -- ::text is not decoration. Across the two arms of a UNION ALL, **a predicate does not push down
    -- into a branch whose column has a different type there** (PostgreSQL's compare_tlist_datatypes
    -- marks only that column unsafe). tubedepth's side is varchar and trend_radar's side is text, and
    -- doc_parent/doc_key were exactly that case -- without the cast the eq filter sits on top of the
    -- Append and only fires after scanning both branches whole -- that is the other half of F2.
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
    -- Without filtering by kind, the same video's video.metadata artifact would count as the artifact
    -- that collected the comment.
    SELECT
        a.identifier, a.kind, a.fetched_at, a.byte_count,
        count(*) OVER ()::int                                     AS candidate_count,
        row_number() OVER (ORDER BY abs(extract(epoch FROM a.fetched_at - c.first_seen_at)),
                           a.identifier)::int                     AS candidate_rank
    FROM tubedepth.artifacts a
    WHERE a.target = c.video_id AND a.kind = 'video.comments'
) cc ON true
-- The job that made that artifact. artifacts has no column pointing at a job, so the only link is
-- (target, kind) plus timing, and the nearest job finished before that artifact was made is the one
-- chosen. If none can be chosen the result is NULL, and the artifact row itself still stands.
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
-- The view owns its own grants (#158) -- the same reason as the header of db/views/mention_lineage.sql.
GRANT SELECT ON needs.collection_lineage TO postgrest_anon;
NOTIFY pgrst, 'reload schema';
