# anon 노출 — PostgREST `postgrest_anon` 이 읽는 관계

**측정 2026-08-27**(`has_table_privilege` 전수 + 세 스키마의 라이브 OpenAPI). `#168` 이 이 상태를
좁힐지 그대로 둘지 정한다 — 이 파일은 **지금 열려 있는 것**을 적지, 열려 있어야 하는 것을 적지 않는다.

`shared-db-postgrest-1` 하나가 database `app` 의 세 스키마를 `0.0.0.0:3000` 에 낸다
(`PGRST_DB_SCHEMAS=trend_radar,tubedepth,needs`, `PGRST_DB_ANON_ROLE=postgrest_anon`,
`PGRST_OPENAPI_MODE=follow-privileges`, `Access-Control-Allow-Origin: *`). 익명·무인증 GET 이고
읽기 전용인 근거는 `postgrest_anon` 의 권한이지 PostgREST 설정이 아니다. 합계 **34개 관계**.

## 문이 셋이다

| 스키마 | 여는 경로 | 자리 |
|---|---|---|
| `needs` | `postgrest_anon` 에 직접 GRANT 한 화이트리스트 | `db/grants/postgrest_anon_needs.sql` + `db/views/pipeline_health.sql` (이 레포) |
| `trend_radar` | **롤 멤버십** `GRANT trend_radar_reader TO postgrest_anon` | `service/stack/init/20-postgrest-roles.sh` (구 스택, archive) |
| `tubedepth` | **직접 GRANT + DEFAULT PRIVILEGES** (`api_keys` 만 REVOKE) | `service/stack/init/40-postgrest-tubedepth-grants.sh` (구 스택, archive) |

뒤의 둘은 이 레포 밖이고 `db/migrate.sh` 가 건드리지 않는다. `postgrest_anon_needs.sql` 의
"Whitelist, not default privileges" 는 그래서 `needs` 절에만 참이다 — `trend_radar` 와
`tubedepth` 양쪽에 `ALTER DEFAULT PRIVILEGES` 가 살아 있어 **새 테이블은 자동으로 anon 에 열린다.**

## needs

9개. 이 레포가 GRANT 하고 `tests/test_anon_exposure_contract.py` 가 이 목록과 대조한다.

`needs.metrics_need` · `needs.metrics_wish` · `needs.product_ref` · `needs.analysis_run` ·
`needs.entity_lexicon` · `needs.aspect_lexicon` · `needs.pipeline_stage` · `needs.pipeline_edge` ·
`needs.pipeline_health`(뷰)

닫혀 있는 것 중 이름이 걸린 둘: `need_mention` · `labeled_set` — 수집 원문 문장을 들고 있어
화이트리스트 밖이다. `corpus_*` · `*_mention` · `retrieval_chunk` · `topic_quarter_*` 도 같다.

포털이 실제로 부르는 것은 7개다(`portal/public/app.js:282-287`, `map-app.js:127-129`,
`ops-app.js:92`). `entity_lexicon` · `aspect_lexicon` 은 열려 있으나 부르는 화면이 없다.

## trend_radar

13개 전부(테이블 12 + 뷰 1). `trend_radar_reader` 가 갖는 것을 `postgrest_anon` 이 상속한다.

`fetch_log` · `new_product` · `new_products_view` · `price_point` · `product` · `rank_snapshot` ·
`review` · `review_answer` · `review_stats` · `review_summary` · `review_topic` · `run` · `run_source`

`review` 는 리뷰 **전문**(`body`) 30,044행이다. `alembic_version` 하나만 닫혀 있는데 그것은
정책이 아니라 순서다 — DEFAULT PRIVILEGES 보다 먼저 만들어졌다.

PostgREST 를 거쳐 이 스키마를 부르는 화면은 없다. `trend-radar-dashboard` 는
`TREND_RADAR_READONLY_DATABASE_URL` 로 `trend_radar_reader` 가 되어 DB 에 직접 붙는다
(`service/stack/docker-compose.yml:172`), 그리고 `127.0.0.1:8000` 에만 묶여 있다.

## tubedepth

12개. `api_keys` 만 REVOKE 되어 있다.

`alembic_version` · `artifacts` · `channel_snapshots` · `comments` · `flatten_progress` · `jobs` ·
`lane_health` · `listing_entries` · `source_health` · `transcripts` · `video_snapshots` ·
`worker_control`

원문: `comments` 285,749행 · `transcripts` 5,303행. **데이터가 아닌 표**:
`alembic_version`(마이그레이션 원장) · `worker_control` · `lane_health` · `source_health` ·
`flatten_progress` · `jobs`(337,201행) · `artifacts`.

2026-08-21 의 실측(`service/data-portal/docs/postgrest-observed.md`)에는 이 스키마가 6개였다 —
그 뒤 마이그레이션이 만든 6개가 DEFAULT PRIVILEGES 를 타고 조용히 붙었다. `tubedepth-api` 는
`tubedepth_runtime` 으로 붙으므로 anon 과 무관하고 `127.0.0.1:8080` 에만 있다.

## 유일한 소비자

`data-portal`(`0.0.0.0:3001`)은 고정된 표 목록이 없다. `Accept-Profile` 로 스키마를 고르고
OpenAPI 의 `definitions` 키를 그대로 표 목록으로 그린 뒤(`public/app.js:99,113`), 고른 표를
`PAGE_SIZE=1000` 으로 끝까지 페이지 넘겨 CSV/JSON 으로 받게 한다. 즉 **anon 에 열린 것이 곧 이
화면의 기능**이라, "이 화면이 부르는 표"의 화이트리스트는 정의되지 않는다.

`cosmai-portal-1`(`0.0.0.0:3003`)은 `needs` 만 부른다. 구 `cosmai-*` 넷과 `tubedepth-api` 는
PostgREST 를 쓰지 않는다.
