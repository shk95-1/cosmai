-- One line per stored DataLab point, with the anchor rescale of issue #90 beside its raw ratio.
--
-- NAVER scales the largest series of a request to 100 and returns the rest as a ratio of it, so two
-- requests are two different scales (contracts/formats.md, NAVER DataLab section, #44). Since #90
-- every request carries one global anchor keyword as its own keywordGroups entry, and the anchor is
-- stored like any other group. Dividing a point by the anchor point of its own request_key and
-- month is therefore the one comparison that crosses a request boundary without inventing a number.
--
-- The rescale is computed here rather than written by the collector (user decision 2026-08-26):
-- the rows keep the raw ratio, so changing the anchor never means collecting again.
--
-- Two rules this view holds, and nothing else may relax them:
--   * only within one request_key -- the join never falls back to "the anchor of that month"
--   * NULL when this request has no anchor point for that month, or the anchor's ratio is 0
--     (a NULL is a missing comparison; a filled-in one would be a plausible wrong number)
-- A NULL therefore reads as "not comparable across requests"; the raw `ratio` on the same row is
-- still comparable inside its own request_key.
--
-- The anchor join is on needs.naver_datalab_anchor (request_key, month), which the collector writes
-- one row into per request (#248, contracts/ddl/needs/009_naver_datalab_anchor.sql) -- unlike
-- naver_datalab_point's own PK (category, group_key, month), a request boundary is never overwritten
-- by a later request of the same category, so every batch keeps the anchor it was sent with. The
-- anchor row is still written into naver_datalab_point too, unchanged, for #90's readers -- this
-- view no longer reads it from there.
--
-- Not on the postgrest_anon whitelist: the portal reads no naver surface today, and
-- db/grants/postgrest_anon_needs.sql stays a whitelist (contracts/anon_exposure.md).
--
-- db/migrate.sh (f) re-applies this on every deploy. CREATE OR REPLACE only succeeds when the column
-- names, order and types stay the same, so DROP goes first -- a deploy that widens the view must not
-- stop with exit 1.

DROP VIEW IF EXISTS needs.naver_datalab_rescaled;
CREATE VIEW needs.naver_datalab_rescaled AS
SELECT
    p.category                                                        AS category,
    p.group_key                                                       AS group_key,
    p.month                                                           AS month,
    p.ratio                                                           AS ratio,
    a.ratio                                                           AS anchor_ratio,
    CASE
        WHEN a.ratio IS NULL OR a.ratio = 0 THEN NULL
        ELSE round(p.ratio / a.ratio, 6)
    END                                                               AS ratio_rescaled,
    p.request_key                                                     AS request_key,
    p.terms                                                           AS terms,
    p.captured_at                                                     AS captured_at
FROM needs.naver_datalab_point p
LEFT JOIN needs.naver_datalab_anchor a
       ON a.request_key = p.request_key
      AND a.month = p.month;

GRANT SELECT ON needs.naver_datalab_rescaled TO needs_runtime;
