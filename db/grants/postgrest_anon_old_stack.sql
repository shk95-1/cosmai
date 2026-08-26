-- 초안이다. 실행하지 않았고, db/migrate.sh 도 이 파일을 집지 않는다 -- migrate.sh:100,105 가
-- postgrest_anon_needs.sql 과 needs_runtime_reader.sql 두 이름만 적기 때문이다. #168 이
-- 사용자 결정으로 닫힐 때 그 결정이 이 파일을 실행 경로에 넣거나 이 파일을 지운다.
--
-- 무엇을 하나: postgrest_anon 이 구 스택 두 스키마(trend_radar · tubedepth)에서 읽는 것을
-- 없앤다. 조사 결과 PostgREST 를 거쳐 그 두 스키마를 부르는 화면이 하나도 없다 --
-- trend-radar-dashboard 는 PostgREST 가 아니라 trend_radar_reader 로 DB 에 직접 붙고
-- (service/stack/docker-compose.yml:172), tubedepth-api 는 tubedepth_runtime 으로 붙는다.
-- 남는 소비자는 data-portal 하나이고 그것은 고정된 표 목록이 없다: anon 에게 보이는 것을
-- OpenAPI 로 훑어 전부 내려받게 하는 범용 브라우저다(service/data-portal/public/app.js:99).
-- 그래서 "화면이 실제로 부르는 표"의 화이트리스트는 이 두 스키마에서 비어 있다.
--
-- 되돌리기 (한 줄씩, 슈퍼유저 platform 으로):
--   GRANT trend_radar_reader TO postgrest_anon;
--   GRANT SELECT ON ALL TABLES IN SCHEMA tubedepth TO postgrest_anon;
--   REVOKE ALL ON TABLE tubedepth.api_keys FROM postgrest_anon;
--   ALTER DEFAULT PRIVILEGES FOR ROLE trend_radar_owner IN SCHEMA trend_radar
--     GRANT SELECT ON TABLES TO trend_radar_reader;
--   ALTER DEFAULT PRIVILEGES FOR ROLE tubedepth_owner IN SCHEMA tubedepth
--     GRANT SELECT ON TABLES TO postgrest_anon;
--   NOTIFY pgrst, 'reload schema';
-- 그 다섯 줄이 오늘 상태를 그대로 복원한다(2026-08-27 실측). 원본은 구 스택 init 스크립트
-- service/stack/init/20-postgrest-roles.sh 와 40-postgrest-tubedepth-grants.sh 이며,
-- 그 둘은 빈 db-store 첫 기동에만 돌기 때문에 여기서 끊어도 재실행이 되돌려 놓지 않는다.
--
-- 슈퍼유저(platform)가 실행한다: REVOKE 의 대상 권한을 준 것은 trend_radar_owner 와
-- tubedepth_owner 이고 DEFAULT PRIVILEGES 도 그 둘 소유다.

-- 1. trend_radar: 롤 멤버십 하나가 13개 관계를 전부 열고 있다. 직접 GRANT 는 없다.
REVOKE trend_radar_reader FROM postgrest_anon;

-- 미래 테이블까지 따라 열리는 것을 끊는다. 이것이 남아 있으면 다음 마이그레이션이 만드는
-- 표가 다시 조용히 anon 에 열린다 -- trend_radar_reader 를 다시 붙이는 날 그대로 부활한다.
ALTER DEFAULT PRIVILEGES FOR ROLE trend_radar_owner IN SCHEMA trend_radar
    REVOKE SELECT ON TABLES FROM trend_radar_reader;

-- 2. tubedepth: reader 롤이 없어 anon 에 직접 GRANT 되어 있다(구조가 trend_radar 와 다르다).
--    api_keys 만 빼고 12개가 열려 있고, 그중 285,749행 comments · 5,303행 transcripts 가
--    수집 원문이다(2026-08-27 실측).
REVOKE SELECT ON ALL TABLES IN SCHEMA tubedepth FROM postgrest_anon;
ALTER DEFAULT PRIVILEGES FOR ROLE tubedepth_owner IN SCHEMA tubedepth
    REVOKE SELECT ON TABLES FROM postgrest_anon;

-- USAGE 는 남긴다: 표가 하나도 없으면 PostgREST 가 그 스키마를 빈 OpenAPI 로 내고,
-- 아래 화이트리스트를 여는 날 스키마 권한을 다시 챙길 필요가 없다.
-- REVOKE USAGE ON SCHEMA trend_radar, tubedepth FROM postgrest_anon;  -- 완전 차단을 원하면

-- 3. data-portal 의 구 스택 탐색을 살리기로 하면 여기를 푼다. 이 목록은 "화면이 부른다"가
--    아니라 사용자 정책이다 -- 구조화된 사실만 두고 자유 텍스트 원문 셋
--    (trend_radar.review.body · tubedepth.comments.text · tubedepth.transcripts.full_text)과
--    수집기 내부 상태 표는 뺀 선이다. 푸는 쪽을 고르면 근거를 이 주석에 남긴다.
--
-- GRANT SELECT ON trend_radar.product, trend_radar.rank_snapshot, trend_radar.price_point,
--     trend_radar.new_product, trend_radar.new_products_view, trend_radar.review_stats,
--     trend_radar.review_topic, trend_radar.review_answer, trend_radar.review_summary
--     TO postgrest_anon;
-- GRANT SELECT ON tubedepth.video_snapshots, tubedepth.channel_snapshots,
--     tubedepth.listing_entries TO postgrest_anon;
--
-- 뺀 것과 이유:
--   trend_radar.review          -- 리뷰 전문 30,044행. #144·#168 이 겨눈 바로 그 노출.
--   trend_radar.run/run_source/fetch_log -- 수집 운영 기록, 데이터가 아니다.
--   tubedepth.comments/transcripts -- 댓글 원문 285,749행 · 자막 전문 5,303행.
--   tubedepth.jobs/artifacts/worker_control/lane_health/source_health/flatten_progress
--                               -- 수집기 내부 상태. jobs 337,201행은 API 로 낼 것이 아니다.
--   tubedepth.alembic_version   -- 마이그레이션 원장. trend_radar.alembic_version 은
--                                  default privileges 보다 먼저 생겨 애초에 닫혀 있다.

NOTIFY pgrst, 'reload schema';
