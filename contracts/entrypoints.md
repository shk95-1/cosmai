# 엔트리 규약

## 수집기
```
cosmai collect <collector> --dataset <dataset> [--board <board>] [--since <date>]
  collector ∈ {commerce, youtube, naver}
  commerce datasets: ranking | product | review | review_stats | new_product | review_low
  youtube  datasets: watch | work | flatten | prune  (기존 tubedepth 명령 의미 유지)
  naver    datasets: datalab | blog   (source 행 모델 없음 -- cosmai-old 계승 안 함, 원천은 needs.naver_* (004))
종료 코드: 0 ok · 1 partial(일부 실패·절단) · 2 blocked(차단/거부)   ← trend-radar 관찰 규약 그대로
cosmai login --source <source>
  <source> 가 registry 에 없거나 브라우저 트랜스포트(Transport.BROWSER)가 아니면 종료 코드 2 로 거절.
  headless=False 로 실제 창을 띄우고, **호스트에서 레포 루트 기준으로 실행**한다(컨테이너 안이 아님 --
  WSL2 는 WSLg 로 창을 그대로 띄우고, cwd 가 레포 루트가 아니면 종료 코드 2 로 거절한다). 그 cwd 가
  `stack/docker-compose.yml` 의 `COMMERCE_BROWSER_PROFILE_DIR` 기본값과 같은 디렉터리로 풀리는 이유다
  (#27). 호스트에 Chromium 이 없으면 최초 1회 `uv run playwright install chromium`.
```
- 수집기는 **자기 스키마의 테이블에만** 쓴다 (`ddl/current`). 다른 스키마 읽기는 reader 롤로만.
- HTTP 트랜스포트의 UA(`collectors/commerce/contract.py`의 `DEFAULT_UA`)는 **우리가 우리를 밝히는 이름**이다
  — 브라우저 흉내도 아니고, 통과를 사려고 고르는 값도 아니다. 값은 테스트가 리터럴로 못 박는다.
- **이미지 베이스는 TLS 지문을 바꾸므로 수집 성공/실패를 가른다.** 챌린지를 UA·레이트·IP 로 읽기 전에
  베이스부터 본다: 같은 코드·같은 UA 로 호스트는 통과하고 컨테이너는 막히는 일이 실제로 있었고
  (2026-08-25 oliveyoung), 가른 것은 베이스 이미지의 OpenSSL — 즉 ClientHello 지문(JA3/JA4) — 이었다.
  그래서 `stack/Dockerfile` 의 베이스는 패키징 취향이 아니라 **수집 입력**이고, 빌드가 그 하한을 이미지
  안에서 검사한다 (`tests/stack/test_image_tls_stack.py`). 브라우저 위장(지문 스푸핑)은 범위 밖이다.
- 표본 설계는 상수로 세고 `collectors/<c>/scope.json` 에 기록한다 (scope.lock 의 변형: 파일 하나, CHANGELOG 의무 없음, 테스트는 상수=파일 일치만 검사).
- **한 소스는 한 번에 한 런만 걷는다.** 레이트 정책은 프로세스 안에서만 강제되므로(수집기마다 자기 게이트)
  겹친 크론 두 줄은 같은 사이트를 정책의 두 배로 때린다. 다른 런이 그 소스를 이미 걷고 있으면 **그 소스만
  건너뛰고** 사유를 남긴 뒤 런은 partial(**1**)로 끝난다 — 기다리지 않는다(대기하면 매시 줄이 쌓인다).
  **차단(2)이 아니다**: 사이트가 거절한 게 아니라 우리가 양보한 것이고, 건너뛴 소스는 다음 런이 그대로
  가져간다(모든 쓰기가 자연키 upsert). commerce 의 구현은 소스별 세션 스코프 어드바이저리 락이다
  (`collectors/commerce/storage/locks.py`).

## DB 접속 노브 (secret 아님)
```
COSMAI_DB_HOST   기본값 127.0.0.1
COSMAI_DB_PORT   기본값 5434
```
- 호스트에서 `uv run cosmai ...` 는 shared-postgres 의 게시 포트(127.0.0.1:5434)로, 컴포즈 망 안에서는
  서비스명:5432 로 **같은 DB** 에 닿는다. 움직이는 것은 호스트와 포트뿐이다.
- compose 는 값이 없는 `${VAR}` 를 빈 문자열로 넘기므로 **빈 값은 기본값**으로 읽는다.
- 세 자리가 같은 규칙을 따른다: `db/runtime.py`(needs_runtime), `collectors/commerce/storage/db.py`,
  `collectors/youtube/storage/db.py`. 함수에 명시된 host/port 인자가 env 를 이긴다.
- 롤·DB 이름·secret 키 이름은 노브가 아니다 (`contracts/secrets.md`).
- **읽자마자 커밋한다. 서버 커서는 수명 내내 트랜잭션을 연다.** `needs_runtime` 롤은
  `db/bootstrap.sql` 이 `statement_timeout = 30s` · `idle_in_transaction_session_timeout = 15s` ·
  `transaction_timeout = 60s` 로 건다 — 트랜잭션을 연 채 느린 CPU/IO(토큰화, 큰 행렬 로드, LLM 왕복)를
  하면 그 자리에서 끊긴다. 픽스처: `tests/test_aggregate_scale.py`(`IDLE_LIMIT`) ·
  `tests/test_analyze_polarity.py`(`SQUEEZED_TIMEOUTS`) · `tests/test_ollama_predictor_connection.py`
  (같은 이름) 가 세 한도를 압축해 재현한다. 새 DB 코드 리뷰는 이 불릿을 체크리스트로 쓴다.

## 공통 운영 뷰 (각 수집기가 제공해야 하는 최소 형태)
```sql
-- db/views/collector_health.sql 이 commerce(trend_radar.run+fetch_log),
-- naver(needs.naver_run+naver_fetch_log), youtube(tubedepth.jobs) 세 팔을 UNION 한다
collector text, dataset text, run_id text, started_at timestamptz, finished_at timestamptz,
status text,          -- ok | partial | blocked | failed | running
requests int, ok int, blocked int, failed int, queued int, p90_ms int
```
P16 의 표가 이 뷰 하나로 나와야 한다. `requests` 는 fetch 시도 전부이고 `ok`·`blocked`·`failed` 는
2xx / 403·429 / (error 또는 5xx) 세 통뿐이다 — 셋의 합과 `requests` 의 차이가 어느 통에도 안 들어간
응답(예: 404)이다.

**youtube 팔은 #77 이 붙였다** (3단계에서 뺐던 것을 되돌렸다 — 사용자 결정 2026-08-24 가 걸었던 근거
셋이 #100·#101·#102 로 다 없어졌다: `jobs.error_code` 가 차단을 분류하고(#100), `jobs.started_at`·
`jobs.elapsed_ms` 가 생겼고(#101), `jobs.dataset` 이 CLI 동사를 담는다(#102 — `queue.enqueue` 가 새
행마다 적고, `watch` 가 만든 listing job 의 후속 job 은 원본의 `dataset` 을 물려받는다. `kind →
dataset` 역방향 매핑은 1:N 이라 성립하지 않아 별도 컬럼으로 뒀다. 백필은 없어 옛 행은 NULL 이다).

**youtube 의 한 행은 `(dataset, started_at 의 1시간 버킷)` 하나다.** `tubedepth.jobs` 에는 run 이
없으므로 `run_id` 는 NULL 이고 — 그 자리를 늘리지 않는 것이 #10 §A-2 의 판정이다 — commerce 의 run 에
해당하는 "유한한 일감 묶음" 을 뷰가 시간으로 만든다. 창(`최근 1h`)이 아니라 버킷인 것은 commerce 가
지난 run 을 전부 행으로 남기기 때문이다: 창이면 크론이 한 시간 쉰 순간 youtube 팔이 표에서 사라진다.
claim 된 적 없는 job(대기 중이거나 #101 이전의 옛 행)은 `started_at` 이 없어 `created_at` 으로 앉는다.

**`elapsed_ms` 의 뜻은 팔마다 다르다 — 이 뷰에서 가장 틀리기 쉬운 자리다.** commerce·naver 의
`fetch_log.elapsed_ms` 는 fetch 한 번의 왕복이고, youtube 의 `jobs.elapsed_ms` 는 job 하나의 전체
벽시계(claim→finish)다(#101: 캐시로 답한 job 은 fetch 를 아예 안 해서 왕복으로는 잴 것이 없다). 그래서
youtube 의 `p90_ms` 는 "요청이 얼마나 느렸나" 가 아니라 "일감 하나가 얼마나 걸렸나" 이고, 같은 이유로
`requests` 도 HTTP 요청 수가 아니라 끝난 job 수다 — 캐시로 답한 job 도 1 로 센다. 두 팔을 나란히 놓고
`p90_ms` 를 비교하면 안 된다. `elapsed_ms` 가 NULL 인 옛 행은 백분위에서 빠진다(0 으로 채우지 않는다).

`queued` 는 commerce·naver 에서 NULL 이다: 둘 다 크론이 부르는 배치 워커라 대기 큐라는 것이 아예 없다.
youtube 에서만 숫자이고, 0(큐가 비었다)과 NULL(큐라는 것이 없다)이 그래서 갈린다. `oldest_pending` 같은
큐 고유값은 컬럼으로 더하지 않는다 — 12컬럼은 위 sql 펜스가 정본이고, 늘리면 다른 두 팔도 NULL 자리를
하나씩 더 내야 한다. 큐 적체의 나이는 `queued > 0` 인 가장 오래된 버킷의 `started_at` 으로 읽는다.

`error_code`(`jobs.error_code`, `String(64)`)는 #100 부터 예외 클래스명이 아니라 아래 분류값이다
(`collectors/youtube/cli.py::_classify_error`). 위 youtube 팔이 정본으로 읽는 어휘가 이것이다 —
`blocked` 는 `quota`·`rate_limited`·`http_403`(quotaExceeded 아닌 403)·`http_429` 를
합친 것으로, commerce `fetch_log.status` 의 403·429 정의와 이어진다.
- `quota` — 403 + 본문 `error.errors[].reason == "quotaExceeded"` (YouTube Data API 는 쿼터 소진을
  429 가 아니라 이 모양으로 준다).
- `rate_limited` — 429.
- `http_<code>` — 그 외 HTTP 상태(`http_403`은 quotaExceeded 가 아닌 403 — forbidden·
  accessNotConfigured 등 — 을 포함, `http_500` 등).
- `transport` — HTTP 상태 자체가 없는 실패(DNS·소켓·타임아웃).

`error_message`(`Text`)는 그대로 `str(error)` — 원문 예외 텍스트는 컬럼을 옮기지 않았다, `error_code`
가 클래스명 자리를 분류값으로 대체했을 뿐이다. 라이브 트랜스포트가 아직 없어(#10 이전, `_RaisingFetcher`
가 기본값) 실제 403 응답 본문에 이 코드가 닿아본 적은 없다 — 분류기는 `urllib.error.HTTPError` 모양
(`.code`·`.read()`)을 기준으로 짰고, #10 이 어떤 전송을 붙이든 그 모양으로 예외를 던지게 하는 것이
그때의 몫이다.

분석판은 `db/views/analysis_health.sql` 의 `needs.analysis_health` 다: run 별 started/finished/
status/versions 와 그 run 의 `metrics_need`·`metrics_wish` 행 수. `need_mention`·`wish_mention` 은
run_id 를 갖지 않으므로(versioning.md A19) 각 단계가 만든 행 수는 `analysis_run.note` 가 이름=값으로
나른다. `db/migrate.sh` 가 배포마다 다시 적용한다 (CREATE OR REPLACE).

### 단계의 지금 상태 — `needs.pipeline_health`

위 둘은 **run 하나당 한 줄인 로그**라 "지금 무엇이 막혔나" 에 답하지 못한다. 그 답을 지는 것이
`db/views/pipeline_health.sql` 의 `needs.pipeline_health` 이고, 기대 주기는 `needs.pipeline_stage`
(DDL 007)가 선언한다 — 크론탭(`stack/crontab.d/`)은 DB 에 없고 포털은 PostgREST 로 DB 만 읽는다.
크론탭을 파싱해 넣지 않는 이유는 `enabled` 다: `youtube watch` 는 크론 줄이 **있는데** compose
profile 뒤라 안 돈다. 선언과 크론탭의 어긋남은 `tests/test_pipeline_stage.py` 가 막는다.

행은 선언된 단계마다 정확히 하나이고, 컬럼은 `stage_key` · `arm` · `dataset` · `enabled` ·
`expected_interval` · `last_success_at` · `last_run_at` · `last_run_status` · `overdue_by` ·
`freshness` · `requests` · `ok` · `blocked` · `failed` · `p90_ms` 다.

**두 사실을 하나로 접지 않는다.** `freshness` 는 "안 돌았다" 만, `last_run_status` 는 "돌았는데
어떻게 끝났나" 만 말한다. 셋째 사실을 넣지 않는 이유는 3일 전에 실패하고 그 뒤로 안 돈 단계가 한
값으로는 둘 중 하나로만 보이기 때문이다.

`freshness` 는 마지막으로 **돈** run 의 `finished_at` 을 기준으로 다섯 값 중 하나다. "돈" 것에는
`status = 'ok'` 와 **`partial` 이 든다** — 돌았고 대부분을 걷었으면 돈 것이고, 얼마나 잘 끝났는지는
`last_run_status` 가 나란히 말한다. 들지 않는 것은 `yielded`(소스 락에 전부 밀려 물러나 아무것도 안
걷었다, #78) · `failed` · `blocked` 다. 이 선을 "깨끗하게 돌았나" 로 좁히면 매일 정시에 돌지만 늘
`partial` 인 단계가 이틀 뒤 `stalled` 로 굳어 영원히 빨갛다(#154 가 그것을 실측으로 잡았다):

| 값 | 뜻 |
|---|---|
| `disabled` | `pipeline_stage.enabled = false` — 선언상 안 도는 것. 최신 성공이 있어도 이것이 이긴다 |
| `never` | 성공한 run 이 한 번도 없다. `overdue_by` 는 NULL 이다 — 늦었냐는 질문이 성립하지 않는다 |
| `ok` | 마지막 성공이 `expected_interval` 안 |
| `late` | 그것을 넘었지만 `2 × expected_interval` 안 |
| `stalled` | `2 × expected_interval` 을 넘었다 |

눈금이 절대값이 아니라 주기의 배수인 것은 주기가 5분(`youtube work`)부터 한 달(`naver datalab`)까지
벌어져 있어서다 — 상수 여유를 두면 어느 한쪽에서 반드시 틀린다.

분석 두 줄은 `analysis_run.note` 의 `missing=` 으로 갈린다. `stage` 는 구현 판본을 달고 있어 그대로
쓸 수 없고, 크론 줄로도 갈리지 않는다. `eval:*`·`trend-quarter:*` 는 크론 단계가 아니라 이 뷰에
오지 않는다. 분석 팔에는 외부 fetch 가 없어 `requests`·`ok`·`blocked`·`failed`·`p90_ms` 가 NULL 이다.

### 무엇이 무엇을 먹이는가 — `needs.pipeline_edge`

`pipeline_stage` 는 단계 *목록* 일 뿐 관계가 없다. 관계는 `needs.pipeline_edge`(DDL 008)가 진다 —
그림(#142)도 상태 전파(#143)도 계보 추적(#144)도 그 위에 선다.

**노드 표를 따로 두지 않는다.** 단계는 `pipeline_stage.stage_key` 가 이미 선언하고, 저장소는 **정규화된
표 이름 그 자체**가 키다. 그 이름이 실재하는지는 `tests/test_pipeline_edge.py` 가 **이 체크아웃의 DDL** 에
묻는다 — 라이브 DB 에 묻지 않는 이유는 그러면 "이 체크아웃이 아는 표인가" 가 아니라 "지금 그 서버에
있나" 를 재게 되고, 그러면 upstream 계약이 남의 객체를 참조해도 초록이기 때문이다(#107·#150 과 같은 자리).

방향은 둘 다 담는다 — `stage → store` 는 **쓴다**, `store → stage` 는 **읽는다**. 한 방향만 담으면
계보가 한쪽으로만 흘러 지표에서 수집분으로 거꾸로 타지 못한다. **단계끼리는 잇지 않는다**(DDL 이 CHECK 로
막는다): 단계 사이에는 언제나 그것이 남긴 표가 있고, 건너뛰면 계보가 "무엇을 통해" 를 잃는다.

저장소 노드를 고르는 기준은 **다른 단계나 화면이 소비하는 표** 다. 그 기준이 최소 집합을 강제한다 —
단계마다 적어도 하나의 엣지가 있어야 하므로(테스트가 묻는다) 어느 단계의 유일한 산출물은 반드시 노드가
된다. 지금은 **단계 14 + 저장소 14 = 28 노드, 31 엣지**. 일부러 뺀 것과 그 이유는 `db/seed/pipeline.py`
의 주석에 있다 — 그중 `needs.corpus_*` 는 포크 DDL 023 이 만드는 표라 upstream 계약이 참조하지
않는다(운영에서 `analyze` 가 실제로 읽으므로 그림이 그만큼 빈다; 그 엣지는 포크가 제 계약에 더한다).

읽는 롤은 둘이다: `needs_runtime`(뷰 파일의 GRANT)과 `postgrest_anon`
(`db/grants/postgrest_anon_needs.sql`) — 포털은 anon 으로 묻기 때문에 앞의 것만으로는 화면이 아무것도
받지 못한다. 상류 두 뷰는 anon 에 열지 않는다: 화면이 읽는 것은 판정이 끝난 이 뷰 하나다.

## 분석
```
cosmai analyze <stage> [--since <date>] [--scope <category>] [--impl <spec>] [--missing]
  stage ∈ {link, polarity, aggregate, all}
cosmai eval <task>        task ∈ {polarity, wish_class, brand_link, product_match}
cosmai lexicon {load, diff, activate} --kind <kind> --version <n>
```
- T14: `extract` 는 단독 stage 가 아니다 — 후보만 만들고 아무 행도 쓰지 않아 멱등을 관측할 수 없다. 추출은 `polarity` 안에서 돈다(`Extractor` 프로토콜은 그대로).
- B11: `eval aspect` 는 평가셋도 기준선도 0행이라 뺐다. 되살리려면 평가셋 + `interfaces.md` 기준선 표의 행이 같은 PR 에 온다.
- 모든 단계는 **자연키 upsert** 로 멱등. 재실행은 같은 결과를 만든다.
- 산출 행은 반드시 `*_version` 을 가진다 (`versioning.md`).
- `analyze --impl <spec>` 는 `eval` 과 같은 레지스트리·같은 스펙 문법이다(`ollama:gemma4:latest`·`llm:claude-sonnet-5`). 없으면 규칙이 돌고, 있으면 그 구현의 버전이 `analysis_run.versions.polarity` 와 산출 행에 남는다. **소유 표에 자기 자리가 없는 구현은 `--scope` 없이는 거절한다** — 무료여도 그렇다(analyze 의 기본은 전량이라 그런 구현의 스코프 없는 한 줄은 곧 전량 재라벨이고, 값은 돈이거나 GPU 시간이다). 표에 자리가 있는 구현(=주인)은 `--scope` 없이 돌 수 있다: 그 한 줄이 도는 것은 자기 `(scope, 기간)` 뿐이고 `--scope` 는 그것을 더 좁히기만 한다. 그 `--scope` 가 아직 소유 표에 주인이 없는 `lexicon_category` 여도 거절한다: 등록이 패스보다 먼저여야 그 결과가 다음 05:00 에 지워지지 않는다. 유료(`registry.is_paid`) 구현은 그보다 앞서 돈을 이유로 한 번 더 걸린다 — `eval` 의 `--split` 강제와 같은 자리다. 두 거절 모두 run 이 열리기 전이라 blocked(종료 코드 2)이고, 판정은 `analysis/polarity/ownership.py` 가 한다.
- `analyze all` 은 `needs.analysis_run` 행 하나를 만들고(polarity 가 열고 aggregate 가 그 `run_id` 로
  metrics 를 쓴다) `versions` 에 linker·extractor·polarity·aggregate 와 `lexicon`(활성 버전 + ruleset)을
  기록한다. 한 단계라도 실패하면 그 run 은 `status='failed'` + note 로 닫히고 종료 코드는 1 이다.
- `analyze all` 의 aggregate 모집단은 그 run 이 방금 쓴 `extractor_version` 하나다 — 시드(`slice-*`)를
  같은 scope 에 섞으면 한 문장이 두 번 세어진다. 고른 모집단은 `versions.extractor` 에 남는다.
- 그 모집단 안에서 **극성 구현은 (scope, 기간)마다 하나다**: 소유 표(`analysis/polarity/ownership.py`)가
  한 `lexicon_category` 를 한 `polarity_version` 과 그 판본이 책임지는 첫 달(`since`, `need_mention.month`
  와 같은 YYYY-MM)에 배정하고, 그 scope 의 `month >= since` 인 `need_mention` 행은 **주인만** 쓰고
  지운다. 반대 방향도 같다: **주인은 자기 `since` 앞의 달을 쓰지도 지우지도 않는다.** 양쪽 다 읽기
  건너뛰기·삭제문·`DO UPDATE` 에 같은 모양으로 선 소유 술어 하나가 세운다. 소유가 행이 아니라
  `(scope, 기간)` 단위인 것은 005 의 자연키가 `polarity_version` 을 담지 않기 때문이고, 기간이 붙는
  것은 등록과 패스를 떼어놓기 위해서다 — `since` 를 다음 달로 적어 등록하면 그 앞의 달은 규칙이 계속
  갱신하므로, 전량 패스가 끝나기를 기다렸다가 등록할 이유가 없다. 주인 없는 `lexicon_category`,
  주인의 `since` 앞의 달, `lexicon_category IS NULL` 인 행(유튜브 댓글·카테고리를 못 붙인 리뷰)은
  지금처럼 규칙이 갱신한다.
- 그래서 **주인 기간인데 주인이 아직 안 닿은 달에는 행이 없다**(등록 직후, 그리고 주인의 패스 사이).
  규칙이 임시로 채우지 않는 것은 두 구현이 같은 문장에서 다른 `need_key` 를 고르면 그 임시 행이 주인의
  행 옆에 그대로 남아 집계가 한 문장을 두 번 세기 때문이다 — 아래 '카테고리가 움직인 문장' 문단이 같은
  자리를 말한다. 이 구멍의 수명은 주인 패스의 주기다.
- **`--missing` 은 주인의 증분 실행이다**: 고르는 기준이 날짜가 아니라 **'이 실행이 지금 쓸 모양
  (`extractor_version`+`polarity_version`)의 `need_mention` 행이 아직 없는 원천 행'** 이다. 한 페이지를
  읽을 때마다 그 `(src, ref)` 들을 `need_mention` 에 묻고, 있으면 추출도 판정도 하지 않는다. 후보가
  하나도 없는 리뷰는 어느 실행도 행을 만들지 않으므로 매번 추출을 다시 타지만 판정은 부르지 않는다
  (추출은 규칙이라 싸다). **이 모드는 아무것도 지우지 않는다** — `replace_stale` 을 부르지 않고,
  따라서 그 달이 반쪽으로 남는 창도 rewriting 표식도 없다. 없는 것을 더할 뿐이므로 갈아끼우기(역사
  보정·판본 상승·`need_key` 가 바뀐 옛 행 청소)는 여전히 `--scope` 전량 경로의 몫이다. 소유가 없는
  실행(규칙, 표에 없는 구현)에는 '내 판본 행'이 곧 규칙 모집단 전량이라 뜻이 없어 **거절한다** —
  남의 scope 거절과 같은 자리·같은 모양이다(run 이 열리기 전, `status='failed'` + 종료 코드 1).
  그 run 의 `note` 는 `missing=1` 을 달아 전량 패스와 갈린다(증분은 `replaced=0` 을 언제나 낸다).
- `--since <date>` 와는 축이 다르다: `--since` 는 `coalesce(written_at, captured_at)` 로 **읽기와
  삭제를 함께** 자르고, `--missing` 은 **이미 한 일**을 자른다. 수집이 늦게 오므로(`formats.md` §시간)
  그 둘은 겹치지 않는다 — 어제 긁힌 옛 리뷰는 롤링 `--since` 가 놓치고, 고정 컷은 컷 이후 전부를 매일
  다시 판정한다. 크론이 도는 것은 `--missing` 쪽이다.
- **`--since D` 는 삭제도 같이 좁힌다**: D 가 든 달의 `observed_at >= D` 인 행만 지운다(`need_mention`·
  `wish_mention` 둘 다). 좁히지 않으면 그 달의 D 이전 행은 지워지고 다시 쓰이지 않아, 매 실행이 같은
  구멍을 판다. 삭제문의 `observed_at` 은 원천의 `coalesce(written_at, captured_at)` 과 같은 값이라
  읽기 필터와 같은 행 집합을 가리킨다.
- **주인 실행은 자기 `since` 앞의 달을 아예 훑지 않는다**: 그 달에는 소유 술어가 한 행도 통과시키지
  않아 삭제도 0행, 쓰기도 0행이므로 순회가 순수한 비용이다. 자르는 기준은 그 실행이 닿는 `(scope,
  since)` 들 중 가장 이른 `since` 이고(`--scope` 가 있으면 그 scope 의 것), `ALWAYS` 면 아무것도
  자르지 않는다. 모드와 무관하다.
- 그래서 한 문장의 라벨은 그 문장의 `lexicon_category` 를 소유한 구현의 것 하나다 — 그 카테고리가
  움직이지 않는 동안은. `rank_snapshot` 의 최신 행과 `category_map` 이 매일 다시 계산하므로 제품은
  카테고리를 옮겨 다니고, 옮겨간 뒤 옛 scope 에 남은 주인의 행은 아무도 지우지 못한다(주인 아닌 실행은
  손대지 않고, 주인의 `--scope` 삭제는 자기 판본 행을 남긴다). 그 위에 새 scope 의 구현이 같은 문장을
  자기 것으로 뽑으므로, **두 구현이 다른 `need_key` 를 고르면 그 동안 한 문장이 두 행을 갖고 집계도 둘을
  센다** — 같은 `need_key` 면 자연키가 겹치고 소유 술어가 갱신을 막아 주인의 행 하나로 남는다. 옛 행은
  주인의 `polarity_version` 이 오르는 첫 실행이 치운다.
- 반대로 제품이 남의 scope에서 **주인의 scope 안으로** 옮겨오면 회수 주체가 다르다. 옮겨오기 전에
  규칙이 써 둔 행은 저장된 `lexicon_category` 가 옛 카테고리 그대로이고, 규칙 실행은 **그 달이 주인
  기간이면** 그 유닛을 건너뛴다(`analysis/polarity/pipeline.py` 가 `lexicon_category` 와 그 유닛의 달을
  소유 술어에, 그리고 지금 `--scope` 에 견줘 판정 자체를 하지 않는다). 주인의 `since` 앞의 달이면
  규칙이 그 유닛을 그대로 돌아 **새** 카테고리로 다시 뽑으므로, 아래의 이중 계수가 그 달에서는 규칙
  실행 하나로 생긴다. 주인이 도는 `--scope` 삭제문(`NEED_DELETE_SCOPED`)은
  `lexicon_category = <그 scope>` 로 좁혀져 있어 옛 카테고리를 단 그 행을 맞히지 못한다 — 그래서 이
  방향의 옛 행을 치우는 것은 주인 패스가 아니라 **규칙 자신의 버전이 오르는 실행**이다:
  `NEED_DELETE` 의 `NOT (extractor_version = ... AND polarity_version = ...)` 술어가 규칙의
  `extractor_version`·`polarity_version` 이 바뀔 때 그 옛 행을 stale 로 잡아 지운다. 그 사이 이중
  계수는 주인 패스를 몇 번 다시 돌려도 없어지지 않는다.
- `metrics_need` 의 `scope` 축은 `lexicon_category` 가 아니라 원천 카테고리이고 rollup
  scope(`all`)는 전 카테고리를 합치므로 **한 집계 행이 두 구현의 라벨을 함께 셀 수 있다**. 무엇이 어느
  scope 를 셌는지는 소유 표가 답한다: `analyze all` 의 `analysis_run.versions.polarity` 는 **그 run 을
  돈 구현**의 버전이지 그 run 이 집계한 모든 라벨의 버전이 아니다.
- `--scope <값>` 은 **두 축을 다 받는다**(#38): 값이 `lexicon_category` 면 aggregate 는 그 run 의 모집단에서
  그 라벨을 단 언급들의 **원천 카테고리 집합**으로 펼쳐 그 scope 들에 쓰고, 원천 카테고리 문자열이면 그
  한 scope 만 쓴다(`analysis/aggregate/pipeline.py` 의 `scopes_for`). 어느 쪽이든 `metrics_need.scope` 에
  남는 값은 위 줄 그대로 **원천 카테고리**이고, 펼친 scope 의 행은 `--scope` 없는 실행이 그 카테고리에
  쓰는 행과 같다 — scope 는 어느 카테고리를 쓸지를 고를 뿐 그 안에서 무엇이 세어지는지를 바꾸지 않는다.
  역방향(lexicon → 원천)은 `needs.category_map` 만으로 복원되지 않는다: 표에 없는 leaf 는 항등이고
  (`formats.md`) `name_keyword` 라벨은 원천 카테고리가 아예 없다 — 그래서 답은 표가 아니라 그 run 의
  언급에서 나온다.
- 펼치고도 **조용히 0 을 내는 것**은 막는다(#38): `--scope` 실행이 aggregate 까지 갔는데
  `metrics_need` 를 0행 쓰면 그 run 은 락을 놓친 실행과 같은 어휘·같은 자리로 `partial` + 종료 코드 **1**
  로 닫히고, note 와 stdout 이 준 scope 값과 그 `lexicon_category` 를 단 언급들이 실제로 갖고 있는 원천
  category 문자열을 말한다(하나도 없으면 없다고 말한다 — `name_keyword` 라벨이 그 갈래다).
  **`metrics_wish` 는 이 술어에 들어가지 않는다** — `analysis/aggregate/pipeline.py` 의 wish 집계는
  `--scope` 를 아예 보지 않고 그 모집단의 위시 전량을 매번 다시 세므로(`WISH_SCOPES` 는 스코프 인자와
  무관), 0 이든 아니든 이 scope 에 대해 아무것도 말해주지 않는다. `--scope` 없는 실행(05:00 크론)은 이
  술어를 절대 타지 않는다.
- 주인이 아닌 실행이 `--scope <남의 scope>` 를 지정하면 **거절한다** — 조용한 무동작이 아니라 그 단계가
  실패로 끝나고(`analysis_run.status='failed'`, 종료 코드 1) 메시지가 주인의 `polarity_version` 과 소유
  표의 경로를 말한다.
- **analyze 실행은 한 번에 하나다.** 05:00 크론(`analyze all`)과 사람이 손으로 도는 극성 패스는
  겹치는 것이 정상이고, 겹치면 서로의 반쯤 쓴 상태를 읽는다: polarity 는 한 달치를 지우고 **커밋한 뒤**
  페이지별로 다시 쓰고, aggregate 는 `extractor_version` 만 걸고 need_mention 전량을 여러 트랜잭션에
  나눠 읽는다(스냅숏이 아니다). 그래서 락은 **scope 별도 단계별도 아닌 전역 하나**다 — 어느 쪽으로
  좁혀도 `polarity --scope <한 카테고리>` 와 전량을 읽는 `aggregate` 를 가르지 못한다. 다른 실행이
  그 락을 쥐고 있으면 **한 단계도 돌지 않고** 건너뛰고, 사유를 적은 `partial` run 행 하나를 남긴 뒤
  종료 코드 **1** 로 끝난다 — 운영자가 보는 것은 그 행이고, 기다리지는 않는다(수집기와 같은 규약:
  우리가 양보한 것이지 거절당한 게 아니고, 모든 단계가 자연키 upsert 라 다음 실행이 그대로
  가져간다). 구현은 세션 스코프 어드바이저리 락이고 작업 커넥션이 쥔다
  (`analysis/locks.py`).
- 그 락 덕분에 **반쯤 다시 쓰인 달**을 이름으로 말할 수 있다. polarity 는 한 달을 지우기 직전
  `analysis_run.note` 에 `rewriting=<src>/<month>[/<scope>]` 를 적고 그 달을 다 쓰면 지운다. 실행이 그
  사이에 죽으면 그 표식이 남고, 락을 쥔 다음 실행은 **열려 있는 표식은 죽은 실행의 것뿐**이라는
  사실로 그것을 찾아낸다: 그 run 을 failed 로 닫고(영원한 `running` 을 남기지 않는다) 어느 달인지를
  자기 note 와 stdout 에 적은 뒤 partial(**1**)로 끝난다. 찾아내는 조건은 표식이지 `status` 가 아니다
  — 실무에서 가장 흔한 죽음(ollama 예외·`statement_timeout`)은 잡혀서 run 이 `failed` 로 닫히므로
  `running` 만 보면 그 반쪽 달을 통째로 놓친다. 말하는 것은 **한 번뿐**이고, 말한 실행이 그 note 에
  `stale-reported` 를 붙여 그 사실을 적는다.
- 그 "한 번"이 충분한지가 scope 마다 다르다. **주인 없는** scope 의 반쪽 달은 다음 밤 규칙 실행이 그 달을
  통째로 다시 써서 스스로 메워진다 — 한 번 말하면 그것으로 끝이다. **주인 있는** scope(선블록→gemma4)의
  반쪽 달은 규칙 실행이 배제하므로 아무도 메우지 않고, 한 번 말한 뒤로는 아무도 다시 말하지 않는다:
  그 달을 되찾는 길은 사람이 주인의 패스를 그 달에 다시 돌리는 것 하나뿐이고, 그때까지 남는 증거는 죽은
  run 의 note 에 계속 붙어 있는 `rewriting=` 표식이다.

## 검색 (#28 → 포크 cosmai-import-ydc, upstream PR #59)
```
cosmai retrieval chunk  [--since <date>] [--source <s>]...
cosmai retrieval search --query <q> [--engine <e>] [--source <s>]... [--top <n>] [--vectors <path>]
cosmai retrieval eval   --mode <m> [--engine <e>] [--source <s>]... [--out <csv>] [--vectors <path>]
cosmai retrieval embed  [--model <m>] [--device <d>] [--batch <n>] [--vectors <path>]
cosmai retrieval terms  [--source <s>]... [--top <n>]
  source ∈ {youtube_comment, youtube_video, youtube_transcript, commerce_review}
  engine ∈ {bm25, vector, hybrid}      mode ∈ {literal, heldout}
```
- **주제 사전은 `needs.aspect_lexicon` 의 활성 버전이다**(`ruleset='retrieval-topic'`, 포크 #8). 그 별칭이
  BM25 토큰 확장(Kiwi 사용자 단어 + 부분문자열 확장)과 평가 정답(`match_topics`)을 함께 정하므로, 사전을
  바꾸는 길은 `cosmai lexicon load/diff/activate` 하나다 — 적재 원본은 `analysis/retrieval/dict/topics_v1.csv`.
  aspect 버전 하나 = **모든 룰셋을 합친 aspect 사전 전체**라(`activate` 는 kind 단위로 켠다) 주제를 새
  버전으로 올릴 때는 극성 쪽 CSV(`eval/lexicon/aspect_lexicon_v1.csv`)도 같은 버전으로 함께 적재한다.
  **v3 적재는 아직 안 했다**(2026-08-27, 운영은 v2 가 활성) — 그전까지 검색이 보는 사전은 v2 이고, 레포의
  적재 원본은 이미 v3 내용이라 그 둘이 갈려 있다. 켜는 순간 `선크림` 주제가 12,197 → 12,418 문서를 보게
  되고 색인 캐시가 사전 지문으로 무효화된다(`pipeline.index_signature`).
- **색인·추출 축에는 불용어 목록도 조사 목록도 두지 않는다**(포크 #37, ydc `lexicon.json` 처분). 그 축은
  색인 토큰화(`bm25.tokenize`)와 `terms` 다 — 일반어는 lift 축이 걷어내고(`analysis/retrieval/terms.py`),
  조사는 Kiwi 의 태그가 가른다: `KIWI_TAGS` 밖(조사 `J*`·어미 `E*`)을 버리고 한 글자 명사도 버려서, ydc
  가 코퍼스 실측으로 검증한 조사 30개를 어간에 붙여도 토큰이 하나도 달라지지 않는다(실측 2026-08-26 ·
  30/30 · `tests/retrieval/test_particles.py`). **질의 축은 이 문장이 정하지 않는다**(포크 #46): 질의를
  서술하는 말은 df 로 갈리지 않아(ydc 실측 `소비자` 289 < `백탁` 338) 다른 근거가 필요하고, 그 판단은 그
  이슈가 아래 두 항목에서 진다. 축이 어느 쪽이든 살아남는 것은 파일이 아니라 **버전을 받는 행**이다
  (포크 #8) — 주제 표기는 위 사전(`ruleset='retrieval-topic'`), 브랜드 표기는 `needs.entity_lexicon`
  (`formats.md` §사전 CSV).
  `lexicon.json` 의 별칭 **9개가 다 판정됐다**(포크 #56): 셋(`썬크림`·`자외선차단제`·`코스알엑스`)은
  이미 있었고, `선크림추천` 은 확장이 잡아 행이 필요 없으며, **셋**(`썬쿠션`·`썬스틱`·`sunscreen`)이 주제
  사전 v3 의 행이 됐고, `올영`(df 5,583)과 `sunstick`(df 3)은 미등재다 — 앞은 정본이 `tier='stop'` 이라
  행을 더해도 소비자가 없고, 뒤는 바닥(`terms.MIN_DOCS` 5) 아래이며 그 3편을 이미 `선크림` 이 본다.
  같은 v3 가 #37 의 후보 7종도 판정해 `선에센스`·`선스프레이`·`속건조`·`파데프리` 넷을 더 올렸다. 판정
  원장과 등재 기준 넷은 `formats.md` §주제 사전 v3 이고, 그 수를 다시 재 원장과 맞대는 길은
  `tool/measure-lexicon-candidates` 다.
- **질의 토큰화는 색인 토큰화와 갈린다**(포크 #46). 색인은 `bm25.tokenize`, 질의는 `bm25.tokenize_query`
  — 같은 토큰화에 **질의 불용어 제거**만 얹은 것이고, `Index.search` 만 그쪽을 탄다. 색인에서 빼지 않는
  이유는 그러면 `소비자` 를 직접 찾는 질의를 못 하게 되기 때문이다. 뺄 근거가 lift 도 idf 도 아닌 이유는
  그 말들이 흔해서가 아니라 **질문을 서술하는 말이라 주제가 아니어서**다 — 통계로는 반대로 나온다(위의
  `소비자` 289 < `백탁` 338). 그래서 통계가 아니라 판단이고, 판단이므로 **버전을 받는 행으로 산다**:
  `needs.entity_lexicon` 의 `kind='stopword'` · `canonical='query'` 활성 버전이 정본이고, 고치는 길은
  `cosmai lexicon load/diff/activate --kind stopword` 하나다(적재 원본은 주제 사전과 같은 자리의
  `analysis/retrieval/dict/query_stopwords_v1.csv`). 그 kind 는 주제 사전과 **활성 버전이 따로**다 —
  `entity_lexicon` 의 `activate` 는 kind 하나만 켜고 끄므로(`db/lexicon.py` `ENTITY_ACTIVATE`), 질의
  불용어 개정과 aspect 사전 개정이 서로를 끄지 않는다. 버전 **번호표**는 그렇지 않다 —
  `formats.md` §entity 사전의 `kind='stopword'` 가 그 한계와 포크 #58 을 적는다.
- 그 목록에 걸리는 규칙 셋. (1) **질의가 전부 불용어면 지우지 않는다** — 토큰 0개는 결과 0건이고, 필러가
  낀 순위보다 나쁘다. (2) **색인 캐시를 무효화하지 않는다**: `pipeline.index_signature` 는 이 목록을 물지
  않고, 물어서도 안 된다 — 색인은 `tokenize` 그대로라 목록이 바뀌어도 같은 색인이 맞다. heldout 정답을
  정하는 `eval.docs_with_tokens` 가 `tokenize_query` 가 아니라 `tokenize` 를 쓰는 것도 같은 이유다(정답
  정의는 색인 축이다). (3) **활성 버전이 없으면 빈 목록이고 막힘이 아니다** — 주제 사전과 다른 자리다:
  주제 사전이 없으면 정답이 0건이라 점수가 거짓이 되지만, 질의 불용어가 없는 검색은 이 목록 이전의 검색
  그대로다. 그래서 `search` 는 뺀 토큰이 있을 때만 stderr 한 줄로 말하고 종료 코드를 바꾸지 않는다
  (아래 커버리지 경고와 같은 자리). **v1 적재는 아직 안 했다**(2026-08-26) — 그전까지 `search` 가 보는
  목록은 비어 있고, 위 규칙 셋은 적재·활성 뒤에야 관측된다.
- **질의마다 엔진을 고르는 라우터는 두지 않는다**(포크 #47). `--engine` 은 사람이 준 값 그대로 간다.
  ydc `v0.3.0` 의 규칙 라우터(`rag/router.py`)를 승격하지 않았고 그 근거 — 신호 넷 중 둘의 원천이 없어 갈래 둘이 막힌다 ·
  **성분명 판정의 정본이 토크나이저 사전이 아니다** · 우리 사전 위의 오라우팅 실측 — 은 `interfaces.md`
  §질의 라우팅 이 진다. 새 하위명령도 새 종료 코드도 늘지 않는다.
- **벡터 검색에 유사도 하한선을 두지 않는다**(포크 #48). `--engine vector`·`hybrid` 는 코사인이 얼마든
  상위 `--top` 을 채운다 — 넣지 **않기로 한** 것이고, 진짜 질의(주제 별칭 61개)와 코퍼스에 없는 성분명의
  최고 코사인 분포가 갈리지 않는다는 실측이 그 근거다(`interfaces.md` §벡터 하한선). ydc `v0.3.0` 의
  `vector_threshold.py` 를 승격하지 않았고, 새 옵션도 새 종료 코드도 늘지 않는다.
- **대신 근거 없는 질의를 청크빈도로 막는다 — `vector`·`hybrid` 에만**(포크 #48,
  `analysis/retrieval/grounding.py`). 길이 4 이상인 질의 토큰 중 빈도가 0 인 것이 있으면 `search` 는 순위를
  매기지 않고 stderr 한 줄 + 결과 0건으로 답한다 — 코퍼스가 그 이름을 한 번도 말한 적이 없다는 뜻이라,
  결과가 나와도 그 이름과 무관한 문서다.
  **종료 코드는 이미 있는 `1`(결과 없음)이고 새 코드가 늘지 않는다.**
  토큰이 0개인 질의(`톤 업`·키릴 표기)는 빈도로 판정하지 않고 통과시킨다 — 막으면 벡터가 유일하게
  답하는 자리를 막는다.
- **`bm25` 의 동작은 이 이슈 전과 같다.** 어휘 검색은 빈도 0 인 낱말을 idf 0 으로 무시하고 **남은 낱말로
  답하므로**, 게이트를 걸면 "진짜 주제 + 코퍼스에 아직 없는 신제품 이름" 질의에서 예전에 나오던 부분 답이
  0건이 된다. 그 손해는 아무도 재지 않았고(질의 로그가 없다), 재지 않은 손해를 감수할 이유가 없다.
- 그래서 **`--engine vector` 도 BM25 색인을 연다**(게이트가 보는 빈도가 거기 있다). `bm25`·`hybrid` 가
  이미 내던 비용을 vector 쪽도 내게 된 것이고, 그 대가로 코퍼스에 없는 이름이 상위 k 를 채워 근거로
  인쇄되는 일이 없어진다. 캐시가 있으면 피클 한 벌, 없으면 38만 청크를 형태소 분석하는 십수 분인데 —
  **캐시는 `--source` 조합마다 따로다**(`pipeline.index_signature` 가 `sources` 를 문다). 좁혀 쓰는 vector
  검색은 지금까지 색인을 연 적이 없으므로 그 조합의 캐시가 존재한 적이 없고, **첫 호출이 무조건 십수
  분**이다. 벡터 파일이 없는 호스트는 그 비용을 치른 **뒤에야** blocked(2)를 본다 — 게이트가 저장소보다
  앞이다. `retrieval eval` 은 이 게이트를 타지 않는다 — 타더라도 주제 별칭 61개 중 막히는 것이 0개다
  (`interfaces.md` §벡터 하한선).
- `terms` 는 그 사전이 **못 잡는** 고빈도 명사와 사전 표기의 등장 문서 수를 stdout 표 두 개로 낸다 —
  사람이 읽고 위 CSV 를 고치는 재료다. 파일로 떨구지 않는다: 매일 자라는 코퍼스의 스냅숏이라 레포에
  두면 낡고, 무엇보다 두 번째 사전으로 오해된다. 남기려면 리다이렉트한다.
- `chunk` 만 쓰기다(`needs.retrieval_chunk`). 나머지 넷은 그 표와 파일을 읽는다. 원천은 다른 스키마이고
  `db/grants/needs_runtime_reader.sql` 의 SELECT 로만 닿는다 — 수집기가 자기 스키마에만 쓴다는 규칙의 반대편이다.
- 멱등: `chunk` 는 `text_md5` 가 같은 행을 건드리지 않는다(재실행 = 변경 0). `embed` 는 전량 재인코딩이다.
- `chunk` 의 **삭제 판정은 이번 실행이 훑은 범위 안에서만 선다**(포크 #23). 짧아진 문서의 꼬리와 본문이
  통째로 빈 문서의 청크는 언제나 지운다 — 근거가 훑은 문서 자체에 있다. 원천에서 **행이 사라진** 문서는
  `--since` 없는 전량 실행에서만 지운다: 증분 실행에서 "안 나왔다"는 "범위 밖이라 안 봤다"와 구분되지
  않는다. 훑어서 문서가 0건인 소스도 같은 이유로 제외한다("다 사라졌다"와 "못 읽었다"가 같아 보인다).
  건너뛴 것은 실행 note 가 말한다.
- **`--vectors` 는 세 하위명령에서 같은 뜻이다**(벡터 저장소 경로). `--out` 은 `eval` 에서만 쓰고 점수 CSV 를 뜻한다.
- **기본 `--engine bm25` 는 literal 용도 기준이다** — heldout 에서 bm25 는 P@10 0.000·Hit 0%, vector 는
  0.062·25% 인데 literal 에서는 bm25 가 P@10 0.864 로 가장 높다(여섯 줄 전부는 `contracts/interfaces.md`
  §검색 실측). 탐색 용도의 기본값은 포크 이슈 #11 에서 정한다.
- 종료 코드: 0 ok · 1 partial(`chunk` 의 계약 위반, `search` 의 결과 없음 — 근거 없는 질의가 막힌 것도 여기다, `eval` 의 채점된 질의 0개와
  `terms` 의 훑은 문서 0건 — 둘 다 청크가 비었다는 뜻이다) · 2 blocked(연결 거절, 벡터 저장소를 읽을 수
  없음 — 파일이 없는 것과, 매니페스트에 `model`·`query_prefix`·`l2_normalized`·`dim` 이 빠졌거나 그것이
  행렬과 어긋난 것이 같은 자리다, **활성 주제 사전 없음** — `cosmai lexicon load/activate` 를 아직 안
  돌렸다는 뜻이라 실패가 아니라 막힘이다). `embed` 에는 partial 이 없다 — 전량 재인코딩이라 반쯤 된 저장소를 남기지 않고, 끝나면 0 이다.
- **커버리지 경고는 stderr 로 나가고 종료 코드를 바꾸지 않는다** — `search`·`eval` 의 vector·hybrid 는 저장소가
  덮는 청크 수와 매니페스트 `chunked_at_max` 를 BM25 캐시 키와 **같은 질의**(`count(*)`·`max(chunked_at)`)와
  대조하고, 어긋나면 한 줄을 찍고 계속한다 — 멈추면 옛 코퍼스를 일부러 검색하는 정상 용법까지 막힌다.
  `eval` 은 같은 줄을 CSV `note` 열과 stdout 요약에 싣는다(어느 코퍼스 위의 점수인지). `chunked_at_max` 는
  **필수 키가 아니다** — 없으면 개수만 대조하고 그 사실을 경고한다(거부하면 그 키 이전에 구운 저장소로 도는
  검색이 통째로 멈춘다). 어긋남을 고치는 것은 `embed` 전량 재인코딩이다.
- **평가 행은 저장소 판본을 어긋나지 않아도 싣는다** (포크 #49). `eval` 의 vector·hybrid 는 매니페스트의
  `model`·`revision`·벡터 수·`chunked_at_max` 를 CSV `store` 열과 stdout 요약 한 줄에 싣는다 — 바로 위
  커버리지 경고와 **축이 다르다**: 그쪽은 어긋날 때만 말하므로 정상일 때는 판본이 아무 데도 안 남고, 그
  자리가 ydc 에서 "1차 → 2차" 로 라벨한 델타가 실은 "식약처 벡터 없음 → 2차" 였던 사고다(`v0.3.0` 은
  산출 파일명에 판본을 붙여 고쳤다 — 우리는 파일이 아니라 행으로 내므로 같은 자리가 행이다).
  **판본 없는 행은 나올 수 없다**: 저장소를 못 열면 그 실행이 통째로 blocked(2)이고, `model` 이 빈
  저장소는 `load` 가 거절한다(위 blocked 항목). bm25 행은 비어 있다 — 저장소를 열지 않으니 지어낼 판본이
  없다. 이 열은 종료 코드를 바꾸지 않는다.
- **벡터는 파일이다** — `var/retrieval/vectors/e5base.{npy,ids.csv,manifest.json}`. pgvector 는 #28 단계 4b 로 미뤘다.
  BM25 색인도 `var/retrieval/bm25/index-<sha16>.pkl` 로 캐시한다(키 = 청크 수 + 최신 `chunked_at` + Kiwi
  사전 두 벌의 해시 + **활성 주제 사전의 버전과 내용 지문**). 주제 사전이 파일이 아니게 된 뒤로 파일 해시만
  거는 키는 주제 변경을 놓친다 — 버전 번호만으로도 모자란다(켜져 있는 버전에 행을 더할 수 있다).
  둘 다 `var/` 라 레포에 들어가지 않고, 지워도 다시 만들어진다.
- **`embed` 는 크론이 아니라 사람이 GPU 호스트에서 돌린다.** 그래서 `sentence-transformers`·`torch` 는 `embed`
  extra 에만 있고 `stack/Dockerfile` 에도 `tool/checks/test` 에도 들어가지 않는다 — 테스트는 이미지가 싣는
  집합에서 돌아야 한다. 실행은 `uv run --extra retrieval --extra embed cosmai retrieval embed …` 로 한다.
  `uv sync --extra embed` 로 깔아 두면 다음 `tool/checks/test` 가 지운다(그게 맞는 동작이다).
- `analyze all` 과 같은 이유로 크론 간격 규칙에서 제외된다 — 외부 fetch 가 없는 DB·파일 전용 작업이다.

## 분기 시계열 (포크 #5, ydc `trend.py` 승격)
```
cosmai trend quarter [--url <url>]
```
- 활성 코퍼스 스냅샷(`corpus_snapshot.active`)과 활성 패널 명부(`panel_channel` 의 활성 판본)를 읽어
  `needs.metrics_topic_quarter` 를 쓴다. **스냅샷도 명부도 인자가 아니다** — 고르는 길이 둘이면 분모도
  둘이 되고, 활성 판본을 고르는 자리는 `db/corpus.active_snapshot` 과 `db/seed/panel.active_version`
  하나씩이다(후자는 활성 판본이 둘이면 답 대신 멈춘다).
- 모집단은 매니페스트 규칙 그대로다: `content_type='video_long'` · `panel_role='product'` ·
  `topic_id='선크림'` 언급이 있는 영상, 그리고 그 영상들에 달린 댓글. 산출 행의 `scope` 는
  `metrics_need.scope` 와 같은 어휘(`선블록`)이고 `content_type` 은 `long_form` 이다.
- **한 실행이 그 (run, scope, 명부) 의 행을 통째로 다시 쓴다.** 부분 갱신이 아닌 것이 격자를 조밀하게
  지키는 방법이다 — 재실행은 같은 `run_id`(note 로 찾는다)에 같은 행을 낸다.
- 쓰고 나서 `needs.metrics_topic_quarter_violation` 에 그 run 을 되묻는다. 뷰가 무엇이든 말하면
  종료 코드 **1**(partial)이고 stdout 이 그 줄을 싣는다 — 표는 섰지만 그 표의 뜻이 계약과 다르다.
- 종료 코드: 0 ok · 1 partial(위 불변식 위반) · 2 blocked(연결 거절, **활성 명부 없음**·**활성 스냅샷
  없음**, 모집단이 비어 산출할 행이 없음 — 셋 다 `db/seed --only panel`·`db/corpus load` 를 아직 안
  돌렸다는 뜻이라 실패가 아니라 막힘이다).
- `analysis_run.versions.metric` 이 그 행들의 정의 판본을 든다 (`versioning.md`).

## 판정 (포크 #40, ydc `judge.py` 승격)
```
cosmai trend judge [--url <url>]
```
- `cosmai trend quarter` 가 낸 **그 run 의** `needs.metrics_topic_quarter` 행을 읽어
  `needs.topic_quarter_judgement` 를 쓴다. run 은 `quarter` 와 **같은 길**로 찾는다(활성 스냅샷·활성
  명부에서 만든 note) — 인자가 없는 이유도 같다. 지표 행이 없으면 판정할 것이 없다.
- **지표를 다시 계산하지 않는다.** 판정 기준(`TAU`·가중치·유형 이름)은 팀 합의로 바뀌고, 그때 지표를
  다시 세지 않아도 되도록 두 단계를 갈라 둔 것이 ydc 의 설계이고 이 명령이 그것을 그대로 받는다.
- 한 실행이 그 (run, scope, 명부) 의 판정 행을 통째로 다시 쓴다 — 부분 갱신이 아닌 것이 지표 행과의
  1:1 을 지키는 방법이다.
- 쓰고 나서 `needs.topic_quarter_judgement_violation` 에 그 run 을 되묻는다. 뷰가 무엇이든 말하면 종료
  코드 **1**(partial)이고 stdout 이 그 줄을 싣는다.
- 종료 코드: 0 ok · 1 partial(위 불변식 위반) · 2 blocked(연결 거절, 활성 명부·스냅샷 없음, **그 run 에
  지표 행이 없음** — `cosmai trend quarter` 를 아직 안 돌렸다는 뜻이라 실패가 아니라 막힘이다).
- `analysis_run.versions.judgement` 가 그 행들의 정의 판본을 든다 (`versioning.md`).

## 민감도·후향 검증 (포크 #41, ydc `panel_sensitivity.py`·`backtest.py`·`spam_ad_flags.py` 승격)
```
cosmai trend sensitivity [--url <url>]
```
- `cosmai trend quarter` 가 낸 **그 run 의** 결론이 세 선택에 흔들리는지 묻는다: 패널 구성(product 만 대 43채널
  전부) · 컷오프(과거 분기까지만 알던 것처럼 다시 셈) · 광고·협찬 표시(빼고 다시 셈). run 은 `quarter`·`judge`
  와 **같은 길**로 찾는다(활성 스냅샷·활성 명부에서 만든 note) — 인자가 없는 이유도 같다.
- **아무것도 쓰지 않는다.** 세 측정이 만드는 행은 반사실 모집단의 것이고 022 의 `panel_role` 어휘에도
  `analysis_run` 에도 자리가 없다(`interfaces.md` §민감도). 답은 표가 아니라 stdout 이고, 읽기 전용이라 운영 DB 에
  그대로 돌린다. 저장된 표가 그대로인 것은 `tests/test_sensitivity_pipeline.py` 가 지문으로 붙든다.
- 기저는 다시 세고, 그 기저가 저장된 `metrics_topic_quarter` 행과 다르면 그 사실(`baseline_drift`)이 먼저 나온다 —
  그때 이 명령의 모든 차이는 뜻이 없다.
- 종료 코드: **0 ok — 답이 계산됐다** · 1 partial(**이 산출을 믿지 마라** — `baseline_drift`, 또는 방향성 판정
  사례가 둘 미만이라 후향 검증이라 부를 것이 없다(`thin_backtest`)) · 2 blocked(연결 거절, 활성 명부·스냅샷·주제
  사전 없음, **그 run 에 지표 행이 없음** — `cosmai trend quarter` 를 아직 안 돌렸다는 뜻이라 실패가 아니라
  막힘이다. 코퍼스가 비었는데 지표 행만 남아 창이 설 분기가 없는 것(`ShortHistory`)도 같은 자리다).
- **"결론이 흔들린다"는 1 이 아니다.** 그것은 이 명령이 답하려고 존재하는 **발견**이지 실행의 실패가 아니고,
  이 파일 맨 위의 공통 규약(`0 ok · 1 partial(일부 실패·절단) · 2 blocked`)에서 1 은 "산출이 온전하지 않다"는
  뜻이다. 흔들림은 종료 코드가 아니라 `note` 의 `panel_flips=`·`ad_flips=` 와 세 표가 싣는다 — 전량에서 흔들림은
  평상 상태라(광고·협찬을 빼면 19셀에서 유형이 바뀐다) 1 로 내면 `set -e` 셸·make·CI 한 줄이 정상 실행을 실패로
  읽는다. 출처인 ydc 도 같은 자리다: `panel_sensitivity.py`·`spam_ad_flags.py` 는 언제나 0 이고 `backtest.py` 만
  사례 2건 미만에 1 을 쓴다.
- 크론에 걸어도 안전하다(읽기 전용 · 0 이 평상 상태). 다만 답이 바뀌는 것은 코퍼스나 명부가 바뀔 때라, 지금은
  사람이 한 번 물어 이슈에 남긴다.
- **아래 §근거·카드 의 `cards` 도 같은 자리다** — "규칙에 걸린 셀이 없다"는 발견이지 실패가 아니다.

## 근거·카드 (포크 #6, ydc `evidence_comments.py`·`cards.py` 승격)
```
cosmai trend evidence [--url <url>]
cosmai trend cards --quarter <q> [--url <url>]
```
- `evidence` 는 `cosmai trend judge` 가 판정한 **그 run 의** 셀에 붙는 근거 댓글을
  `needs.topic_quarter_evidence` 에 쓴다. run 을 찾는 길은 `quarter`·`judge` 와 같은 note 하나이고,
  그래서 인자도 `--url` 하나다.
- **모집단은 지표를 세운 그 술어다** — `analysis/trend/pipeline.py` 의 `POPULATION` CTE 를 그대로 든다.
  근거만 다른 모집단에서 고르면 카드의 발화와 카드의 숫자가 다른 분모 위에 선다.
- **후보를 읽자마자 커밋하고 그 뒤로는 DB 를 보지 않는다.** 근거는 판정과 달리 코퍼스를 훑는 단계라
  `needs_runtime` 의 `idle_in_transaction_session_timeout`(15초)에 그대로 걸린다 — 커서를 연 채 접으면
  끊긴다(`analysis/trend/pipeline.py` 와 같은 자리). 읽어 오는 것은 본문이 아니라 포인터와 좋아요뿐이고,
  전량에서 후보 15,602행 · 0.52s · 73MB 로 실제로 재 봤다 (`interfaces.md` §근거 "전량 실측").
- 한 실행이 그 (run, scope, 명부) 의 근거 행을 통째로 다시 쓴다 — 부분 갱신이면 자리(rank)의 사다리가
  조용히 구멍 난다.
- 쓰고 나서 `needs.topic_quarter_evidence_violation` 에 그 run 을 되묻는다. 뷰가 무엇이든 말하면 종료
  코드 **1**(partial)이고 stdout 이 그 줄을 싣는다.
- 종료 코드: 0 ok · 1 partial(위 불변식 위반) · 2 blocked(연결 거절, 활성 명부·스냅샷 없음, **그 run 에
  판정 행이 없음** — `cosmai trend judge` 를 아직 안 돌렸다는 뜻이라 실패가 아니라 막힘이다).
- `cards` 는 **아무것도 쓰지 않는다.** 위 세 표를 읽어 마크다운 카드 묶음을 stdout 으로 낸다. 파일로
  떨구지 않는 것은 `retrieval terms` 와 같은 이유다(자라는 코퍼스의 스냅숏이라 레포에 두면 낡는다) —
  남기려면 리다이렉트한다. `--quarter` 는 필수다: 카드는 "이번 분기에 이 주제를 더 볼지"를 담당자가
  정하는 단위라 분기가 없으면 물음이 서지 않는다.
- `cards` 의 종료 코드: **0 ok — 카드가 계산됐다(0장이어도 그렇다)** · 1 partial(**규칙에 걸렸는데 근거
  원문이 없어 카드로 서지 못한 셀이 있다** — 그것만이 잘린 산출이다) · 2 blocked(연결 거절, 그 run 에 판정
  행이 없음, 그 분기가 이 run 의 격자에 없음 — 뒤의 둘은 메시지가 갈라 말한다).
- **"규칙에 걸린 셀이 없다"는 1 이 아니다.** 그것은 규칙이 다 돌고 나온 정상적으로 계산된 답이고, 이 파일
  맨 위의 공통 규약에서 1 은 "산출이 온전하지 않다"는 뜻이다 — 바로 위 §민감도 의 "흔들린다는 1 이 아니다"와
  **같은 자리, 같은 문장**이다. 실측으로도 그렇다: 표본 골든 11분기 중 **8분기가 0장**이라 1 로 내면 평상
  상태의 73%가 실패로 읽히고, `cards` 는 사람이 한 번 치는 탐색 명령이 아니라 `quarter → judge → evidence
  → cards` 의 마지막 칸이라 `set -e` 셸·make·크론이 그 줄에서 멈춘다(upstream #55 의 착수 조건이 "S6 자동
  소비자"다). 몇 장인지는 종료 코드가 아니라 stderr 의 `note` 가 싣는다.
- **stdout 은 마크다운 산출물뿐이다.** `note` 와 잘린 셀 줄은 stderr 로 나간다 — 리다이렉트한 `.md` 안에
  `trend cards run=…` 이 남지 않아야 그 파일이 그대로 문서다.
- `analysis_run.versions.evidence` 가 근거 행들의 정의 판본을 든다 (`versioning.md`). 카드는 행을 만들지
  않으므로 판본을 남기지 않는다 — 그 카드가 어느 정의의 근거를 실었는지는 읽은 run 의 이 키가 답한다.

## 대조 (포크 #7, ydc `source_composition.py`·`commerce_crosscheck.py`·`cross_source.py` 승격)
```
cosmai trend crosscheck [--url <url>]
```
- 네 소스를 나란히 놓고 어긋나는 자리를 찾는다: 구성(같은 사전으로 소스마다 주제 구성비) · 평가(커머스
  플랫폼의 속성 평가 대 그 run 의 판정) · 성분(성분 담론 셋과 성분 키 감사). 합산하지 않는다 —
  분모가 소스마다 다르다(`interfaces.md` §대조).
- **아무것도 쓰지 않는다.** 세 답의 행은 (주제) 또는 (성분) 하나가 키인데 022 의 분기 입자는 여덟 칸이
  키이고, 커머스 쪽에는 그중 분기도 명부도 없다. 답은 표가 아니라 stdout 이고, 읽기 전용이라 운영 DB 에
  그대로 돌린다. 저장된 표가 그대로인 것은 `tests/test_crosscheck_pipeline.py` 가 지문으로 붙든다.
- run 은 `quarter`·`judge`·`sensitivity` 와 **같은 길**로 찾는다(활성 스냅샷·활성 명부에서 만든 note) —
  인자가 `--url` 하나인 이유도 같다. 대조하는 분기는 그 run 격자의 **마지막에서 두 번째**다(마지막은
  판정이 `미확정(진행 중)` 으로 두는 진행 중 분기라 과소 집계된다).
- 청크 색인을 한 번 훑는다 — 전량 381,950청크 48MB **11.3초**로 실제로 재 봤다(2026-08-27, 키셋 2만 행
  페이지에 페이지마다 커밋). 한 흐름으로 훑으면 `needs_runtime` 의 `transaction_timeout`(60초)에 걸리므로
  `analysis/retrieval/eval.py` 의 `gold_from_chunks` 와 같은 방식을 쓴다.
- 종료 코드: **0 ok — 대조표가 계산됐다** · 1 partial(**이 산출을 믿지 마라** — 성분 키가 사람이 한 번
  읽어 금지한 성분명을 잡았거나(`key_mismatch`, §대조 의 `시카` 사고가 이 자리다), 커머스
  `topic_group` 이 가리키는 우리 주제가 활성 사전에 없다(`group_map_drift`)) · 2 blocked(연결 거절, 활성
  명부·스냅샷·주제 사전 없음, **그 스냅샷·명부에 지표 run 이 없음**(`cosmai trend quarter` 를 아직 안
  돌렸다), **그 run 에 판정 행이 없음** — `cosmai trend judge` 를 아직 안 돌렸다는 뜻이라 실패가 아니라
  막힘이다. 청크가 비었거나(`cosmai retrieval chunk`) 랭킹에 선케어 제품이 없는 것
  (`cosmai collect commerce`)도 같은 자리다 — 대조할 소스가 아직 없다). **여덟 갈래 전부** 코드의
  `NoPopulation`·`NoCrosscheck`·`NoDictionary` 셋 중 하나이고, 메시지가 어느 것인지 갈라 말한다.
- **"소스가 어긋난다"는 1 이 아니다.** 그것은 이 명령이 답하려고 존재하는 **발견**이지 실행의 실패가
  아니고, 이 파일 맨 위의 공통 규약에서 1 은 "산출이 온전하지 않다"는 뜻이다 — 위 §민감도 의 "흔들린다는
  1 이 아니다", §근거·카드 의 "규칙에 걸린 셀이 없다는 1 이 아니다"와 **같은 자리, 같은 문장**이다. 실측
  으로도 그렇다: 전량에서 13주제 중 어긋남 해석이 붙는 주제가 여럿이라(예: `백탁` 커머스 9.80% 대 댓글
  1.55%) 1 로 내면 평상 상태가 실패로 읽힌다. 어긋남은 종료 코드가 아니라 표의 `reading` 열과 `note` 가
  싣는다.
- **근거가 얇은 것도 1 이 아니다.** 속성 평가 제품이 `MIN_PRODUCTS`(5) 미만인 주제는 해석을 쓰지 않고
  `note` 의 `thin=` 가 센다. 얇다는 것은 계산된 답이지 잘린 산출이 아니다.
- 크론에 걸어도 안전하다(읽기 전용 · 0 이 평상 상태). 다만 답이 바뀌는 것은 코퍼스나 수집이 바뀔 때라,
  지금은 사람이 한 번 물어 이슈에 남긴다.

## 홀드아웃 (포크 #51, ydc `holdout_commerce.py` 승격)
```
cosmai trend holdout [--url <url>]
```
- **새로 쌓인 커머스 리뷰로 기존 결론을 되묻는다 — 숫자를 갈아치우지 않는다.** 같은 모집단(§대조 의
  선케어 랭킹 술어)의 리뷰를 두 팔로 가르고 같은 코드로 센다: 청크 색인에 있는 리뷰(`seen`, 우리가 본
  것)와 없는 리뷰(`holdout`, **한 번도 안 본 것**). 갈리면 왜 갈리는지를 창·플랫폼 구성·제품 바스켓
  셋으로 갈라 잰다(`interfaces.md` §홀드아웃).
- **인자가 `--url` 하나인 이유**: 컷오프가 날짜가 아니라 `needs.retrieval_chunk` 의 커머스 `doc_id`
  명부다 — 고르는 길이 둘이면 분모도 둘이 된다(`quarter`·`judge`·`crosscheck` 와 같은 규약). ydc 는
  `--cutoff` 를 받았지만 그 레포에는 "무엇을 봤는가" 가 행으로 없었다.
- **아무것도 쓰지 않는다.** 이 답의 행은 (팔, 주제)가 키인데 `팔` 의 경계가 청크 색인이라 `cosmai
  retrieval chunk` 가 돌 때마다 움직인다(오늘의 홀드아웃이 내일의 기존이다). 답은 표가 아니라 stdout
  이고, 읽기 전용이라 운영 DB 에 그대로 돌린다. 저장된 표가 그대로인 것은
  `tests/test_holdout_pipeline.py` 가 지문으로 붙든다.
- **네 읽기(커머스 청크 명부 · 리뷰 키 명부 · 빈 본문 수 · 모집단)는 한 트랜잭션 스냅샷
  (`REPEATABLE READ`) 안에서 한다.** 밖에 두면 수집기가 도는 동안 그 넷이 서로 다른 모집단을 가리켜
  `seen + holdout + empty` 가 어떤 모집단의 크기도 아니게 된다. ydc 가 손으로 얹은 정지·전순서
  정렬·행수 대조 셋이 여기서 각각 어디로 가는지는 `interfaces.md` §홀드아웃 의 표가 든다 — 그 셋을
  그대로 옮기면 이 자리에서는 항등식이라 검사가 아니다.
- 종료 코드: **0 ok — 답이 계산됐다(재현되지 않아도 그렇다)** · 1 partial(**이 산출을 믿지 마라** —
  원천 리뷰가 없는 커머스 청크가 있다(`chunk_orphan`). 청크에는 외래키가 없으므로(020) 그때 기존 팔은
  분석이 실제로 본 그 팔이 아니다) · 2 blocked(연결 거절, 활성 주제 사전 없음, 랭킹에 선케어 제품이
  없음(`cosmai collect commerce`), 커머스 청크가 하나도 없음 — **기준 시점 자체가 없다는 뜻이라**
  `cosmai retrieval chunk` 를 돌려라, 선케어 모집단 안에 청크가 있는 리뷰가 하나도 없음 — 비교할 기존
  팔이 없다, **안 본 리뷰가 하나도 없음** — 되물을 새 표본이 아직 없다). **여섯 갈래 전부** 코드의
  `NoHoldout`·`NoDictionary` 둘 중 하나이고, 메시지가 어느 것인지 갈라 말한다.
- **"재현되지 않는다"는 1 이 아니다.** 그것은 이 명령이 답하려고 존재하는 **발견**이고, 이 파일 맨 위의
  공통 규약에서 1 은 "산출이 온전하지 않다"는 뜻이다 — 위 §민감도 의 "흔들린다는 1 이 아니다", §대조 의
  "어긋난다는 1 이 아니다"와 **같은 자리, 같은 문장**이다. 출처인 ydc 의 `report` 도 언제나 0 을 낸다.
  어느 갈래인지(`재현`·`순위 재현`·`순위 변동`·`순위 없음`)는 `note` 와 표가 싣는다.
- **표본이 얇은 것도 1 이 아니다.** 기존 팔의 문서 수가 `MIN_MENTIONS`(5) 미만인 주제는 순위를 갖지
  않고(`-`) `note` 의 `ranked=` 가 몇 주제가 순위를 갖는지 센다.
- 크론에 걸어도 안전하다(읽기 전용 · 0 이 평상 상태). 다만 답이 바뀌는 것은 수집과 청킹이 도는 때라,
  지금은 사람이 한 번 물어 이슈에 남긴다. **`cosmai retrieval chunk` 를 돌리면 홀드아웃이 기존으로
  넘어가 이 명령의 답이 리셋된다** — 되묻기 전에 청킹부터 돌리면 물음 자체가 사라진다.

## 스케줄 (stack/crontab.d/, UTC)
commerce 줄의 규칙은 "분 0 회피"가 아니라 **인접한 두 줄의 간격이 앞 줄의 소요보다 넓다**이다. 그 소요는
여기 숫자로 적지 않는다 — 코드에서 나온다. 그 dataset(그리고 `--board`)을 선언한 소스들을 `engine.collect`가
**소스마다 레인 하나씩 동시에** 돌고(#25), 한 소스는 `SourcePolicy.min_interval_s` × (요청 수 − `burst`)만큼
걸린다. 그래서 한 줄의 소요는 소스들의 **합이 아니라 가장 느린 소스**의 것이다. 레인 수에는 상한이 있고
(`collectors/commerce/storage/db.py` 의 `MAX_CONCURRENT_LANES`) 그것은 취향이 아니라 커넥션 예산이다 —
레인마다 소스 락 커넥션 하나를 걷는 내내 쥐므로, 소스가 레인보다 많은 줄은 "전체 작업량 ÷ 레인 수"라는
두 번째 하한을 하나 더 갖는다. 요청 수의 기준이 둘이라
소요도 둘이다: `seeds()` 길이만 도는 **씨드 기준**과 `max_requests_per_run`까지 차는 **예산 기준**. 예산 기준으로는
매시 ranking이 가장 느린 소스(daisomall) 하나만으로도 02:10 product·04:15 review의 시작 시각을 넘겨 점유한다 — 크론을 옮겨
풀 겹침이 아니라 소스별 어드바이저리 락(#10 §A-8-1, `collectors/commerce/storage/locks.py`)이 닫는 겹침이고,
그 락은 이미 운영 진입점에 무조건 배선돼 있다(`collectors/commerce/cli.py`,
`tests/collectors/commerce/test_source_lock.py`가 그 자리를 붙든다). 간격 산술은 락을 볼 수 없으므로
`tests/collectors/commerce/test_every_dataset_is_collected_and_scheduled.py`는 씨드 기준만 항상 검사하고,
그 두 쌍의 예산 기준은 **영구히** xfail(strict)로 남는다 — 그 strict 가 잡는 것은 락의 착륙이 아니라
예산이 줄어 겹침 자체가 사라지는 날이다.

**둘 다 상한이 아니라 하한이다.** 위 계산은 정책이 *선언한* 페이스를 쓰는데, `Gate._back_off`는 사이트가
403·429·503으로 답하면 살아 있는 인터벌을 `Gate.MAX_INTERVAL_S`(300초)까지 벌린다 — daisomall의 30초가
300초가 된다. 응답 지연과 재시도도 값에 없고, `max_requests_per_run`이 없는 소스는 예산 기준에서도
씨드 수로만 계산된다(#10 이후 네 소스 모두 선언하므로 오늘 그런 소스는 없다). 레인 산술도 마찬가지로
낙관적이다: 실제 실행은 레인을 등록 순서대로 나눠 주지 긴 소스부터 주지 않으므로, 소스가 레인보다 많은
줄은 위 두 하한 중 어느 쪽보다도 오래 걸릴 수 있다. 그러니 이 숫자는 "적어도 이만큼"이지 "많아야 이만큼"이 아니다. 겹치지 않는다는 보장은
간격이 아니라 락이 준다. `analyze all`은 외부 fetch가 없는 DB 전용 작업이라 매시 실행과 겹쳐도
무해하므로 이 규칙에서 제외된다. 다만 `analyze all` 에도 간격 규칙이 하나 있고 그것은 락이 세운다:
같은 락을 쓰는 주인의 극성 패스와 겹치면 뒤에 온 쪽이 그 밤을 통째로 건너뛰므로, 그 줄과 `0 5` 사이의
간격은 그 패스의 최악 소요 T 보다 넓어야 한다. **그 줄은 이제 있다**(`0 8`, #32) — 명령은 증분
(`--missing`, #98)이고 **그 T 는 아직 미측정이다**: 운영 실행이 조정자의 몫이라 줄을 넣은 작업이 재지
못했다. 그래서 시각은 T 를 아는 대신 간격을 최대로 벌려 골랐다 — `0 8` 은 `0 5` 에 3h 를 주고 자기는
21h 를 받는다. 실측이 있는 유일한 값은 전량 패스의 것이고(선블록 하나를 도는 run 16 이 **6h44m**),
21h 는 그 3배다. 조정자가 첫 밤과 정상 밤의 T 를 재고, 21h 를 위협하면 이 두 시각을 옮긴다. 계산과
GPU 창(08:00–16:00 UTC, `retrieval embed` 가 피한다)은 `stack/crontab.d/analyze` 에 적혀 있다.

youtube 의 `work` 는 2026-08-24 에 이 표에 더해졌다(그전에는 셋만 있었고 큐를 비우는 줄이
없었다). 상주 데몬이 아니라 크론인 이유는 `collectors/youtube/cli.py:_run_work` 가 한 번에
`DEFAULT_WORK_BATCH` 만큼만 claim 하고 끝나는 배치이기 때문이다 — 반복은 바깥이 준다. 겹쳐 떠도
안전하다: `_claim` 이 `FOR UPDATE SKIP LOCKED` 한 문장으로 집는다.
```
0 * * * *   cosmai collect commerce --dataset ranking
10 2 * * *  cosmai collect commerce --dataset product
30 3 * * *  cosmai collect commerce --dataset review_low --board suncare   (보드는 scope.json 목록으로 확장)
15 4 * * *  cosmai collect commerce --dataset review
45 4 * * *  cosmai collect commerce --dataset review_stats
30 5 * * *  cosmai collect commerce --dataset new_product
0 5 * * *   cosmai analyze all
0 8 * * *   cosmai analyze polarity --impl ollama:gemma4:latest --missing
youtube: watch 1h · work 5m · flatten 15m · prune 1d  (팬아웃 상한 적용 후)
naver:   datalab 월 1회 (키워드 사전 기준) · blog 월 1회
```
