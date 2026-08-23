# 05 — 문서·결정 관리

실측(`metrics.md`): 마크다운 줄 수 / 코드 줄 수 — trend-radar 0.35, yt-scrapper 0.69, Research_Paper 0.10, **cosmai-old 0.88**(217개 md, 39,861줄,
8일간). 거버넌스 문서의 커밋 관여율 — yt-scrapper `docs/status.md` 62/219 커밋(28%), cosmai-old `docs/decisions/` 40/191 + `project-state.md` 34/191,
trend-radar `docs/judgment-debt.md` 26/122. 문서가 많을수록 코드 커밋이 문서 수정을 동반하는 비율이 올라갔고, 그래도 드리프트는 남았다(아래 각 항목).

| ID | 이름 | 등급 |
|---|---|---|
| D01 | AGENTS.md 구조 — "깨면 비싼 규칙" / "일하는 방식" / 레이아웃 / 검사, ~150줄 | 변형 |
| D02 | `docs/judgment-debt.md` — 세 통(안 함 / 탐색했고 비었음 / 안 하기로 결정) + 되돌릴 조건 | 채택 |
| D03 | `NOTES.local.md` — gitignore된 세션 노트, 굳으면 judgment-debt로 | 채택 |
| D04 | `docs/sources/<key>.md` — 날짜 붙은 사이트 관측 기록 | 변형 |
| D05 | 결정 기록: yt-scrapper `decisions/`(겪고 나서만, 비용 명시) vs cosmai-old `DP-*`(템플릿 12절) | 변형 / 제외 |
| D06 | `experiments/EXPERIMENT-TEMPLATE.md` — 가설·반증·종료 조건 | 제외 |
| D07 | 주석 목소리 — "규칙 옆에 그것이 막는 실패" | 변형 |
| D08 | 증거 라벨 `[확인 사실]`·`[측정]`·`[추론]`·`[가설]`·`[결정]` | 변형 |
| D09 | `docs/status.md`·`docs/plan.md` 보존 vs "계획서를 보관하지 않는다" | 제외 |
| D10 | `docs/troubleshooting.md` — 헤딩이 에러 메시지, grep용 | 채택 |
| D11 | README `.ko.md` 번역 쌍 | 제외 |
| D12 | `docs/working-agreements.md` — 규약마다 "강제되는가" 표 | 채택 |

---

## D01. AGENTS.md 구조

- **어디서**: trend-radar `AGENTS.md`(145줄): `:7-72` "The rules that are expensive to break" 8개(각각 왜·무엇이 깨지는가), `:74-115` "How we work here" 7개,
  `:117-138` Layout(문서 지도 + `*.local.md`), `:140-145` Checks. yt-scrapper `AGENTS.md`(163줄): `:9-18` "Every session starts here", `:46-81` 규칙, `:83-115` Workflow,
  `:117-131` This host, `:148-163` Layout 표. cosmai-old `AGENTS.md`(107줄): 읽어야 할 문서 12개 지시(`:9-36`), 역할 모델(`:43-54`), 라벨(`:70-77`).
- **관찰된 효과**: trend-radar의 규칙 8개는 전부 테스트 또는 훅이 뒤에 있다(`docs/working-agreements.md:172-184` 표) — "규칙이 산문이던 동안 틀려 있었다"
  (`test_sources_stay_at_their_layer.py:9-13`)를 겪고 테스트로 옮긴 결과. 오늘 새 세션이 `review_low`를 추가할 때 이 파일만 읽고 scope·no-ff·커밋 형식을 지켰다.
- **관찰된 비용**: (1) 드리프트 — yt-scrapper `:120-125` "이 줄은 오랫동안 '도커 없음'이라 적혀 있었고 두 결정이 그 거짓 전제 위에 있다. 둘 다 다시 결정해야 한다"
  (문서가 스스로 고백). trend-radar `:79` master-only ↔ `tool/worktree.sh:22` dev. (2) cosmai-old는 AGENTS.md 자체는 짧지만 "읽어라"가 12개 문서·2,192줄
  (`docs/conventions/*` + `branching.md` + `agent-workflow/README.md`)로 번진다. (3) 사이트 이름 금지 테스트(T09)가 AGENTS.md를 "프로젝트 규칙"으로 유지했지만
  그 대가로 `docs/domain.md`·`docs/sources/` 4개가 더 생겼다.
- **재사용 형태**: `snippets/AGENTS.template.md` — 4절(규칙 ≤8 / 일하는 방식 ≤6 / 레이아웃 표 / 검사 명령), **120줄 상한**, 규칙마다 "강제: 훅|테스트|없음" 표기.
- **등급: 변형**.

## D02. `docs/judgment-debt.md` — 세 통

- **어디서**: trend-radar `docs/judgment-debt.md:1-7`(정의: 무엇을 / 왜 / 무엇이 바뀌면 다시 보나), `:11-151` §1 안 하기로 한 것, `:153-238` §2 알면서 남긴 한계,
  `:240-255` §3 탐색했고 비었음, `:318-543` §5 검증·해결 이력(543줄 중 225줄). `AGENTS.md:91-93`("섞이면 결정이 미완성으로 읽혀 조용히 되돌려진다").
  Research_Paper `docs/judgment-debt.md:1-7`("trend-radar 교본의 패턴"이라 명시하고 이식, 86줄 표 형식), cosmai-old는 `docs/open-questions/` 16개(OPEN 10, RESOLVED 4)로 분산.
- **관찰된 효과**: `:23-43` `Dataset.PRODUCT` 삭제(08-20) → 조건 충족(08-21, 이슈 #21) → 복귀. "되돌릴 조건"이 적혀 있어서 복귀가 번복이 아니라 조건 이행으로 기록됐다.
  오늘 `architect/REBUILD.md` §3 "cosmai experiments/ 삭제 후보"도 cosmai-old 문서가 아니라 이 패턴으로 적을 내용.
- **관찰된 비용**: 543줄로 자랐다 — §5 "해결 이력"이 changelog와 중복(`CHANGELOG.md:3-5`가 "결정은 judgment-debt에"라며 서로 가리킴). 26/122 커밋이 이 파일을 건드림.
- **재사용 형태**: `snippets/judgment-debt.template.md` — §1~§3만, 항목당 3줄(무엇/왜/되돌릴 조건), 이력 절 없음. 해결되면 항목을 **지운다**(git이 이력).
- **등급: 채택**.

## D03. `NOTES.local.md`

- **어디서**: trend-radar `.gitignore:101` `*.local.md`, `AGENTS.md:133-138`, `NOTES.local.md:1-11`(커밋 안 됨, 굳은 판단은 judgment-debt로),
  `docs/working-agreements.md:163-166`("클론한 사람은 진행 중인 것을 모른다 — 받아들인 거래").
- **관찰된 효과**: `NOTES.local.md:13-33` 오늘 아침 세션 기록(실측 검증 숫자, 배포 반영, 열린 것)이 그대로 다음 세션의 출발점. 175줄, git 밖.
  cosmai-old `AGENTS.md:88-89`는 반대로 "세션 스냅샷을 트리 어디에도 두지 말 것" — 그 결과 진행 상태가 `docs/project-state.md`(435줄, 34회 수정)로 갔다.
- **관찰된 비용**: 머신을 바꾸면 없다. 그게 의도.
- **재사용 형태**: `.gitignore`에 `*.local.md` 한 줄 + AGENTS.md 한 단락.
- **등급: 채택**.

## D04. `docs/sources/<key>.md` — 날짜 붙은 관측

- **어디서**: trend-radar `docs/sources/oliveyoung.md:1-30`(정찰 날짜, curl 403 vs Playwright 200 원문, 정책 값의 근거), `docs/working-agreements.md:81-109`
  ("날짜 없는 사실은 언제부터 거짓이었는지 모른다"; 트림된 픽스처·응답 메타데이터를 관측으로 착각한 세 사례 표).
- **관찰된 효과**: `test_user_agent_is_honest.py:9-12`의 2026-08-19 실측, `CHANGELOG.md:43-49`의 "13샘플 3.0~34.7초" 같은 숫자가 전부 이 관행에서 나왔다.
- **관찰된 비용**: 4파일. 관측이 코드 상수 주석(S04)과 중복되기 쉽다.
- **재사용 형태**: 새 레포 `collectors/<source>/NOTES.md` 한 파일(날짜·명령·응답 원문 3줄 단위). 별도 `docs/` 트리 없음.
- **등급: 변형**.

## D05. 결정 기록 — 두 방식

- **어디서**: yt-scrapper `decisions/README.md:1-13`("겪고 나서, 측정된 비용이 있을 때만; 안 겪은 규칙은 status.md로"), 표 `:15-19`(비용: 8× 처리량, p99 1,434→19.9ms,
  리스 갱신 0 호출), 파일당 30~36줄, 3개. cosmai-old `docs/decisions/DP-TEMPLATE.md`(67줄 12절), DP 33개 **5,051줄**, 최대 349줄(`DP-033`), `README.md:32-36`
  ("`[측정]` 이 목록은 DP-018~022를 누락했다 — 낡은 색인은 색인 없음보다 나쁘다").
- **관찰된 효과**: yt 방식 — 각 결정이 수치 하나로 요약되고 "무엇이 바뀌면 그만둘지"를 적는다(`001…:31-36`). 사문화도 예견대로: `002…:29-33` "Postgres로 가면 이 구분은
  아무것도 못 번다" → 실제로 Postgres 이관(#15) 후 `readonly=True` 분기만 남았다(`src/tubedepth/collection.py:167-177`).
- **관찰된 비용**: (1) cosmai-old 색인이 **또** 낡았다 — 위 `[측정]` 경고 아래에서 DP-028~035 8건이 색인에 없다(`grep -c DP-03 docs/decisions/README.md` = 0).
  (2) DP 5,051줄 중 실행 경로가 있는 결정은 소수 — `architect/analysis/cosmai-apps.md` 기준 experiments는 어떤 슬라이스도 안 썼다. (3) yt `decisions/002`도 사문화된 채
  `README.md:18` 표에 "활성"으로 남아 있다(삭제 규칙은 있지만 실행 안 됨).
- **재사용 형태**: yt 형식 축약 — `docs/decisions.md` **한 파일**, 항목당 ≤10줄(규칙 / 겪은 비용 수치 / 그만둘 조건), "겪은 뒤에만". `snippets/decision-entry.md`.
- **등급: 변형(yt) / 제외(DP 템플릿)** — 소유자 지시.

## D06. 실험 템플릿

- **어디서**: cosmai-old `experiments/EXPERIMENT-TEMPLATE.md`(138줄: 가설·반증·종료 조건·입력 출처·환경·관측·해석·결과·체크리스트), `AGENTS.md:103-104`.
- **관찰된 효과**: 오늘 슬라이스 7개(`architect/slice-*/README.md`)는 이 템플릿 없이 "질문 / 쓴 데이터 / 결과 / 요구사항 / 한계" 5절로 같은 일을 했고 재구성 사양의 근거가 됐다.
- **관찰된 비용**: 템플릿 자체가 `[가설]`·`[측정]` 라벨 규칙과 결합돼 한 실험 문서가 수백 줄.
- **등급: 제외** — 슬라이스 README 5절 형식을 `eval/README.md`에 관례로만.

## D07. 주석 목소리 — "규칙 옆에 막는 실패"

- **어디서**: trend-radar `AGENTS.md:114-115`, `docs/working-agreements.md:138-139`. 실례: `sources/oliveyoung.py:74-78`, `tool/checks/data:8-24`, `.githooks/pre-push:16-27`.
- **관찰된 효과**: 숫자마다 근거가 있어 오늘 P16·REBUILD가 코드만 읽고 정책값의 의도를 복원할 수 있었다(메모리 규칙 "계획이 아니라 코드를 분석").
- **관찰된 비용**: 측정치 — src 디렉터리 prose/code: trend-radar 0.39, Research_Paper 0.61, **yt-scrapper 0.70**; 코드 20줄 이상인 src 파일 중 산문 ≥ 코드인 파일
  trend-radar 3/30, Research_Paper 7/38, yt-scrapper 7/37, cosmai-old 28/192(최대 `apps/addon_host/settings.py` 코드 23줄 : 산문 49줄). 파일 docstring이 사고 서사로
  자란다(`tests/test_fixtures_are_scrubbed.py:1-16` 16줄, `test_collection_scope_is_recorded.py:1-25` 25줄).
- **재사용 형태**: 규칙 수정 — "상수·조건문 옆 **한 문장**: 날짜·측정값·막는 실패. 사고 서사는 커밋 본문(R09)으로". 파일 docstring ≤5줄.
- **등급: 변형**.

## D08. 증거 라벨

- **어디서**: cosmai-old `AGENTS.md:70-77`, `docs/conventions/evidence-labels.md`(246줄). 사용량: docs+experiments에서 `[측정]` 1,054 / `[확인 사실]` 850 / `[추론]` 664 /
  `[결정]` 461 / `[가설]` 168; 커밋 본문에도 68회.
- **관찰된 효과**: `docs/agent-workflow/README.md:160-190`처럼 "누가 무엇을 측정했고 무엇은 추론인가"가 리뷰 라운드(R2, R3)에서 실제로 교정됐다.
- **관찰된 비용**: 라벨이 문장마다 붙어 읽기 비용이 크고, 라벨을 붙이는 규칙 문서가 246줄. 오늘 슬라이스 README는 라벨 없이 "실측"·"한계" 절로 같은 구분을 했다.
- **재사용 형태**: `eval/`·`analysis/` 노트에서 **숫자 옆에 날짜와 명령**을 적는 것으로 대체. 라벨 체계 없음.
- **등급: 변형**.

## D09. `status.md`·`plan.md` 보존

- **어디서**: yt-scrapper `docs/status.md` **1,687줄**(헤딩 "Decisions that are expensive to reverse" 이하 20여 절), `docs/plan.md` 1,126줄(`AGENTS.md:28-29` "M0–M9 전부 끝났고
  기록으로만 — 여기서 일을 고르지 말 것"). 반대: trend-radar `docs/working-agreements.md:159-162` "계획서를 보관하지 않는다. 낡은 계획은 없는 계획보다 나쁘다 — 현재 상태로 읽힌다".
- **관찰된 비용**: 1,126줄의 끝난 계획을 "읽지 말라"고 안내하는 문서가 따로 필요하다. status.md는 219커밋 중 62회 수정.
- **등급: 제외** — trend-radar 원칙 채택. 결정은 D05 한 파일, 진행은 `NOTES.local.md`, 순서는 `git log`.

## D10. `docs/troubleshooting.md` — grep용

- **어디서**: yt-scrapper `AGENTS.md:42-44`("헤딩이 실제 에러 메시지다. 위에서부터 읽지 말고 grep"), 265줄.
- **관찰된 효과**: `test_no_ddl_on_the_boot_path.py:9-10`의 `duplicate column name`처럼 테스트 docstring이 이 파일의 헤딩을 가리킨다.
- **등급: 채택** — 새 레포 `docs/troubleshooting.md` 하나, 헤딩 = 에러 원문.

## D11. README 번역 쌍

- **어디서**: yt-scrapper `AGENTS.md:85-98`(README·api·CHANGELOG·AGENTS 네 문서는 영어 원본 + `.ko.md`, 나머지는 한국어만), trend-radar `README.ko.md` + T09 테스트.
- **관찰된 비용**: 문서 4종 × 2 + 동기화 테스트. 외부 독자가 없다.
- **등급: 제외** — 한국어 단일. 식별자·경로는 영어(이 playbook과 같은 규칙).

## D12. "강제되는가" 표

- **어디서**: trend-radar `docs/working-agreements.md:172-184`(규약 7개 × 강제 수단), cosmai-old `docs/agent-workflow/README.md:43-75`("강제 1항목, 나머지는 관례 —
  관례를 통제라고 쓰지 말 것"), `AGENTS.md:54`.
- **관찰된 효과**: 어느 규칙이 훅/테스트 없이 사람에게 기대는지 한눈에. cosmai-old는 이 표를 쓰다가 "2개 강제"가 틀렸음을 리뷰로 잡았다(`docs/agent-workflow/README.md:51-52`).
- **등급: 채택** — AGENTS.md 규칙 표의 한 열로 흡수(D01).
