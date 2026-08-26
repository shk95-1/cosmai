-- #123 이관: product_denominator.category 를 정본 표기로 되돌린다.
--
-- 무엇을 고치나
--   analysis/aggregate/ranking.py:denominators() 가 사이트 카테고리를 leaf 로 잘라 적었다
--   ('01 > 선케어 > 선블록' → '선블록'). metrics_need.scope 는 need_mention.category 에서 나오고
--   그쪽은 원문 그대로라, 두 문자열이 절대 만나지 못해 카테고리 scope 가 분모를 하나도 못 받았다.
--   정본은 contracts/formats.md §카테고리 표기 (A21) 가 못박는다. 코드는 같은 PR 에서 고쳤으므로
--   앞으로 쌓이는 행은 이 파일 없이도 정본이다 — 이 파일은 이미 쌓인 행만 되돌린다.
--
-- 왜 짧은 형 → 계층형 대응표를 쓰지 않나
--   leaf → 경로는 함수가 아니다('블러셔' 는 glowpick 의 '블러셔' 이면서 oliveyoung 의
--   '02 > 베이스 메이크업 > 블러셔' 다). 그래서 문자열 대응표 대신 (source, product_key) 로
--   원천을 다시 읽는다 — 코드가 다음 run 에 쓸 값과 정확히 같은 값이고, 대응이 1:1 이 아닌 값은
--   애초에 생기지 않는다. daisomall 의 '뷰티/위생' 은 그 사이트가 발행한 경로 전체라 이미 정본이고
--   바뀌지 않는다.
--
-- 누가 어떻게 돌리나 (STATE.md §3 — 운영 DB 의 UPDATE 는 코디네이터 세션이 한 명령씩)
--   docker exec -i shared-postgres \
--     psql -U needs_runtime -d app -X -v ON_ERROR_STOP=1 < db/backfill/123_category_canonical.sql
--   needs_runtime 은 needs.* 에 UPDATE 를, trend_radar.rank_snapshot 에 SELECT 를 가진 유일한 롤이다
--   (db/bootstrap.sql · db/grants/needs_runtime_reader.sql). statement_timeout 30s / transaction_timeout
--   60s 안에 든다 — 2026-08-27 실측 대상 208행.
--
-- 멱등: IS DISTINCT FROM 가드가 이미 정본인 행을 건드리지 않는다. 두 번째 실행은 0행이다.
--   시드(db/seed/data/slice-p1-category-gap/product_denominator.csv)는 짧은 형 그대로 얼려 둔
--   과거 슬라이스 산출물이라 손대지 않았다(그 골든이 그 표기로 고정돼 있다). 그 시드를 운영에 다시
--   부으면 38행이 짧은 형으로 되살아난다 — 그때는 이 파일을 다시 돌리면 된다.

\set ON_ERROR_STOP on
BEGIN;

\echo '--- 사전 카운트 (2026-08-27 실측: 4561 행 / category 있는 809 / NULL 3752)'
SELECT source,
       count(*) AS rows_total,
       count(category) AS rows_with_category,
       count(*) FILTER (WHERE category IS NULL) AS rows_null
FROM product_denominator GROUP BY 1 ORDER BY 1;

\echo '--- 사전 카운트: 무엇이 몇 행 바뀌나 (2026-08-27 실측: 208 행, 전부 oliveyoung 짧은 형 → 경로)'
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

\echo '--- 이관'
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

\echo '--- 사후 카운트: 남은 불일치는 0 이어야 한다'
SELECT count(*) AS still_disagreeing
FROM product_denominator d
JOIN (
    SELECT DISTINCT ON (source, product_key) source, product_key, category_name
    FROM trend_radar.rank_snapshot
    WHERE category_name IS NOT NULL
    ORDER BY source, product_key, captured_at DESC
) latest ON latest.source = d.source AND latest.product_key = d.product_key
WHERE d.category IS DISTINCT FROM latest.category_name;

\echo '--- 사후 카운트: 짧은 형이 남았나 (0 이어야 한다 — 2026-08-27 이관 시뮬레이션 0행)'
SELECT source, count(*) AS leaf_shaped_left
FROM product_denominator
WHERE source = 'oliveyoung' AND category IS NOT NULL AND category NOT LIKE '%>%'
GROUP BY 1;

\echo '--- 사후 카운트: 분모의 category 를 언급도 같은 문자열로 들고 있나 (0 이어야 한다 — 시뮬레이션 0)'
SELECT count(DISTINCT (d.source, d.category)) AS categories_no_mention_carries
FROM product_denominator d
WHERE d.category IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM need_mention m WHERE m.site = d.source AND m.category = d.category);

COMMIT;
