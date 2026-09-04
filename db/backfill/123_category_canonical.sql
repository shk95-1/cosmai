-- #123 backfill: restores product_denominator.category to its canonical form.
--
-- What this fixes
--   analysis/aggregate/ranking.py:denominators() 가 사이트 카테고리를 leaf 로 잘라 적었다
--   ('01 > 선케어 > 선블록' → '선블록'). metrics_need.scope 는 need_mention.category 에서 나오고
--   그쪽은 원문 그대로라, 두 문자열이 절대 만나지 못해 카테고리 scope 가 분모를 하나도 못 받았다.
--   정본은 contracts/formats.md §카테고리 표기 (A21) 가 못박는다. 코드는 같은 PR 에서 고쳤으므로
--   앞으로 쌓이는 행은 이 파일 없이도 정본이다 — 이 파일은 이미 쌓인 행만 되돌린다.
--
-- Why this does not use a short-form -> hierarchical lookup table
--   leaf → 경로는 함수가 아니다('블러셔' 는 glowpick 의 '블러셔' 이면서 oliveyoung 의
--   '02 > 베이스 메이크업 > 블러셔' 다). 그래서 문자열 대응표 대신 (source, product_key) 로
--   원천을 다시 읽는다 — 코드가 다음 run 에 쓸 값과 정확히 같은 값이고, 대응이 1:1 이 아닌 값은
--   애초에 생기지 않는다. daisomall 의 '뷰티/위생' 은 그 사이트가 발행한 경로 전체라 이미 정본이고
--   바뀌지 않는다.
--
-- Who runs this and how (STATE.md §3 -- an UPDATE on the production DB is run one command at a time
-- from the coordinator session)
--   docker exec -i shared-postgres \
--     psql -U needs_runtime -d app -X -v ON_ERROR_STOP=1 < db/backfill/123_category_canonical.sql
--   needs_runtime is the only role with UPDATE on needs.* and SELECT on trend_radar.rank_snapshot
--   (db/bootstrap.sql -- db/grants/needs_runtime_reader.sql). Fits inside statement_timeout 30s /
--   transaction_timeout 60s -- 208 rows measured as the target on 2026-08-27.
--
-- Idempotent: the IS DISTINCT FROM guard never touches a row that is already canonical. A second run
-- affects 0 rows.
--   The seed (db/seed/data/slice-p1-category-gap/product_denominator.csv) is left untouched, since it
--   is a past slice's output frozen in the short form (its golden is pinned to that form). Re-loading
--   that seed into production would bring 38 rows back in the short form -- at that point, running
--   this file again is the fix.

\set ON_ERROR_STOP on
BEGIN;

\echo '--- Before count (measured 2026-08-27: 4561 rows / 809 with category / 3752 NULL)'
SELECT source,
       count(*) AS rows_total,
       count(category) AS rows_with_category,
       count(*) FILTER (WHERE category IS NULL) AS rows_null
FROM product_denominator GROUP BY 1 ORDER BY 1;

\echo '--- Before count: what changes and how many rows (measured 2026-08-27: 208 rows, all oliveyoung short form -> path)'
SELECT d.source, d.category AS before, latest.category_name AS after, count(*)
FROM product_denominator d
JOIN (
    SELECT DISTINCT ON (source, product_key) source, product_key, category_name
    FROM trend_radar.rank_snapshot
    WHERE category_name IS NOT NULL
    ORDER BY source, product_key, captured_at DESC
) latest ON latest.source = d.source AND latest.product_key = d.product_key
WHERE d.category IS DISTINCT FROM latest.category_name
GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC;

\echo '--- Backfill'
UPDATE product_denominator d
SET category = latest.category_name
FROM (
    SELECT DISTINCT ON (source, product_key) source, product_key, category_name
    FROM trend_radar.rank_snapshot
    WHERE category_name IS NOT NULL
    ORDER BY source, product_key, captured_at DESC
) latest
WHERE d.source = latest.source
  AND d.product_key = latest.product_key
  AND d.category IS DISTINCT FROM latest.category_name;

\echo '--- After count: the remaining disagreement must be 0'
SELECT count(*) AS still_disagreeing
FROM product_denominator d
JOIN (
    SELECT DISTINCT ON (source, product_key) source, product_key, category_name
    FROM trend_radar.rank_snapshot
    WHERE category_name IS NOT NULL
    ORDER BY source, product_key, captured_at DESC
) latest ON latest.source = d.source AND latest.product_key = d.product_key
WHERE d.category IS DISTINCT FROM latest.category_name;

\echo '--- After count: did the short form survive (must be 0 -- the 2026-08-27 backfill simulation was 0 rows)'
SELECT source, count(*) AS leaf_shaped_left
FROM product_denominator
WHERE source = 'oliveyoung' AND category IS NOT NULL AND category NOT LIKE '%>%'
GROUP BY 1;

\echo '--- After count: does a mention carry the denominator category as the same string too (must be 0 -- simulation was 0)'
SELECT count(DISTINCT (d.source, d.category)) AS categories_no_mention_carries
FROM product_denominator d
WHERE d.category IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM need_mention m WHERE m.site = d.source AND m.category = d.category);

COMMIT;
