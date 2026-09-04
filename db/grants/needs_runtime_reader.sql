-- SELECT-only privilege for analysis to read the source schemas. Neither DDL nor DML is granted.
-- Reasoning: analysis/slices/**/{load_db.py,ctx.py,q*.py} and each slice's _raw CSV headers (audit (D), T1/T2/T12).
-- Run by the superuser (platform) -- needs_migrator cannot GRANT on someone else's schema.
-- This must also run where the schema does not exist (a test container), so it only runs where it does.

SELECT format('GRANT USAGE ON SCHEMA %I TO needs_runtime', n)
FROM (VALUES ('trend_radar'), ('tubedepth')) v(n)
WHERE EXISTS (SELECT FROM pg_namespace WHERE nspname = n) \gexec

-- trend_radar: product, ranking, price, review, site-structure axes
-- tubedepth: comments, transcripts, video metadata, listings
SELECT format('GRANT SELECT ON %s TO needs_runtime', t)
FROM (VALUES
    ('trend_radar.product'),         -- p2 load_db.py:5 (source,product_key,name,brand,volume,first_seen_at,ingredients)
    ('trend_radar.rank_snapshot'),   -- p2 load_db.py:4, p3 rank_snapshot.csv, p9 brand/category
    ('trend_radar.price_point'),     -- p2 load_db.py:4 · q4_price_rank.py:18 (captured_at,price,discount_rate)
    ('trend_radar.new_product'),     -- p2 load_db.py:6 · q6_new.py:4-6 (listed_at)
    ('trend_radar.review'),          -- p1/suncare/p9 (rating,written_at,body) -- the column name is body (T3)
    ('trend_radar.review_stats'),    -- p1 review_stats.csv (review_count,pct_1..pct_5) = population denominator
    ('trend_radar.review_topic'),    -- p1 site_topic_raw.csv (topic_group,topic_name,share_pct)
    ('trend_radar.review_answer'),   -- p1 site_answer_raw.csv (question_name,answer) = daisomall's survey axis
    ('tubedepth.comments'),          -- p9 export_data.sh:5 · p3 (video_id,comment_id,like_count,text)
    ('tubedepth.transcripts'),       -- p3·suncare (video_id,language,full_text,segment_count)
    ('tubedepth.video_snapshots'),   -- p9 export_data.sh:6, p3 (title,channel_id,view_count), same as #2's "videos" (T1)
    ('tubedepth.listing_entries')    -- p3 q4_trending.py:13 (kind,target,video_id,title,channel_id)
) v(t)
WHERE to_regclass(t) IS NOT NULL \gexec

-- The second role this benefits: the stage-5 operational view needs.collector_health
-- (db/views/collector_health.sql). Because db/migrate.sh (f) creates that view with SET ROLE needs_owner
-- and the view runs with owner privilege, the role that has to read the source is needs_owner, not
-- needs_runtime (needs_runtime only reads the view).

SELECT format('GRANT USAGE ON SCHEMA %I TO needs_owner', n)
FROM (VALUES ('trend_radar'), ('tubedepth')) v(n)
WHERE EXISTS (SELECT FROM pg_namespace WHERE nspname = n) \gexec

SELECT format('GRANT SELECT ON %s TO needs_owner', t)
FROM (VALUES
    ('trend_radar.run'),        -- the commerce arm's run_id/started_at/finished_at/status
    ('trend_radar.fetch_log'),  -- the same arm's dataset/requests/ok/blocked/failed/p90_ms (status,error,elapsed_ms)
    ('trend_radar.run_source'),  -- #78: the correlated subquery that tells whether a status='partial' run is
                                -- entirely skipped
    ('trend_radar.review'),     -- #144: the two lineage views read a review mention's original text
                                -- (a 120-char excerpt of body, rating, written_at) and that review's
                                -- captured_at (the only value that links it to a collection run candidate).
                                -- needs_runtime already receives this table above, but needs_owner could
                                -- not read it (measured 2026-08-27) -- the view runs with owner privilege,
                                -- so that GRANT alone did not cover it.
    ('tubedepth.comments'),     -- #144: a comment mention's original text (a 120-char excerpt of text,
                                -- like_count, published_at) and when that comment was first seen
                                -- (first_seen_at). Same reason as the needs_runtime side.
    ('tubedepth.artifacts'),    -- #144: the artifact that carried in that comment (target,
                                -- kind='video.comments', fetched_at, byte_count). A video can have
                                -- several artifacts, and fetched_at is what tells them apart.
    ('tubedepth.jobs')          -- #77: all 12 columns of the youtube arm (dataset,state,error_code,
                                -- started_at,created_at,finished_at,elapsed_ms). #144 also reads this as
                                -- the job that made that artifact.
) v(t)
WHERE to_regclass(t) IS NOT NULL \gexec

-- Future tables are not opened automatically: DEFAULT PRIVILEGES is deliberately never granted.
-- When a new source table needs to be read, add one line here and record the reasoning (slice file:line)
-- in a comment.
-- What is not read (and why): needs_runtime never touches trend_radar.run/fetch_log/run_source or
-- tubedepth.jobs/artifacts directly -- all five are read by needs_owner only through an operational
-- view, trend_radar.review_summary, tubedepth.channel_snapshots, every one of tubedepth's internal
-- collector state tables other than jobs/artifacts (lane_health, flatten_progress, worker_control, and
-- so on), every table under cosmai.*.
