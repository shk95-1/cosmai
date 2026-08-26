# anon 노출 — PostgREST `postgrest_anon` 이 읽는 관계

`shared-db-postgrest-1` 하나가 database `app` 의 세 스키마를 `0.0.0.0:3000` 에 낸다
(`PGRST_DB_SCHEMAS=trend_radar,tubedepth,needs`, `PGRST_DB_ANON_ROLE=postgrest_anon`,
`PGRST_OPENAPI_MODE=follow-privileges`, `Access-Control-Allow-Origin: *`). 익명·무인증 GET 이고
읽기 전용인 근거는 `postgrest_anon` 의 권한이지 PostgREST 설정이 아니다.

**이 파일은 두 상태를 나눠 적는다.** 좁히기(`#168` 안 B, 사용자 결정 2026-08-27)는 **아직 운영에
적용되지 않았다** — `db/grants/postgrest_anon_old_stack.sql` 은 `db/migrate.sh` 가 집지 않는
파일이고 코디네이터 세션이 한 명령씩 적용한다. 적용 전까지 **현행은 34개**다. 적용·확인이
끝나면 "적용 전" 절을 지우고 "적용 후"를 현행으로 올린다. 대조는
`db/grants/postgrest_anon_check.sql`(읽기 전용)과 `tests/test_anon_exposure_contract.py`.

| | 관계 수 | 근거 |
|---|---|---|
| 적용 전 (현행, 2026-08-27 실측) | 34 = needs 9 + trend_radar 13 + tubedepth 12 | `has_table_privilege` 전수 + 세 스키마 라이브 OpenAPI |
| 적용 후 (목표) | 21 = needs 9 + trend_radar 9 + tubedepth 3 | `db/grants/postgrest_anon_old_stack.sql` |

## 문이 셋이다

| 스키마 | 여는 경로 (적용 전) | 자리 |
|---|---|---|
| `needs` | `postgrest_anon` 에 직접 GRANT 한 화이트리스트 | `db/grants/postgrest_anon_needs.sql` + `db/views/pipeline_health.sql` (이 레포) |
| `trend_radar` | **롤 멤버십** `GRANT trend_radar_reader TO postgrest_anon` | `service/stack/init/20-postgrest-roles.sh:34` (구 스택, archive) |
| `tubedepth` | **직접 GRANT `ON ALL TABLES` + DEFAULT PRIVILEGES** (`api_keys` 만 REVOKE) | `service/stack/init/40-postgrest-tubedepth-grants.sh:12-16` (구 스택, archive) |

뒤의 둘은 이 레포 밖이고 `db/migrate.sh` 가 건드리지 않는다. `postgrest_anon_needs.sql` 의
"Whitelist, not default privileges" 는 그래서 `needs` 절에만 참이었다 — `trend_radar` 와
`tubedepth` 양쪽에 `ALTER DEFAULT PRIVILEGES` 가 살아 있어 **새 테이블이 자동으로 anon 에 열린다.**
좁히기가 그 둘도 함께 끊는다(사용자 결정 2). 적용 후 `postgrest_anon` 은 `trend_radar_reader` 를
포함해 **어떤 롤에도 속하지 않고**, 세 스키마 전부에서 표를 이름으로만 받는다.

## needs

9개. 변화 없다 — 좁히기가 이 스키마를 건드리지 않는다. 이 레포가 GRANT 하고 테스트가 실제 DB의
`has_table_privilege` 와 대조한다.

`needs.metrics_need` · `needs.metrics_wish` · `needs.product_ref` · `needs.analysis_run` ·
`needs.entity_lexicon` · `needs.aspect_lexicon` · `needs.pipeline_stage` · `needs.pipeline_edge` ·
`needs.pipeline_health`(뷰)

닫혀 있는 것 중 이름이 걸린 둘: `need_mention` · `labeled_set` — 수집 원문 문장을 들고 있어
화이트리스트 밖이다. `corpus_*` · `*_mention` · `retrieval_chunk` · `topic_quarter_*` 도 같다.

포털이 실제로 부르는 것은 7개다(`portal/public/app.js:282-287`, `map-app.js:127-129`,
`ops-app.js:92`). `entity_lexicon` · `aspect_lexicon` 은 열려 있으나 부르는 화면이 없다.

## trend_radar 적용 전

13개 전부(테이블 12 + 뷰 1). `trend_radar_reader` 가 갖는 것을 `postgrest_anon` 이 상속한다.

`fetch_log` · `new_product` · `new_products_view` · `price_point` · `product` · `rank_snapshot` ·
`review` · `review_answer` · `review_stats` · `review_summary` · `review_topic` · `run` · `run_source`

`review` 는 리뷰 **전문**(`body`) 30,044행이다. `alembic_version` 하나만 닫혀 있는데 그것은
정책이 아니라 순서다 — DEFAULT PRIVILEGES 보다 먼저 만들어졌다.

PostgREST 를 거쳐 이 스키마를 부르는 화면은 없다. `trend-radar-dashboard` 는
`TREND_RADAR_READONLY_DATABASE_URL` 로 `trend_radar_reader` 가 되어 DB 에 직접 붙고
(`service/stack/docker-compose.yml:172`) `127.0.0.1:8000` 에만 묶여 있다 — 그래서 멤버십을 끊어도
그 화면은 영향을 받지 않는다.

## trend_radar 적용 후

9개. 집계된 사실만 남는다.

`trend_radar.product` · `trend_radar.rank_snapshot` · `trend_radar.price_point` ·
`trend_radar.new_product` · `trend_radar.new_products_view` · `trend_radar.review_stats` ·
`trend_radar.review_topic` · `trend_radar.review_answer` · `trend_radar.review_summary`

빠지는 넷: `review`(리뷰 전문) · `run` · `run_source` · `fetch_log`(수집 운영 기록).

## tubedepth 적용 전

12개. `api_keys` 만 REVOKE 되어 있다.

`alembic_version` · `artifacts` · `channel_snapshots` · `comments` · `flatten_progress` · `jobs` ·
`lane_health` · `listing_entries` · `source_health` · `transcripts` · `video_snapshots` ·
`worker_control`

원문: `comments` 285,749행 · `transcripts` 5,303행. **데이터가 아닌 표**:
`alembic_version`(마이그레이션 원장) · `worker_control` · `lane_health` · `source_health` ·
`flatten_progress` · `jobs`(337,201행) · `artifacts`.

2026-08-21 의 실측(`service/data-portal/docs/postgrest-observed.md:60`)에는 이 스키마가 6개였다 —
그 뒤 마이그레이션이 만든 6개가 DEFAULT PRIVILEGES 를 타고 조용히 붙었다. `tubedepth-api` 는
`tubedepth_runtime` 으로 붙으므로 anon 과 무관하고 `127.0.0.1:8080` 에만 있다.

## tubedepth 적용 후

3개. 영상·채널·목록 메타만 남는다.

`tubedepth.video_snapshots` · `tubedepth.channel_snapshots` · `tubedepth.listing_entries`

빠지는 아홉: `comments` · `transcripts`(수집 원문) · `jobs` · `artifacts` · `worker_control` ·
`lane_health` · `source_health` · `flatten_progress`(수집기 내부 상태) · `alembic_version`(원장).
`api_keys` 는 전부터 닫혀 있었고 좁히기도 열지 않는다.

## 유일한 소비자

`data-portal`(`0.0.0.0:3001`)은 고정된 표 목록이 없다. `Accept-Profile` 로 스키마를 고르고
OpenAPI 의 `definitions` 키를 그대로 표 목록으로 그린 뒤(`public/app.js:99,113`), 고른 표를
`PAGE_SIZE=1000` 으로 끝까지 페이지 넘겨 CSV/JSON 으로 받게 한다. 즉 **anon 에 열린 것이 곧 이
화면의 기능**이라, "이 화면이 부르는 표"의 화이트리스트는 정의되지 않는다. 좁히기가 이 화면에
일으키는 것은 고장이 아니라 **기능 축소**다 — 목록에서 13개가 사라지고 9개가 남는다.

`cosmai-portal-1`(`0.0.0.0:3003`)은 `needs` 만 부른다. 구 `cosmai-*` 넷과 `tubedepth-api` 는
PostgREST 를 쓰지 않는다. `0.0.0.0` 바인드 자체는 이 파일이 다루지 않는다(사용자 결정 3, 별건).
