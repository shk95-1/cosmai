-- One row per product with launch evidence: the interval its claims allow, and what was counted
-- to get there (#282, contracts/interfaces.md §Launch evidence).
--
-- This view stops where the evidence stops. It does **not** answer `new_3m`/`not_new`/`unknown`,
-- because the verdict is relative to a reference date that is a parameter of the read and a view
-- takes no parameter; the rule table is a pure function over these columns in analysis/launch and
-- the aggregate stage calls it (#125). Putting the verdict here would mean either freezing
-- `current_date` into a stored derivation or writing the rule table a second time in SQL.
--
-- The one duplication this pair does carry is the interval itself: the same widening and the same
-- tightest-bound rule exist in analysis.launch.launch_interval, because a SQL reader needs them
-- here and the rule table needs them there. tests/test_product_launch_view.py compares the two
-- over the same claims, and the excluded-axis list below against the module's constant -- a mirror
-- nobody compares is just a second implementation.
--
-- The five rules this view holds:
--   * a month claim is worth its whole month (first day for a lower bound, last day for an upper
--     one), so a bound only ever moves outward -- mixed precision costs a tier and never buys one
--   * `latest` is the tightest upper bound **the evidence names** (the earliest of them), and
--     `earliest` folds the lower side TWICE (rule version 1.1, #283 review): each axis contributes
--     the EARLIEST edge it names -- several filings of one product line are one statement and only
--     its first is certainly true, a re-filing or a refill/set filing of a 2021 line does not say
--     the launch was not before 2026 -- and the LATEST of those per-axis bounds is the interval's,
--     because two axes are two independent statements and both have to hold. NULL means no claim of
--     that side exists, which is not the same answer as a wide interval and the rule table treats it
--     as a different one. The reference-date clamp on the upper end is the verdict's, not this
--     view's -- a view takes no reference date
--   * `earliest_match` is the join strength of whichever claim ended up setting `earliest`, and on
--     a tie -- inside an axis or between two of them -- the surer one ('exact' sorts before
--     'partial'). The rule table reads it: a tier needs it to be 'exact'
--   * an axis this rule version does not read is counted in `excluded_claims` and moves no bound
--     (`vendor_title_tag`: marketing text on one vendor, user decision 2026-09-20, #283 axis 5)
--   * a product whose only claims are excluded still gets a row, with claims = 0 -- "recorded and
--     not read" is a state worth being able to see. A product with no claims at all gets no row,
--     and a reader takes a missing row as row 1 of the rule table (`unknown`)
--
-- The columns are exactly the fields of `LaunchIntervalRow` (contracts/interfaces.md), in order.
--
-- Not on the postgrest_anon whitelist: no screen reads this, and
-- db/grants/postgrest_anon_needs.sql stays a whitelist (contracts/anon_exposure.md).
--
-- db/migrate.sh (f) re-applies this on every deploy. CREATE OR REPLACE only succeeds when the
-- column names, order and types stay the same, so DROP goes first -- a deploy that widens the view
-- must not stop with exit 1.

DROP VIEW IF EXISTS needs.product_launch;
CREATE VIEW needs.product_launch AS
WITH claim AS (
    SELECT
        e.product_ref                                                      AS product_ref,
        e.direction                                                        AS direction,
        e.match_strength                                                   AS match_strength,
        e.axis                                                             AS axis,
        e.axis <> ALL (ARRAY['vendor_title_tag'])                          AS in_rule,
        CASE WHEN e.claimed_precision = 'month'
             THEN date_trunc('month', e.claimed_on)::date
             ELSE e.claimed_on END                                         AS lower_edge,
        CASE WHEN e.claimed_precision = 'month'
             THEN (date_trunc('month', e.claimed_on) + interval '1 month')::date - 1
             ELSE e.claimed_on END                                         AS upper_edge
    FROM needs.product_launch_evidence e
),
-- The first fold: one axis's own lower bound is the EARLIEST edge it names, with that claim's
-- strength and, on a tie, the surer one ('exact' < 'partial').
axis_lower AS (
    SELECT
        product_ref,
        min(lower_edge)                                                       AS lower_edge,
        (array_agg(match_strength ORDER BY lower_edge ASC, match_strength ASC))[1] AS match_strength
    FROM claim
    WHERE in_rule AND direction IN ('not_before', 'at')
    GROUP BY product_ref, axis
),
-- The second: across axes the tightest wins, because each of them has to hold on its own.
lower_bound AS (
    SELECT
        product_ref,
        max(lower_edge)                                                       AS earliest,
        (array_agg(match_strength ORDER BY lower_edge DESC, match_strength ASC))[1] AS earliest_match
    FROM axis_lower
    GROUP BY product_ref
)
-- LEFT JOIN, not an inner one: a product whose only claims are upper bounds or excluded ones has no
-- row in `lower_bound` and still gets a view row, with `earliest` NULL.
SELECT
    c.product_ref,
    b.earliest                                                                   AS earliest,
    b.earliest_match                                                             AS earliest_match,
    min(c.upper_edge) FILTER (WHERE c.in_rule AND c.direction IN ('not_after', 'at')) AS latest,
    count(*) FILTER (WHERE c.in_rule)::int                                       AS claims,
    count(*) FILTER (WHERE c.in_rule AND c.direction IN ('not_before', 'at'))::int AS lower_claims,
    count(*) FILTER (WHERE c.in_rule AND c.direction IN ('not_after', 'at'))::int  AS upper_claims,
    count(*) FILTER (WHERE NOT c.in_rule)::int                                   AS excluded_claims
FROM claim c
LEFT JOIN lower_bound b ON b.product_ref = c.product_ref
GROUP BY c.product_ref, b.earliest, b.earliest_match;

GRANT SELECT ON needs.product_launch TO needs_runtime;
