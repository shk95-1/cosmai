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
같은 락을 쓰는 주인의 극성 패스와 겹치면 뒤에 온 쪽이 그 밤을 통째로 건너뛰므로, 그 패스에 크론 줄이
생기는 날 그 줄과 `0 5` 사이의 간격은 패스의 최악 소요보다 넓어야 한다. **전량 패스의 실측은 하나 있다**:
선블록 하나를 도는 run 16 이 6h44m 만에 끝났다 — 그러니 `0 2`(간격 3h)도 `0 0`(5h)도 실측으로 탈락한다.
**줄은 상한을 잰 뒤에 넣는다**, 그것도
#32 의 `--since` 증분이 붙은 뒤 크론이 실제로 돌릴 명령으로 잰 값으로(전량 패스의 소요와 증분 패스의
소요는 다른 수다). 계산은 `stack/crontab.d/analyze` 에 적혀 있다.

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
youtube: watch 1h · work 5m · flatten 15m · prune 1d  (팬아웃 상한 적용 후)
naver:   datalab 월 1회 (키워드 사전 기준) · blog 월 1회
```
