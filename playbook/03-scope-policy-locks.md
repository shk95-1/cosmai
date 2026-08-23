# 03 — 범위·정책 잠금

"숫자 하나가 조용히 바뀌면 시계열의 뜻이 바뀐다"는 trend-radar의 핵심 관찰이다(`tests/test_collection_scope_is_recorded.py:3-8`:
page size 20→50, 1페이지→2페이지로 시간당 리뷰 400→1,000, 6주 뒤 `git log`만이 답할 수 있었다). 이 섹션의 잠금들은 전부 그 한 사고의
파생이며, 효과는 분명하나 비용도 이 섹션에 몰려 있다.

| ID | 이름 | 등급 |
|---|---|---|
| S01 | `scope.lock.json` + CHANGELOG `Unreleased` 결합 + 기준 ref 비교 | 변형 |
| S02 | scope는 상수에서 파생, 모든 run 행에 저장 | 채택 |
| S03 | `service-db.json` 매니페스트 + 연결 예산 산수 테스트 + `policy.py` 렌더/감사 | 변형 |
| S04 | `SourcePolicy`가 유일한 요청 제어 계약, 상수 옆에 실측 이유 | 채택 |
| S05 | enum 멤버마다 수집기가 있다 ("기능처럼 보이는 구멍") | 채택 |
| S06 | 페이지 걷기는 정책 깊이 안에, 조용한 절단은 보고서·exit code에 | 채택 |
| S07 | 정직한 User-Agent 테스트 | 채택 |
| S08 | 예산 추적기 — 헤더 의미를 정책이 선언 (`budget_is_daily`) | 채택 |

---

## S01. `scope.lock.json` + CHANGELOG 결합 + 기준 ref 비교

- **어디서**: trend-radar `scope.lock.json:2-14`(파일 자체 설명: dataset 키, `changed_in`), `tests/test_collection_scope_is_recorded.py:70-107`
  (레지스트리↔lock 동일, `changed_in`이 릴리스 헤딩 또는 `Unreleased`, Unreleased면 CHANGELOG Unreleased 절에 소스명), `:110-172`
  (**커밋된 lock과 비교** — HEAD 또는 CI의 `SCOPE_LOCK_BASE_REF`; 양쪽을 같이 고쳐도 통과 못 하게), `.github/workflows/checks.yml:101-133`(기준 ref 계산).
  `AGENTS.md:98-109`.
- **관찰된 효과**: 오늘 b9ffa95(`review_low`)가 lock 없이 통과했을 리 없다 — 새 데이터셋은 lock에 자기 이름으로 scope를 기록해야 하고
  (`AGENTS.md:101-103`), 커밋 본문이 "scope records it under its own name so the two walks' row counts cannot be confused"라고 적은 것이 이 테스트의 산물.
  1.1.0 릴리스 커밋 ba11c24 본문 "Stops the ingredients scope from identifying itself as Unreleased"도 이 결합이 만든 문장.
- **관찰된 비용**: (1) 숫자 하나 바꾸는 데 세 파일(소스 상수, lock, CHANGELOG) + 릴리스 때 `changed_in` 갱신. (2) CI가 기준 ref를 계산하는 33줄 yaml.
  (3) CHANGELOG가 없는 레포(Research_Paper 결정)에서는 성립 불가 — 테스트가 `## Unreleased` 문자열을 `index()`로 찾는다(`:64`). (4) 뜻의 변화는 못 본다
  (`:21-24`, 그래서 `docs/sources/<key>.md`가 또 필요).
- **재사용 형태**: lock 파일은 가져가지 않는다. 대신 S02(행에 scope 저장) + "scope는 상수에서 파생" 테스트(`snippets/test_scope_is_derived.py`)만.
  scope 변경의 **기록**은 run 행의 `scope` jsonb와 `collector_version`이 이미 한다 — 소급 질문("그때 몇 페이지 걸었나")은 SQL로 답한다.
- **등급: 변형** — 소유자 지시(scope-lock을 그대로 가져가지 않음). 잃는 것: "바꾸기 전에 사람이 문장을 쓴다"는 강제. 대신 커밋 본문(R09)에 맡긴다.

## S02. scope는 상수에서 파생, 모든 run 행에 저장

- **어디서**: trend-radar `src/trend_radar/contract.py:139-145`(주석: 왜 run마다 저장하는가), `AGENTS.md:25-28`("두 번 쓴 숫자는 한쪽이 썩는다"),
  `tests/test_scope_is_declared.py:131-172`(`CONSTANT_FOR` 표로 scope 값 == 모듈 상수), `:57-65`(scope 키 집합 == datasets 집합),
  `tool/checks/data:68-73`(spec 없이 수집한 run을 hygiene 위반으로).
- **관찰된 효과**: 오늘 P16이 `trend_radar.run`(140행)과 `fetch_log`(5,336행)만으로 소스별 성공률·p50·p90 표를 만들었다
  (`architect/slice-p16-collector-reliability/README.md:7,12-19`). 행이 자기 출처를 들고 있어서 가능했다.
- **관찰된 비용**: `ClassVar` 선언 규칙(`AGENTS.md:19-23`)과 `MappingProxyType` 중첩 같은 타입 체조. 테스트 180줄.
- **재사용 형태**: `contracts/run.md`에 "run 행은 `collector_version`, `schema_revision`, `scope(jsonb)`를 가진다"를 계약으로;
  `snippets/test_scope_is_derived.py` 30줄.
- **등급: 채택** — 새 레포 `contracts/`의 run/fetch_log 형태 항목이 바로 이것.

## S03. `service-db.json` 매니페스트 + 예산 산수 + `policy.py`

- **어디서**: trend-radar `service-db.json`(41줄: 4롤, 연결 예산 분해, 세션 기본값), `tests/test_service_database_manifest.py:32-46`
  (`instances × (pool+overflow) + workers + migration + spare == total`), `tool/db/policy.py:1-7`(매니페스트가 유일한 출처, GRANT SQL은 생성),
  **1,451줄**. yt-scrapper `service-db.json`(48줄: 3롤, 외부 객체 저장소 선언 `:12-19`, 예산 32), `deploy/tubedepth-worker.service:52-59`
  (`2C + 13 = 25 at C=6, 32 안에`). `docs/shared-postgres.md` 규칙 4(`:313`).
- **관찰된 효과**: 연결 예산이 "대충"이 아니라 산수로 닫힌다. 컨테이너 14개가 Postgres 하나에 붙은 오늘 스택에서 `max_connections` 초과가 없었다.
- **관찰된 비용**: (1) `policy.py` 1,451줄 — 렌더·감사·추출 리허설까지 한 파일, 새 레포가 다 쓸 일 없다. (2) 매니페스트 드리프트: `service-db.json:4`
  `"database": "trend_radar"`인데 실제 배치는 `app`(`stack/docker-compose.yml:152`, `architect/README.md` §6 #1) — 매니페스트가 "유일한 출처"인데 틀렸다.
  (3) 같은 정보가 `postgres-bootstrap.sql:144-153` 주석과 유닛 파일 주석에 또 있다.
- **재사용 형태**: `snippets/service-db.json`(한 스키마 = 한 항목, 3롤, 예산 분해) + `snippets/test_connection_budget_adds_up.py`(12줄).
  SQL 생성기는 두지 않는다 — bootstrap SQL을 직접 쓰고(04-O01) 매니페스트 값과 일치하는지 grep 한 번.
- **등급: 변형** — 모노레포 전체에 매니페스트 **하나**(스키마 4개 × 롤 3개 × 예산), 테스트 하나.

## S04. `SourcePolicy` — 유일한 요청 제어 계약, 상수 옆에 실측 이유

- **어디서**: trend-radar `src/trend_radar/contract.py:105-136`(interval/concurrency/timeout/attempts/depth/budget + `__post_init__` 검증),
  `AGENTS.md:37-41`("실측 응답 크기·지연·완료 창에서 나온다; 바꾸려면 새 측정과 어떤 운영 실패를 막는지 한 문장"),
  `src/trend_radar/sources/oliveyoung.py:74-78`("50이 이 엔드포인트 최대. 60·70·75·80·100에서 SUCCESS + 빈 목록 실측. 재측정 없이 올리지 말 것"),
  Research_Paper `src/paper_radar/contract.py:41-56`(`max_attempts=5` Retry-After 39~40초 실측, `budget_is_daily` 헤더 의미 충돌 실측),
  `docs/sources/oliveyoung.md:28-30`(`min_interval_s=5.0, concurrency=1`의 근거).
- **관찰된 효과**: 오늘 P16 — trend-radar 4소스 5,336요청 중 차단 1건(`slice-p16…/README.md:12-17`). 반대 사례가 같은 표에: tubedepth는
  팬아웃 상한이 없어 큐 232k 발산(`:26-28`). 정책이 계약에 있는 수집기와 없는 수집기의 차이가 수치로 나왔다.
- **관찰된 비용**: 상수마다 2~5줄 주석. `AGENTS.md:114-115` "이유 없는 상수는 다음 사람이 정리하는 상수" — 이 레포에서 가장 설득력 있는 주석 규칙이지만
  yt-scrapper `src/`에서는 prose/code 0.70으로 팽창했다(`metrics.md`).
- **재사용 형태**: `contracts/collector.md`에 SourcePolicy 필드 표 + "상수 옆 한 문장(측정일·값·막는 실패)" 규칙. 새 레포 `collectors/youtube`의 팬아웃 상한이
  첫 적용 대상.
- **등급: 채택** — 주석은 **한 문장**으로 제한(05-D07).

## S05. enum 멤버마다 수집기가 있다

- **어디서**: trend-radar `tests/test_every_dataset_has_a_collector.py:1-19`(`NEW_PRODUCT`·`PRODUCT` 두 번 겪은 "행 0건인 채로 존재하는 데이터셋"),
  `:37-45`(멤버마다 수집하는 소스가 있다), `:48-62`(수집한다는 소스는 seed도 준다), `docs/judgment-debt.md:23-43`(PRODUCT 삭제 → 이슈 #21로 복귀 경위).
- **관찰된 효과**: 62줄로 두 번 조사 비용을 막는다. 오늘 P16의 "크론 누락"(`review`/`review_stats`/`new_product`가 08-21 이후 0건)은 같은 모양의
  사고를 **배선 층**에서 겪은 것 — enum에는 있고 crontab에는 없었다.
- **재사용 형태**: `snippets/test_every_enum_member_is_collected.py` + T10 변형(크론 줄 존재)으로 배선까지 덮는다.
- **등급: 채택**.

## S06. 페이지 걷기는 정책 깊이 안에, 조용한 절단은 보고서에

- **어디서**: trend-radar `tests/sources/test_page_walks_fit_their_policy.py:1-12`(3페이지 선언 + max_depth 2 → 셋째 페이지가 요청도 안 되고 run은 ok),
  `tests/test_scope_is_declared.py:101-115`(같은 검사를 scope 쪽에서), `AGENTS.md:68-72`("No silent truncation… 두 번 겪었다"),
  `tests/engine/test_report_is_honest_about_stopping_early.py`, `tests/engine/test_a_run_records_only_the_scope_it_walked.py`.
- **관찰된 효과**: 오늘 b9ffa95가 `max_depth`를 "두 걷기 중 깊은 쪽"으로 바꿨다고 본문에 명시 — 이 테스트가 없었으면 `review_low` 6페이지 중 4페이지가 조용히 버려졌다.
- **재사용 형태**: 규칙 두 줄(계약) + 수집기마다 "선언한 깊이 ≤ 정책 깊이" 테스트 10줄.
- **등급: 채택**.

## S07. 정직한 User-Agent

- **어디서**: trend-radar `tests/test_user_agent_is_honest.py:1-15`(Chrome UA가 403, curl/빈 UA/Firefox는 200 — 2026-08-19 실측), `:25-47`
  (브라우저 토큰 금지, `trend-radar/<version>` + GitHub URL).
- **관찰된 효과**: 정책("위조 안 함")과 버그(WAF 403)를 한 테스트로. 47줄.
- **등급: 채택** — 새 레포 `contracts/collector.md`에 UA 형식 한 줄 + 테스트 그대로.

## S08. 예산 추적기 — 헤더 의미를 정책이 선언

- **어디서**: Research_Paper `src/paper_radar/contract.py:47-56`(`x-ratelimit-remaining`이 OpenAlex는 일일, NCBI는 초당 — 이름만으로 구분 불가,
  호스트명 분기 대신 정책 필드), `tests/paper_radar/test_budget.py`, git 로그 `f3b06fa fix(paper_radar): NCBI 초당 레이트리밋을 일일 예산으로 오독하던 경고 제거`.
- **관찰된 효과**: 오탐 경고로 운영자가 경고를 무시하게 되는 것을 막았다(`:52-55`).
- **등급: 채택** — 패턴만: "호스트 이름으로 분기하지 말고 정책이 선언한다".
