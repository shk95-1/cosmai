# anon 노출 — PostgREST `postgrest_anon` 이 읽는 관계

`shared-db-postgrest-1` 하나가 database `app` 의 세 스키마를 `0.0.0.0:3000` 에 낸다
(`PGRST_DB_SCHEMAS=trend_radar,tubedepth,needs`, `PGRST_DB_ANON_ROLE=postgrest_anon`,
`PGRST_OPENAPI_MODE=follow-privileges`, `Access-Control-Allow-Origin: *`). 익명·무인증 GET 이고
읽기 전용인 근거는 `postgrest_anon` 의 권한이지 PostgREST 설정이 아니다.

**현행은 23개**다 = `needs` 11 + `trend_radar` 9 + `tubedepth` 3. 아래 세 절이 그 전부를 이름으로
적는다. 대조는 `db/grants/postgrest_anon_check.sql`(읽기 전용, 일곱 절)과
`tests/test_anon_exposure_contract.py`.

`#168` 안 B(사용자 결정 2026-08-27)를 **2026-08-27 코디네이터 세션이 적용했다** —
`db/grants/postgrest_anon_old_stack.sql`, 한 명령씩. 적용 전은 36개였다(`trend_radar` 13 ·
`tubedepth` 12). 적용 후 실측: 남기기로 한 23개 전부 200, 막기로 한 것 전부 401
(`trend_radar` `review`·`run`·`fetch_log`·`run_source` · `tubedepth`
`comments`·`transcripts`·`jobs`·`worker_control`). 되돌리는 일곱 줄은 그 파일 꼬리에 있다 —
`relacl`·`nspacl` 을 적용 전 모양 그대로 복원한다.

## 좁히기 전 문이 셋이었다

| 스키마 | 좁히기 전에 열던 경로 | 자리 |
|---|---|---|
| `needs` | `postgrest_anon` 에 직접 GRANT 한 화이트리스트 | `db/grants/postgrest_anon_needs.sql` + `db/views/pipeline_health.sql` (이 레포) |
| `trend_radar` | **롤 멤버십** `GRANT trend_radar_reader TO postgrest_anon` | `service/stack/init/20-postgrest-roles.sh:34` (구 스택, archive) |
| `tubedepth` | **직접 GRANT `ON ALL TABLES` + DEFAULT PRIVILEGES** (`api_keys` 만 REVOKE) | `service/stack/init/40-postgrest-tubedepth-grants.sh:12-16` (구 스택, archive) |

뒤의 둘은 이 레포 밖이고 `db/migrate.sh` 가 건드리지 않는다. `postgrest_anon_needs.sql` 의
"Whitelist, not default privileges" 는 그래서 `needs` 절에만 참이었다 — `trend_radar` 와
`tubedepth` 양쪽에 `ALTER DEFAULT PRIVILEGES` 가 살아 있어 새 테이블이 자동으로 anon 에 열렸다.

**지금은 문이 하나다.** `postgrest_anon` 은 `trend_radar_reader` 를 포함해 **어떤 롤에도 속하지
않고**, 세 스키마 전부에서 표를 이름으로만 받는다. 표를 늘리려면 `needs` 는
`db/grants/postgrest_anon_needs.sql`, 나머지 둘은 `postgrest_anon_old_stack.sql` 에 한 줄을
더해야 한다 — 어느 마이그레이션도 조용히 열지 못한다.

### 보이려면 둘이 다 있어야 한다: SELECT 와 USAGE

"anon 이 무엇을 보는가"는 표의 `SELECT` 만으로 성립하지 않는다. 스키마 `USAGE` 가 없으면
PostgREST 는 **401** 을 내고, 그 스키마는 표를 몇 개 GRANT 했든 0개와 같다. `has_table_privilege`
는 스키마 권한과 무관하게 `t` 를 내므로 **표를 세는 것만으로는 이 구멍이 안 보인다.**

anon 이 USAGE 를 얻는 경로도 스키마마다 달랐다(`pg_namespace.nspacl` 실측 2026-08-27):

| 스키마 | USAGE 경로 | 멤버십을 끊으면 |
|---|---|---|
| `needs` | `postgrest_anon=U/needs_owner` — 직접 | 그대로 |
| `tubedepth` | `postgrest_anon=U/tubedepth_owner` — 직접 | 그대로 |
| `trend_radar` | `trend_radar_reader=U/trend_radar_owner` — **멤버십으로 상속** | **함께 사라진다** |

그래서 좁히기는 `trend_radar` 에만 `GRANT USAGE ON SCHEMA … TO postgrest_anon` 을 다시 준다.
2026-08-27 적용 직후 이 한 줄이 없어 `trend_radar` 9개가 전부 401 이었다 — 표 개수(절 2)는
목표대로 `9` 를 찍고 있었는데도. `db/grants/postgrest_anon_check.sql` 절 6 이 그 뒤로 USAGE 를
따로 재고, 세 스키마 모두 `usable = t` 여야 한다.

`trend_radar` 만 무언가를 되돌려 줘야 하는 이유는 아래 DEFAULT PRIVILEGES 절과 **같은 비대칭**
이다: 이 스키마의 권한은 `trend_radar_reader` 라는 롤에 걸려 있고 anon 은 그 롤의 손님이었다.
`tubedepth` 는 처음부터 anon 앞으로 걸려 있었다.

### DEFAULT PRIVILEGES 는 두 스키마를 다르게 다룬다 (사용자 결정 2)

같은 표류인데 **수혜자가 다르기 때문에** 처방이 반대다. `pg_default_acl` 실측:

| 스키마 | 기본권한의 수혜자 | 좁히기가 하는 일 |
|---|---|---|
| `trend_radar` | `trend_radar_reader=r/trend_radar_owner` — anon 이 아니다 | **그대로 둔다.** 멤버십을 끊으면 anon 은 그 롤의 기본권한을 물려받지 않으므로 표류가 이미 멈춘다 |
| `tubedepth` | `postgrest_anon=r/tubedepth_owner` — anon 에게 직접 | **지운다.** 남기면 다음 마이그레이션이 만드는 표가 그대로 anon 에 붙는다 |

`trend_radar` 쪽을 지우면 안 되는 이유는 `trend_radar_reader` 가 anon 의 통로이기 **이전에**
`trend-radar-dashboard` 가 직접 로그인하는 롤이라서다(`service/stack/docker-compose.yml:172`,
`rolcanlogin=t`). 기본권한을 없애면 앞으로 이 스키마에 생기는 표를 그 화면이 못 읽는다 —
"지금 열려 있는 것을 바꾸지 않으면서 표류만 멈춘다"는 결정의 근거를 넘는다.

그래서 적용 후 `pg_default_acl` 은 **0행이 아니라 `trend_radar` 한 행**이 남는다
(`db/grants/postgrest_anon_check.sql` 절 4 가 그 기대를 적는다).

## needs

11개. 좁히기는 이 스키마를 건드리지 않는다 — 적용 전후가 같다. 이 레포가 GRANT 하고 테스트가
실제 DB 의 `has_table_privilege` 와 대조한다.

`needs.metrics_need` · `needs.metrics_wish` · `needs.product_ref` · `needs.analysis_run` ·
`needs.entity_lexicon` · `needs.aspect_lexicon` · `needs.pipeline_stage` · `needs.pipeline_edge` ·
`needs.pipeline_health`(뷰) · `needs.mention_lineage`(뷰) · `needs.collection_lineage`(뷰)

뒤의 둘은 `#144` 계보 드릴다운이 연다(`db/views/mention_lineage.sql:149` ·
`collection_lineage.sql:191`). 포털이 지표 한 칸에서 언급으로, 언급에서 수집분으로 내려갈 때
부른다(`portal/public/app.js:351,441`).

**`mention_lineage` 는 원문을 내지 않는다 — 120자 발췌만 낸다**(`sentence_excerpt` ·
`doc_excerpt`, 뷰 파일 125·132행; 전문 길이를 나란히 두어 잘렸다는 사실이 숨지 않는다).
원문 컬럼은 이름조차 나가지 않는다. 사용자 결정 2026-08-27. 그 자르기의 이유는 **이 뷰가
원문 전달 경로가 되지 않게** 하는 것이지 anon 이 리뷰 본문을 못 본다는 것이 아니다 — 그 선은
`trend_radar.review` 쪽에 이미 없고, 그것을 다루는 것이 이 문서와 `#168` 이다.

닫혀 있는 것 중 이름이 걸린 둘: `need_mention` · `labeled_set` — 수집 원문 문장을 들고 있어
화이트리스트 밖이다. `corpus_*` · `*_mention` · `retrieval_chunk` · `topic_quarter_*` 도 같다.

`entity_lexicon` · `aspect_lexicon` 은 **열려 있으나 부르는 화면이 없다**(`#168` 조사
2026-08-27: `portal/` 전체에 참조 0건). 규칙 사전이라 민감도는 낮고 이번 결정 범위 밖이라
그대로 둔다 — 좁힐지는 별건이다.

## trend_radar

9개. 집계된 사실만 남는다. 이 스키마만 `GRANT USAGE ON SCHEMA` 를 함께 받는다 — 멤버십을 끊으면
USAGE 도 같이 사라지기 때문이다(위 절).

`trend_radar.product` · `trend_radar.rank_snapshot` · `trend_radar.price_point` ·
`trend_radar.new_product` · `trend_radar.new_products_view` · `trend_radar.review_stats` ·
`trend_radar.review_topic` · `trend_radar.review_answer` · `trend_radar.review_summary`

좁히기가 닫은 넷: `review`(리뷰 **전문** body, 30,044행 — `#144`·`#168` 이 겨눈 노출 그 자체) ·
`run` · `run_source` · `fetch_log`(수집 운영 기록이지 데이터가 아니다). `alembic_version` 은
전부터 닫혀 있었는데 정책이 아니라 순서였다 — DEFAULT PRIVILEGES 보다 먼저 만들어졌다.

PostgREST 를 거쳐 이 스키마를 부르는 화면은 처음부터 없었다. `trend-radar-dashboard` 는
`TREND_RADAR_READONLY_DATABASE_URL` 로 `trend_radar_reader` 가 되어 DB 에 **직접** 붙고
(`service/stack/docker-compose.yml:172`) `127.0.0.1:8000` 에만 묶여 있다 — 그래서 멤버십을 끊어도
그 화면은 영향을 받지 않았다. 그 롤의 DEFAULT PRIVILEGES 를 살려 둔 이유이기도 하다(아래 절).

## tubedepth

3개. 영상·채널·목록 메타만 남는다.

`tubedepth.video_snapshots` · `tubedepth.channel_snapshots` · `tubedepth.listing_entries`

좁히기가 닫은 아홉: `comments`(댓글 원문 285,749행) · `transcripts`(자막 전문 5,303행) ·
`jobs`(337,201행) · `artifacts` · `worker_control` · `lane_health` · `source_health` ·
`flatten_progress`(수집기 내부 상태) · `alembic_version`(마이그레이션 원장).

`api_keys` 는
전부터 닫혀 있었고 좁히기도 열지 않는다.

이 스키마가 표류의 증거다: 2026-08-21 실측(`service/data-portal/docs/postgrest-observed.md:60`)
에는 6개였는데 그 뒤 마이그레이션이 만든 6개가 DEFAULT PRIVILEGES 를 타고 조용히 붙어 12개가
됐다 — 아무도 결정하지 않은 채로. `comments`·`transcripts` 가 그렇게 열렸다. 좁히기가 그
기본권한을 지웠으므로 같은 일이 다시 생기지 않는다. `tubedepth-api` 는 `tubedepth_runtime` 으로
붙으므로 anon 과 무관하고 `127.0.0.1:8080` 에만 있다.

## 유일한 소비자

`data-portal`(`0.0.0.0:3001`)은 고정된 표 목록이 없다. `Accept-Profile` 로 스키마를 고르고
OpenAPI 의 `definitions` 키를 그대로 표 목록으로 그린 뒤(`public/app.js:99,113`), 고른 표를
`PAGE_SIZE=1000` 으로 끝까지 페이지 넘겨 CSV/JSON 으로 받게 한다. 즉 **anon 에 열린 것이 곧 이
화면의 기능**이라, "이 화면이 부르는 표"의 화이트리스트는 정의되지 않는다. 좁히기가 이 화면에
일으킨 것은 고장이 아니라 **기능 축소**다 — `trend_radar` 목록이 13개에서 9개로, `tubedepth` 가
12개에서 3개로 줄었고, 남은 것은 그대로 받아 간다.

`cosmai-portal-1`(`0.0.0.0:3003`)은 `needs` 만 부른다. 구 `cosmai-*` 넷과 `tubedepth-api` 는
PostgREST 를 쓰지 않는다. `0.0.0.0` 바인드 자체는 이 파일이 다루지 않는다(사용자 결정 3, 별건).
