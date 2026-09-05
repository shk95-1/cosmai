-- The mentions that made one metrics cell, and the original excerpt of each one (#144 paths 4, 5a, 5b).
--
-- This does not reverse A19. `need_mention`/`wish_mention` carry no `run_id` (versioning.md), and the
-- one predicate aggregation uses to pick its population is `extractor_version = ANY(...)`
-- (load_needs in analysis/aggregate/pipeline.py). "The mentions this run counted" and "other mentions of
-- the same version" are the same set by definition, so there was never a place a per-run subset could
-- come from. So this view knows nothing of run -- it puts the cell's axes out as filterable columns,
-- and which version to pick is something the screen reads off
-- `analysis_run.versions->>'extractor'` and filters by itself.
--
-- What must never be filtered on: `versions.polarity`. One extractor_version carries two polarity
-- versions -- filtering on both together for run 26 turns neg 15,452 into 8,685 (a 44 percent drop)
-- (measured in #144's judgment section). `versions.polarity` is the version the polarity stage of that
-- run wrote, not the aggregation's population.
--
-- Only an **excerpt** of the sentence goes out: cut at 120 characters, with the full length placed next
-- to it -- enough to use as evidence, not enough to reconstruct the full text (user decision
-- 2026-08-27). The original-text column does not even go out by name.
--
-- The reason is written exactly. It is **not** that anon cannot see the review body -- that line is
-- already gone in production: postgrest_anon is a member of trend_radar_reader and reads
-- `trend_radar.review.body` straight through (coordinator measured this 2026-08-27).
-- db/grants/postgrest_anon_needs.sql only governs the needs schema. The reason this view truncates is
-- **to keep this view from becoming a channel for the original text**: this is a spot where thousands
-- of mentions ride out of one metrics cell, and without an excerpt this would effectively become an
-- exit for dumping the original text. That separate exposure (anon reading trend_radar at all) is not
-- this issue's business.
--
-- Called with no filter, this scans every need_mention row (183,571 in production). The screen always
-- calls it narrowed to the cell's axes (PGRST_DB_MAX_ROWS=1000, paging on through a mention_id order),
-- and this view is built for that use.
--
-- LIKE is not used: the driver running this file (psycopg) reads that wildcard character as a
-- placeholder and dies on it -- even a single one sitting inside a comment does it (the spot that bit
-- db/views/pipeline_health.sql).
--
-- db/migrate.sh (f) does DROP + CREATE on every deploy.

DROP VIEW IF EXISTS needs.mention_lineage;
CREATE VIEW needs.mention_lineage AS
WITH mention AS (
    SELECT
        'need'::text                            AS kind,
        m.mention_id,
        m.extractor_version,
        m.src,
        m.site,
        m.ref,
        NULL::text                              AS parent_hint,
        coalesce(m.category, '')                AS category,
        m.need_key,
        -- A17: only a scope='all' rollup folds synonyms through needs.need_key.canonical. Both values
        -- have to sit side by side for the raw category column and the rollup column to split apart in
        -- the same view.
        coalesce(k.canonical, m.need_key)       AS need_key_rollup,
        m.month,
        -- The value of the product axis (_product in analysis/aggregate/__init__.py): product_ref if
        -- present, else source_product_key, else '' -- that '' is the category-total row.
        coalesce(nullif(m.product_ref, ''), nullif(m.source_product_key, ''), '') AS product_axis,
        NULL::text                              AS wish_class,
        ''::text                                AS format_first,
        ''::text                                AS attribute_first,
        ''::text                                AS brand,
        m.polarity,
        NULL::int                               AS like_count,
        m.observed_at,
        m.observed_at_resolution,
        m.sentence
    FROM needs.need_mention m
    LEFT JOIN needs.need_key k ON k.need_key = m.need_key
    UNION ALL
    SELECT
        'wish',
        w.mention_id,
        w.extractor_version,
        w.src,
        -- wish_mention has no site column. Comments are decided here since youtube is the only source,
        -- but the review branch has no value to say which site it is, so it is NULL and, because of
        -- that, never reaches the original text either (doc_kind below).
        CASE WHEN w.src = 'yt_comment' THEN 'youtube' END,
        w.ref,
        w.video_id,
        '',
        NULL,
        NULL,
        w.month,
        coalesce(w.product_ref, ''),
        w.wish_class,
        -- format arrives as up to 3 values joined by ';' and the first is the primary value (A12,
        -- _first in aggregate/__init__.py).
        coalesce(split_part(w.format, ';', 1), ''),
        coalesce(split_part(w.attribute, ';', 1), ''),
        coalesce(w.brand, ''),
        -- wish 는 불만/만족 축이 없다. 없는 값을 '중립' 같은 것으로 채우지 않는다.
        NULL,
        w.like_count,
        w.observed_at,
        w.observed_at_resolution,
        w.sentence
    FROM needs.wish_mention w
),
located AS (
    SELECT
        m.*,
        -- ref is product_key/review_key for a review and video_id/comment_id for a comment (the
        -- comment in 001_needs.sql). A branch with no original-text table (yt_transcript, naver_blog)
        -- and a wish review whose site is unknown come out NULL here, and the two joins below never
        -- fire at all for them -- the row still stands and only doc_found is false.
        CASE WHEN m.src = 'review' AND m.site IS NOT NULL THEN 'review'
             WHEN m.src = 'yt_comment' THEN 'yt_comment' END               AS doc_kind,
        CASE WHEN strpos(m.ref, '/') > 0 THEN split_part(m.ref, '/', 1)
             ELSE m.parent_hint END                                        AS doc_parent,
        CASE WHEN strpos(m.ref, '/') > 0 THEN split_part(m.ref, '/', 2)
             ELSE m.ref END                                                AS doc_key
    FROM mention m
)
SELECT
    l.kind,
    l.mention_id,
    l.extractor_version,
    l.src,
    l.site,
    l.ref,
    l.category,
    l.need_key,
    l.need_key_rollup,
    l.month,
    l.product_axis,
    l.wish_class,
    l.format_first,
    l.attribute_first,
    l.brand,
    l.polarity,
    l.like_count,
    l.observed_at,
    l.observed_at_resolution,
    left(l.sentence, 120)                       AS sentence_excerpt,
    -- The fact that it was cut is never hidden -- the full length has to sit next to it for a reader to
    -- know it's an excerpt.
    length(l.sentence)                          AS sentence_chars,
    l.doc_kind,
    l.doc_parent,
    l.doc_key,
    (r.review_key IS NOT NULL OR c.comment_id IS NOT NULL) AS doc_found,
    left(coalesce(r.body, c.text), 120)         AS doc_excerpt,
    length(coalesce(r.body, c.text))            AS doc_chars,
    coalesce(r.written_at, c.published_at)      AS doc_at,
    r.rating                                    AS doc_rating,
    c.like_count                                AS doc_like_count
FROM located l
-- review's PK is (source, review_key). Without also filtering on site, the same review_key from a
-- different site attaches too and one mention turns into several rows.
LEFT JOIN trend_radar.review r
       ON l.doc_kind = 'review' AND r.source = l.site AND r.review_key = l.doc_key
LEFT JOIN tubedepth.comments c
       ON l.doc_kind = 'yt_comment' AND c.video_id = l.doc_parent AND c.comment_id = l.doc_key;

GRANT SELECT ON needs.mention_lineage TO needs_runtime;
-- The screen asks PostgREST as anon. This GRANT would not survive sitting in
-- db/grants/postgrest_anon_needs.sql: that file is migrate stage (d), while dropping and recreating the
-- view is stage (f), so the new object does not carry the old GRANT along with it. The view owns its
-- own grants (#158 -- the screen was returning 401).
GRANT SELECT ON needs.mention_lineage TO postgrest_anon;
-- Grants changed, so this wakes up PostgREST's schema cache. Stage (d)'s NOTIFY runs before this view
-- even exists.
NOTIFY pgrst, 'reload schema';
