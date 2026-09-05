-- #123 re-aggregation check: after the backfill + `cosmai analyze all`, does a category scope receive
-- population metrics?
--
-- Read-only. Run the backfill (db/backfill/123_category_canonical.sql) first, then run this **after**
-- aggregate has fired once, whether through the regular cron or by hand -- metrics_need is deleted and
-- rewritten per run, so the backfill alone leaves an old run's NULLs standing as they were.
--
--   docker exec -i shared-postgres \
--     psql -U needs_runtime -d app -X -v ON_ERROR_STOP=1 < db/backfill/123_category_canonical_verify.sql

--
-- 2026-08-27 실측(이관 전, run 26): (1) 카테고리 scope 37개 중 34개가 population_share_pct 결측,
-- (4) 계층형 scope 24개가 분모를 하나도 못 받는다. 이관 + 재집계 뒤 기대: (3)·(4) 는 0 행,
-- (2) 는 아래 넷만 남는다 — '01 > 클렌징 > 클렌징워터' · '02 > 립 메이크업 > 립틴트' ·
-- '05 > 헤어 컬러링/펌 > 염모제' · '01 > 선케어 > 애프터선'. 이 넷은 표기 문제가 아니다:
-- 그 카테고리의 제품이 전부 low_complete=false 라(≤2★ 표본이 150 상한에 닿았다) 계약이
-- 분모를 일부러 결측으로 둔다 (contracts/interfaces.md §low_complete). 표본을 더 걷어야 풀린다.
--
\set ON_ERROR_STOP on
BEGIN READ ONLY;

\echo '--- Run being measured: the latest of analyze:all'
SELECT run_id, status, started_at, note FROM analysis_run
WHERE note LIKE 'analyze:all%' ORDER BY run_id DESC LIMIT 1;

\echo '--- (1) Population metrics per category scope (the totals row). missing_pop = 0 is #123 completion bar'
WITH r AS (SELECT max(run_id) AS run_id FROM analysis_run WHERE note LIKE 'analyze:all%')
SELECT m.scope,
       count(*) AS need_keys,
       count(m.denom_low) AS denom_low,
       count(m.denom_site) AS denom_site,
       count(m.low_share) AS low_share,
       count(m.population_share_pct) AS population_share_pct,
       count(*) - count(m.population_share_pct) AS missing_pop
FROM metrics_need m, r
WHERE m.run_id = r.run_id AND m.month = '' AND m.product_ref = '' AND m.scope <> 'all'
GROUP BY 1 ORDER BY missing_pop DESC, need_keys DESC;

\echo '--- (2) Why a scope got no metric: no denominator, or a denominator with no low_complete'
WITH r AS (SELECT max(run_id) AS run_id FROM analysis_run WHERE note LIKE 'analyze:all%'),
scopes AS (SELECT DISTINCT scope FROM metrics_need m, r
           WHERE m.run_id = r.run_id AND m.scope <> 'all')
SELECT s.scope,
       count(d.source) AS denominator_rows,
       count(*) FILTER (WHERE d.low_complete) AS low_complete_rows,
       sum(d.site_review_count) FILTER (WHERE d.low_complete) AS denom_site
FROM scopes s LEFT JOIN product_denominator d ON d.category = s.scope
GROUP BY 1 HAVING count(d.source) = 0 OR count(*) FILTER (WHERE d.low_complete) = 0
ORDER BY 1;

\echo '--- (3) Did one site write the same category under two notations (must be 0 rows)'
-- 사이트가 다른 같은 leaf 는 갈라짐이 아니다: glowpick 의 '샴푸' 와 oliveyoung 의
-- '05 > 헤어 세정류 > 샴푸' 는 서로 다른 사이트 분류이고, 그 둘을 하나로 접는 것은 category 가 아니라
-- lexicon_category 의 일이다 (contracts/formats.md §카테고리 매핑 CSV · #127). 그래서 site 로 묶는다.
WITH r AS (SELECT max(run_id) AS run_id FROM analysis_run WHERE note LIKE 'analyze:all%'),
population AS (SELECT DISTINCT m.site, m.category FROM need_mention m, analysis_run a, r
               WHERE a.run_id = r.run_id AND m.category IS NOT NULL
                 AND m.extractor_version = ANY (string_to_array(a.versions ->> 'extractor', ';')))
SELECT a.site, a.category AS leaf_shaped, b.category AS path_shaped
FROM population a JOIN population b ON a.site = b.site
 AND right(b.category, length(a.category) + 3) = ' > ' || a.category
ORDER BY 1, 2;

\echo '--- (4) Is a scope left with not a single denominator (must be 0 rows)'
WITH r AS (SELECT max(run_id) AS run_id FROM analysis_run WHERE note LIKE 'analyze:all%')
SELECT DISTINCT m.scope
FROM metrics_need m, r
WHERE m.run_id = r.run_id AND m.scope <> 'all' AND m.month = '' AND m.product_ref = ''
  AND m.denom_low IS NULL AND m.denom_site IS NULL
ORDER BY 1;

ROLLBACK;
