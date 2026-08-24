# 엔트리 규약

## 수집기
```
cosmai collect <collector> --dataset <dataset> [--board <board>] [--since <date>]
  collector ∈ {commerce, youtube, naver}
  commerce datasets: ranking | product | review | review_stats | new_product | review_low
  youtube  datasets: watch | work | flatten | prune  (기존 tubedepth 명령 의미 유지)
  naver    datasets: datalab | blog   (source 행 모델 없음 -- cosmai-old 계승 안 함, 원천은 needs.naver_* (004))
종료 코드: 0 ok · 1 partial(일부 실패·절단) · 2 blocked(차단/거부)   ← trend-radar 관찰 규약 그대로
```
- 수집기는 **자기 스키마의 테이블에만** 쓴다 (`ddl/current`). 다른 스키마 읽기는 reader 롤로만.
- 표본 설계는 상수로 세고 `collectors/<c>/scope.json` 에 기록한다 (scope.lock 의 변형: 파일 하나, CHANGELOG 의무 없음, 테스트는 상수=파일 일치만 검사).

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

## 공통 운영 뷰 (각 수집기가 제공해야 하는 최소 형태)
```sql
-- db/views/collector_health.sql 이 commerce(trend_radar.run+fetch_log)와
-- naver(needs.naver_run+naver_fetch_log) 두 팔을 UNION 한다 -- youtube 는 아래 이유로 빠져 있다
collector text, dataset text, run_id text, started_at timestamptz, finished_at timestamptz,
status text,          -- ok | partial | blocked | failed | running
requests int, ok int, blocked int, failed int, queued int, p90_ms int
```
P16 의 표가 이 뷰 하나로 나와야 한다. `requests` 는 fetch 시도 전부이고 `ok`·`blocked`·`failed` 는
2xx / 403·429 / (error 또는 5xx) 세 통뿐이다 — 셋의 합과 `requests` 의 차이가 어느 통에도 안 들어간
응답(예: 404)이다.

**youtube 는 3단계에서 이 뷰에 들어가지 않는다** (사용자 결정 2026-08-24). 12컬럼 중 다섯을 낼 원천이
없고, 그중 `blocked`·`p90_ms` 가 하필 그 수집기의 실제 고장 모드(쿼터 소진·지연)를 보는 컬럼이라
NULL 로 채우면 표는 뜨는데 볼 것이 안 보인다. 근거 넷:
- `tubedepth.jobs` 에는 run 개념이 없다 — `run_id` 를 낼 것이 없고 `created_at` 은 enqueue 시각이라
  `started_at` 도 아니다.
- 같은 표에 지연을 잰 컬럼이 없다 — `p90_ms` 의 원천이 아예 없다.
- `collectors/youtube/cli.py:211` 이 `error_code` 에 예외 클래스명(`type(error).__name__`)만 넣는다 —
  429·쿼터 소진을 `failed` 와 갈라 `blocked` 로 셀 방법이 없다.
- `jobs.kind`(`video.metadata` 계열)가 위 §수집의 youtube dataset 어휘(`watch|work|flatten|prune`)와
  다르다 — `dataset` 컬럼에 그대로 넣으면 다른 두 팔과 다른 어휘가 한 컬럼에 섞인다.

`queued` 가 두 팔 다 NULL 인 것은 그래서다: commerce·naver 는 크론이 부르는 배치 워커라 큐가 없고,
큐를 가진 유일한 수집기가 youtube 다. 컬럼을 지우지 않고 남겨 둔 것은 그 팔이 돌아올 자리이기 때문이다.

분석판은 `db/views/analysis_health.sql` 의 `needs.analysis_health` 다: run 별 started/finished/
status/versions 와 그 run 의 `metrics_need`·`metrics_wish` 행 수. `need_mention`·`wish_mention` 은
run_id 를 갖지 않으므로(versioning.md A19) 각 단계가 만든 행 수는 `analysis_run.note` 가 이름=값으로
나른다. `db/migrate.sh` 가 배포마다 다시 적용한다 (CREATE OR REPLACE).

## 분석
```
cosmai analyze <stage> [--since <date>] [--scope <category>]
  stage ∈ {link, polarity, aggregate, all}
cosmai eval <task>        task ∈ {polarity, wish_class, brand_link, product_match}
cosmai lexicon {load, diff, activate} --kind <kind> --version <n>
```
- T14: `extract` 는 단독 stage 가 아니다 — 후보만 만들고 아무 행도 쓰지 않아 멱등을 관측할 수 없다. 추출은 `polarity` 안에서 돈다(`Extractor` 프로토콜은 그대로).
- B11: `eval aspect` 는 평가셋도 기준선도 0행이라 뺐다. 되살리려면 평가셋 + `interfaces.md` 기준선 표의 행이 같은 PR 에 온다.
- 모든 단계는 **자연키 upsert** 로 멱등. 재실행은 같은 결과를 만든다.
- 산출 행은 반드시 `*_version` 을 가진다 (`versioning.md`).
- `analyze all` 은 `needs.analysis_run` 행 하나를 만들고(polarity 가 열고 aggregate 가 그 `run_id` 로
  metrics 를 쓴다) `versions` 에 linker·extractor·polarity·aggregate 와 `lexicon`(활성 버전 + ruleset)을
  기록한다. 한 단계라도 실패하면 그 run 은 `status='failed'` + note 로 닫히고 종료 코드는 1 이다.
- `analyze all` 의 aggregate 모집단은 그 run 이 방금 쓴 `extractor_version` 하나다 — 시드(`slice-*`)를
  같은 scope 에 섞으면 한 문장이 두 번 세어진다. 고른 모집단은 `versions.extractor` 에 남는다.

## 스케줄 (stack/crontab, UTC)
외부를 fetch하는 commerce 줄은 분 0을 쓰지 않는다 (매시 ranking이 분 0에 시작하고 약 74초 걸린다).
각 일별 걷기는 약 7분이므로 서로 최소 그만큼 떨어뜨린다. `analyze all`은 외부 fetch가 없는 DB 전용
작업이라 매시 실행과 겹쳐도 무해하므로 이 규칙에서 제외된다.
```
0 * * * *   cosmai collect commerce --dataset ranking
5 2 * * *   cosmai collect commerce --dataset product
30 3 * * *  cosmai collect commerce --dataset review_low --board suncare   (보드는 scope.json 목록으로 확장)
15 4 * * *  cosmai collect commerce --dataset review
45 4 * * *  cosmai collect commerce --dataset review_stats
30 5 * * *  cosmai collect commerce --dataset new_product
0 5 * * *   cosmai analyze all
youtube: watch 1h · flatten 15m · prune 1d  (팬아웃 상한 적용 후)
naver:   datalab 월 1회 (키워드 사전 기준)
```
