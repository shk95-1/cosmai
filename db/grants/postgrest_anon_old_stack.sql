-- 사용자 결정 2026-08-27 (#168 안 B): anon 에서 **수집 원문과 수집기 내부 상태**를 뺀다.
-- 집계된 사실은 남긴다 -- 구 스택 설계가 원래 "로컬 네트워크에 익명 읽기"였고
-- data-portal 의 존재 이유가 그것이다(service/data-portal/README.md:3).
--
-- **아직 실행하지 않았다.** db/migrate.sh 는 이름으로 두 파일만 집으므로(migrate.sh:100,105)
-- 이 파일은 실행 경로 밖이고, 여기 두는 것만으로는 아무 일도 일어나지 않는다. 적용은
-- 코디네이터 세션이 한 명령씩 한다 -- 구 스택 권한이라 STATE.md §3 의 매번 승인이다.
--
-- 적용 전후 대조: db/grants/postgrest_anon_check.sql (읽기 전용). 적용 후 anon 이 보는 것은
-- needs 11 + trend_radar 9 + tubedepth 3 = 23 개여야 한다 (needs 는 이 파일이 건드리지 않는다).
--
-- 슈퍼유저(platform)가 실행한다: REVOKE 대상 권한을 준 것이 trend_radar_owner 와
-- tubedepth_owner 이고, 지우는 DEFAULT PRIVILEGES 는 tubedepth_owner 소유다.
-- 두 스키마를 다르게 다룬다 -- trend_radar 는 멤버십만 끊고 기본권한은 남긴다(1절 주석).

-- ---------------------------------------------------------------------------
-- 1. trend_radar -- 롤 멤버십 하나가 13개를 전부 열고 있었다. 직접 GRANT 는 없었다.
-- ---------------------------------------------------------------------------
REVOKE trend_radar_reader FROM postgrest_anon;

-- 이 스키마의 DEFAULT PRIVILEGES 는 **일부러 건드리지 않는다.** 빠진 것이 아니다.
--   pg_default_acl 의 trend_radar 행은 trend_radar_owner -> `trend_radar_reader=r` 이지
--   postgrest_anon 이 아니다. 위 한 줄로 anon 이 그 롤의 멤버가 아니게 되면 기본권한도
--   물려받지 않으므로, anon 쪽 표류는 그 한 줄로 이미 멈춘다.
--   반대로 지우면 대시보드가 깨진다: trend_radar_reader 는 anon 의 통로이기 이전에
--   trend-radar-dashboard 가 **직접 로그인하는 롤**이고(service/stack/docker-compose.yml:172
--   의 TREND_RADAR_READONLY_DATABASE_URL, rolcanlogin=t), 기본권한을 없애면 앞으로
--   trend_radar 에 생기는 표를 그 화면이 못 읽는다. 사용자 결정 2 는 "지금 열려 있는 것을
--   바꾸지 않으면서 표류만 멈춘다"였지 미래의 대시보드 접근을 좁히는 것이 아니었다.
--   tubedepth 쪽(아래 2절)은 기본권한이 postgrest_anon 에게 **직접** 걸려 있어 사정이 다르다.

-- 스키마 USAGE 를 **여기서 다시 준다.** anon 은 이것도 멤버십으로 물려받고 있었다:
-- trend_radar 의 nspacl 은 `trend_radar_reader=U/trend_radar_owner` 이고 postgrest_anon 항목이
-- 없다. 그래서 위 REVOKE 한 줄이 SELECT 와 함께 USAGE 도 가져간다 -- 표를 이름으로 되돌려 줘도
-- PostgREST 는 401 을 낸다(운영 실측 2026-08-27, 적용 직후 trend_radar 9개 전부 401).
-- tubedepth·needs 는 필요 없다: 둘 다 nspacl 에 `postgrest_anon=U` 가 직접 있어 아래 2절의
-- REVOKE 가 USAGE 를 건드리지 않는다. DEFAULT PRIVILEGES 와 **같은 비대칭**이다 -- 이 파일이
-- trend_radar 에서만 무언가를 되돌려 주는 이유가 매번 그것 하나다.
-- 이미 있어도 무해하다(GRANT 는 멱등).
GRANT USAGE ON SCHEMA trend_radar TO postgrest_anon;

-- 멤버십 대신 표를 이름으로 준다. 앞으로 이 스키마에서 anon 이 보는 것은 이 아홉 줄뿐이고,
-- 새 표를 열려면 여기 한 줄을 더해야 한다.
GRANT SELECT ON
    trend_radar.product,             -- 제품 축(source, product_key, name, brand, volume)
    trend_radar.rank_snapshot,       -- 시간별 랭킹
    trend_radar.price_point,         -- 가격·할인율
    trend_radar.new_product,         -- 신제품 등재
    trend_radar.new_products_view,   -- 위의 뷰. data-portal 이 표와 구분 없이 그린다
    trend_radar.review_stats,        -- 리뷰 개수·별점 분포 (집계, 원문 아님)
    trend_radar.review_topic,        -- 사이트가 낸 토픽·비율 (집계)
    trend_radar.review_answer,       -- 다이소 설문 답 (선택지, 자유 서술 아님)
    trend_radar.review_summary       -- 사이트가 낸 요약 (원문 아님)
    TO postgrest_anon;

-- 뺀 것: review 는 리뷰 **전문**(body) 30,044행이고 #144·#168 이 겨눈 노출 그 자체다.
-- run · run_source · fetch_log 는 수집 운영 기록이지 데이터가 아니다.
-- alembic_version 은 애초에 닫혀 있었다(DEFAULT PRIVILEGES 보다 먼저 생겨서다, 정책이 아니라 순서).

-- ---------------------------------------------------------------------------
-- 2. tubedepth -- reader 롤이 없어 anon 에 직접 GRANT 되어 있다(구조가 trend_radar 와 다르다).
--    api_keys 만 빼고 12개가 열려 있었다.
-- ---------------------------------------------------------------------------
REVOKE SELECT ON ALL TABLES IN SCHEMA tubedepth FROM postgrest_anon;

-- 여기서는 DEFAULT PRIVILEGES 를 **반드시** 지운다 -- 1절과 정반대인 이유가 이것 하나다:
-- pg_default_acl 의 tubedepth 행이 postgrest_anon=r/tubedepth_owner 로 anon 에게 직접 걸려
-- 있어서, 남겨 두면 다음 마이그레이션이 만드는 표가 그대로 anon 에 붙는다. 이 스키마가
-- 2026-08-21 6개에서 지금 12개가 된 경로가 정확히 이것이다
-- (service/data-portal/docs/postgrest-observed.md:60). 지워도 잃는 롤은 없다: 이 기본권한의
-- 수혜자는 anon 뿐이고 tubedepth_runtime 은 자기 몫을 따로 갖는다.
ALTER DEFAULT PRIVILEGES FOR ROLE tubedepth_owner IN SCHEMA tubedepth
    REVOKE SELECT ON TABLES FROM postgrest_anon;

GRANT SELECT ON
    tubedepth.video_snapshots,       -- 영상 메타(title, channel_id, view_count)
    tubedepth.channel_snapshots,     -- 채널 메타
    tubedepth.listing_entries        -- 목록(kind, target, video_id, title)
    TO postgrest_anon;

-- 뺀 것: comments 285,749행 · transcripts 5,303행은 수집 원문이다.
-- jobs(337,201행) · artifacts · worker_control · lane_health · source_health ·
-- flatten_progress 는 수집기 내부 상태, alembic_version 은 마이그레이션 원장이다.
-- api_keys 는 전부터 REVOKE 되어 있었고 여기서도 열지 않는다.

-- USAGE 는 세 스키마 모두 anon 에게 남는다 -- 다만 얻는 경로가 다르다: needs·tubedepth 는
-- 원래부터 nspacl 에 직접 있고, trend_radar 만 1절이 다시 준다(멤버십과 함께 사라지므로).
-- "무엇이 보이는가"는 SELECT 와 USAGE 가 **둘 다** 있어야 성립한다: 표를 아무리 GRANT 해도
-- USAGE 가 없으면 그 스키마는 0개와 같다. postgrest_anon_check.sql 절 6 이 그것을 따로 잰다.
-- 0.0.0.0 바인드는 이 파일이 다루지 않는다 -- 사용자 결정 3(2026-08-27)으로 따로 다룬다.

NOTIFY pgrst, 'reload schema';

-- ---------------------------------------------------------------------------
-- 되돌리기 -- 2026-08-27 실측 상태를 그대로 복원한다. 슈퍼유저로 위에서 아래로.
-- 일곱 줄이다. 규칙은 relacl·nspacl 을 오늘과 **같은 모양**으로 되돌리는 것이지 유효 권한만
-- 맞추는 것이 아니다 -- 그래서 안 B 가 새로 만든 직접 부여(표 아홉 + 스키마 USAGE 하나)는
-- 멤버십을 되붙이기 전에 걷는다. 멤버십이 그 둘을 다시 물려주기 때문이다.
-- trend_radar 의 DEFAULT PRIVILEGES 는 애초에 건드리지 않으므로 되돌릴 줄도 없다(1절 주석).
-- tubedepth 쪽은 ON ALL TABLES 가 12개를 다시 덮으므로 별도 REVOKE 가 필요 없고, USAGE 는
-- 이 파일이 준 적이 없어 되돌릴 것도 없다.
-- ---------------------------------------------------------------------------
--   REVOKE SELECT ON trend_radar.product, trend_radar.rank_snapshot, trend_radar.price_point,
--       trend_radar.new_product, trend_radar.new_products_view, trend_radar.review_stats,
--       trend_radar.review_topic, trend_radar.review_answer, trend_radar.review_summary
--       FROM postgrest_anon;
--   REVOKE USAGE ON SCHEMA trend_radar FROM postgrest_anon;
--   GRANT trend_radar_reader TO postgrest_anon;
--   GRANT SELECT ON ALL TABLES IN SCHEMA tubedepth TO postgrest_anon;
--   REVOKE ALL ON TABLE tubedepth.api_keys FROM postgrest_anon;
--   ALTER DEFAULT PRIVILEGES FOR ROLE tubedepth_owner IN SCHEMA tubedepth
--       GRANT SELECT ON TABLES TO postgrest_anon;
--   NOTIFY pgrst, 'reload schema';
--
-- 원본은 구 스택 init(service/stack/init/20-postgrest-roles.sh:34 ·
-- 40-postgrest-tubedepth-grants.sh:12-16)이며, 그 둘은 빈 db-store 첫 기동에만 돌기 때문에
-- 여기서 끊어도 재실행이 되돌려 놓지 않는다.
