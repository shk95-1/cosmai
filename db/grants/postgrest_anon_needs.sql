-- Stage 1 has no needs_reader role; postgrest_anon gets a direct SELECT whitelist instead
-- (same pattern as service/stack/init/40-postgrest-tubedepth-grants.sh for tubedepth).
-- Whitelist, not default privileges: mention/labeled_set tables must stay invisible to anon.
-- 이 문장은 `needs` 절에만 참이다. 같은 anon 롤이 `trend_radar` 13개(trend_radar_reader 멤버십)와
-- `tubedepth` 12개(직접 GRANT + DEFAULT PRIVILEGES)도 읽고, 그 둘은 구 스택 init 이 열어 이
-- 파일이 닿지 않는다 -- 실측·경로·결정 대기 상태는 contracts/anon_exposure.md (#168).

SELECT format('CREATE ROLE postgrest_anon NOLOGIN')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'postgrest_anon') \gexec

GRANT USAGE ON SCHEMA needs TO postgrest_anon;
GRANT SELECT ON needs.metrics_need, needs.metrics_wish, needs.entity_lexicon, needs.aspect_lexicon,
    needs.product_ref, needs.analysis_run TO postgrest_anon;

-- 구조 지도(#142)가 읽는 둘. 표이므로 여기가 맞는 자리다 -- 뷰와 달리 배포가 다시 만들지 않아
-- 이 GRANT 가 살아남는다(뷰는 #158 이 그 반대를 배운 자리다: 뷰의 권한은 뷰 파일이 진다).
-- 단계 이름과 그들 사이의 관계이지 수집 원문이 아니므로 화이트리스트에 든다.
GRANT SELECT ON needs.pipeline_stage, needs.pipeline_edge TO postgrest_anon;

-- 뷰는 여기 적지 않는다. 이 파일은 migrate 단계 (d) 이고 db/views/*.sql 을 DROP + CREATE 하는
-- 것은 (f) 다 -- 여기서 준 GRANT 는 새로 만들어진 뷰에 따라오지 않아 어느 배포에서도 살아남지
-- 못한다(#158: 운영 관제 화면이 그래서 401 이었다). 뷰의 권한은 뷰 파일이 진다, needs_runtime
-- GRANT 가 거기 있는 것과 같은 이유로.

NOTIFY pgrst, 'reload schema';
