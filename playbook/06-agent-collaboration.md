# 06 — AI 에이전트 협업 규칙

네 레포의 커밋은 대부분 에이전트가 썼다: `Co-Authored-By: Claude` 트레일러가 trend-radar 85/122, yt-scrapper 165/219, cosmai-old 185/191, Research_Paper 58/61
(`metrics.md`). 따라서 AGENTS.md의 규칙이 "지켜졌는가"는 git 이력에서 직접 읽을 수 있다. 결론을 먼저 적으면 — **훅·테스트가 뒤에 있는 규칙은 지켜졌고,
산문뿐인 규칙은 문서가 스스로 드리프트를 고백하는 형태로 깨졌다.**

| ID | 이름 | 등급 |
|---|---|---|
| A01 | `CLAUDE.md` → `AGENTS.md` (symlink 또는 `@AGENTS.md`) | 채택 |
| A02 | "세션은 여기서 시작" — doctor → blocked 이슈 → 상태 문서 | 변형 |
| A03 | 완료의 정의 = 관찰 가능한 것 ("적재된 행을 조회해 숫자를 말한다") | 채택 |
| A04 | 역할 분리 orchestrator / planner / worker / attacker + task packet | 제외 |
| A05 | attacker 서브에이전트 `disallowedTools: Write, Edit` | 변형 |
| A06 | "요청받을 때만 커밋·푸시", 공동 저자 트레일러 | 채택 |
| A07 | 검증 못 하면 `BLOCKED` — 조건부 PASS 금지, `blocked/<what>` 이슈 라벨 | 채택 |
| A08 | 컨텍스트 파일은 프로젝트 지식만, 도메인 지식은 밖에 | 변형 |
| A09 | 규칙은 테스트/훅으로 — 산문 규칙의 준수율 실측 | 채택 |
| A10 | "관례를 통제라고 쓰지 말 것" — 강제 수단 명시 | 채택 |
| A11 | 대화 기록·세션 스냅샷을 트리에 두지 않음 (`.superpowers/` 등) | 변형 |
| A12 | "실패한 테스트를 분류하고 나서 고친다" / "호출자 없는 기능은 기능이 아니다" | 채택 |
| A13 | 배칭 → 이슈 → 클리어: 단편 지시를 즉시 실행하지 않고 결정이 닫힐 때까지 모아 이슈에 전체 계획을 쓴 뒤 새 컨텍스트로 구현 | 채택 (2026-08-23 신규) |
| A14 | 역할별 모델·effort를 에이전트 정의 파일로 고정 — 조정자는 싸게, 판정만 비싼 모델에 위임 | 채택 (2026-08-23 신규, 효과 미측정) |

---

## A01. `CLAUDE.md` → `AGENTS.md`

- **어디서**: trend-radar `CLAUDE.md -> AGENTS.md`(symlink), cosmai-old `CLAUDE.md`(`@AGENTS.md` 1줄), yt-scrapper `CLAUDE.md:1-7`("호환 진입점일 뿐, 정책은 여기 없다").
- **관찰된 효과**: 도구가 바뀌어도 한 파일. trend-radar `tests/test_docs_references_resolve.py:46`이 symlink를 건너뛰는 코드를 둬야 했던 것이 유일한 마찰.
- **등급: 채택** — `@AGENTS.md` 방식(symlink는 Windows checkout에서 깨진다).

## A02. 세션 시작 체크리스트

- **어디서**: yt-scrapper `AGENTS.md:9-18`(`tool/doctor.sh` → `gh issue list --label blocked` → `docs/status.md`), `:20-40`(마일스톤/이슈가 "무엇", status.md가 "왜").
  cosmai-old `AGENTS.md:28-35`("읽어라" 7개 문서). trend-radar `AGENTS.md:119`("contract.py를 먼저 읽어라") + `NOTES.local.md`.
- **관찰된 효과**: 오늘 `review_low` 세션이 trend-radar에서 AGENTS.md + NOTES.local.md만으로 컨텍스트를 잡았다. yt-scrapper는 `gh` 의존 — 이 머신은 `gh`가 있지만
  레포가 archive되면 이슈도 닫힌다.
- **관찰된 비용**: cosmai-old식 "7개 문서를 읽어라"는 2,000줄 이상을 세션마다 로드. 에이전트가 실제로 다 읽었는지 검증 불가.
- **재사용 형태**: AGENTS.md 첫 절 3줄: `tool/doctor.sh` / `NOTES.local.md`(있으면) / `docs/decisions.md`.
- **등급: 변형**.

## A03. 완료의 정의 = 관찰 가능한 것

- **어디서**: trend-radar `AGENTS.md:87-89`("초록 테스트가 일곱 번 거짓이었고 여섯 번은 exit 0"), `docs/working-agreements.md:12-27`(사고 표), `tool/checks/data`(T13).
  yt-scrapper `docs/definition-of-done.md:3-5`("'올바르게 동작' 은 검사 불가; '삭제된 레코드에 404'는 가능"), `:23-33` 매 변경 체크리스트 6항목.
- **관찰된 효과**: `NOTES.local.md:18-24` "방문 84, 채움 84, null 0. distinct 83… 길이 95~41,365, 중앙값 520" — 오늘 아침 머지 전에 실제로 행을 세었다.
  yt-scrapper `decisions/003`("테스트·타입체크·린트 전부 통과했는데 동작이 없었다")이 이 규칙의 근거.
- **관찰된 비용**: 자동화 불가. 에이전트가 "테스트 통과"를 완료로 보고하려는 경향을 사람이 매번 되물어야 한다.
- **재사용 형태**: AGENTS.md 한 줄 + `tool/checks/data`(T13). 완료 보고 형식: "적재 N행, distinct M, null K, 실행 시간".
- **등급: 채택**.

## A04. 역할 분리 + task packet

- **어디서**: cosmai-old `AGENTS.md:43-54`(orchestrator/planner/worker/attacker, 흐름 `owner decision → packet → result → attack → acceptance`), `docs/agent-workflow/`
  29개 md(역할 4 + 템플릿 2 + 프롬프트 + 패킷 12 + 리뷰 7), `.claude/agents/` 3개.
- **관찰된 효과**: `docs/agent-workflow/README.md:176-180` — 독립 리뷰어가 "max_pages=2에 12번 fetch, 600건 방출" 같은 실제 결함을 잡았다. 패킷 ACCEPTED 조건은 테스트
  (`tests/environment/test_agent_packet_record.py`)가 강제.
- **관찰된 비용**: (1) 문서 29개, 리뷰 라운드가 리뷰의 리뷰를 낳음(`REVIEW-TASK-001`, `-R2`, `-R3`). (2) 강제되는 것은 "패킷에 PASS 링크가 있는가" **하나**(`README.md:51-62`).
  (3) 8일간 191커밋 중 코드 산출이 슬라이스에 쓰인 것은 DataLab·blog 수집기뿐(`architect/REBUILD.md` §3). (4) 오늘 슬라이스 7개는 이 모델 없이 단일 세션 + 스크립트로 끝났다.
- **등급: 제외** — 소유자 지시. 독립 리뷰가 필요하면 A05 방식으로 ad hoc.

## A05. attacker 서브에이전트

- **어디서**: cosmai-old `.claude/agents/adversarial-reviewer.md:1-5`(`disallowedTools: Write, Edit, NotebookEdit`, "보고만, 수리 안 함"), `docs/agent-workflow/README.md:65-75`
  ("Bash가 남아 있어 2026-08-19 리뷰어가 Bash로 파일을 만들고 테스트를 고쳤다가 되돌렸다 — 완화이지 쓰기 장벽이 아니다. 진짜 속성은 **복사본에서 작업**하는 쪽에 있었다").
- **관찰된 효과**: 리뷰어가 저자의 확증 편향을 한 번 깼다(`README.md:176-183` "애드온이 협조했다"를 "플랫폼이 강제했다"로 읽은 사례).
- **관찰된 비용**: 프론트매터로 막히는 건 `Write/Edit`뿐. 리뷰 보고서 7개·리뷰의 리뷰.
- **재사용 형태**: 서브에이전트 정의 없음. 필요할 때 `git worktree`(R07) 복사본에서 "읽기 전용 리뷰" 프롬프트 한 줄 — 복사본이 장벽.
- **등급: 변형**.

## A06. 커밋·푸시는 요청받을 때만, 공동 저자 트레일러

- **어디서**: cosmai-old `AGENTS.md:26`("Commit or push only when asked"), 전 레포 커밋 본문 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` +
  `Claude-Session: https://claude.ai/code/session_…`(trend-radar 20b875e, ba11c24).
- **관찰된 효과**: 어느 세션이 어느 커밋을 만들었는지 추적 가능 — 오늘 b9ffa95(`review_low`)도 트레일러로 세션 식별. 사람 커밋(trend-radar `ea2b591 Mount the dev Postgres volume…`,
  `8b01202 Merge product rows…` — 훅 도입 전 비관례 제목 2건)과 구분된다.
- **관찰된 비용**: 없음.
- **등급: 채택**.

## A07. `BLOCKED` — 조건부 PASS 금지

- **어디서**: cosmai-old `AGENTS.md:53`("접근 없음·증거 없음은 PASS가 아니다"), yt-scrapper `docs/definition-of-done.md:11-19`(`gh issue create --label blocked --label blocked/<what>`),
  `AGENTS.md:15-16`(세션 시작에 blocked 이슈 조회), trend-radar `tool/checks/prerequisite`(exit 69 = 같은 원칙의 셸 버전, R04).
- **관찰된 효과**: yt-scrapper `AGENTS.md:120-125` — "도커 없음"이 거짓 전제였음을 발견하자 "두 결정을 다시 해야 한다"고 적고 넘어가지 않았다.
- **재사용 형태**: AGENTS.md 한 줄 + `NOTES.local.md` "## BLOCKED" 절(이슈 트래커 의존 없이).
- **등급: 채택**.

## A08. 컨텍스트 파일은 프로젝트 지식만

- **어디서**: trend-radar `tests/test_agent_context_is_project_only.py:1-15`("사이트가 내일 바뀌어도 살아남는 규칙만", 소스 키·한글 사이트명 금지 — 레지스트리에서 이름을 가져와
  5번째 소스가 생기면 자동 확장), `AGENTS.md:1-5`(첫 단락만 예외).
- **관찰된 효과**: AGENTS.md 145줄이 8일간 14회 수정됐지만 사이트별 지식은 한 번도 들어오지 않았다(테스트가 막음).
- **관찰된 비용**: 테스트 80줄 + `docs/domain.md`·`docs/sources/` 분리 유지. 새 레포는 모노레포라 "도메인"이 곧 프로젝트 — 경계가 다르다.
- **재사용 형태**: 규칙만: "AGENTS.md에는 `collectors/<x>/NOTES.md`로 옮길 수 있는 문장을 쓰지 않는다". 테스트 없음.
- **등급: 변형**.

## A09. 규칙은 테스트/훅으로 — 산문 규칙의 준수율

- **증거** (`metrics.md`):

  | 규칙 | 강제 수단 | 준수 실측 |
  |---|---|---|
  | Conventional Commits | 훅(trend-radar, yt) | 121/122, 219/219 |
  | Conventional Commits | 없음(cosmai-old; AGENTS.md에 언급도 없음) | 23/191 |
  | Conventional Commits | 없음(Research_Paper; AGENTS.md 자체가 없음) | 59/61 — 예외적으로 높음, 한 세션이 한 계획(`.superpowers/sdd`)으로 일관 |
  | 제목 ≤72자 | 훅 | 초과 0 / 0 |
  | 제목 ≤72자 | 없음 | cosmai-old 초과 114/191, 최장 150자 |
  | 소스는 아래로만 import | 산문(초기) → 테스트 | 산문 시절 4/4 소스가 위반(`test_sources_stay_at_their_layer.py:9-11`) |
  | 결정 색인 최신 | 산문 + `[측정]` 경고 | cosmai-old DP-028~035 8건 누락(경고문 바로 아래에서) |
  | "이 호스트에 도커 없음" | 산문 | 거짓인 채 "오랫동안"(`yt AGENTS.md:122`) |
  | scope 변경 시 기록 | 테스트 + lock | 오늘 b9ffa95가 첫 커밋부터 기록 |
  | 하드코딩 버전 없음 | AST 테스트 | 위반 0 |

- **결론**: 에이전트는 훅·테스트가 있는 규칙을 거의 100% 지켰고, 산문 규칙은 문서가 많을수록 더 어겼다(cosmai-old). "규칙을 늘리지 말고 검사를 늘려라"가 네 레포의
  공통 교훈이며 trend-radar `docs/working-agreements.md:174`("판정 가능한 것만 테스트로 갔다")이 그것을 명문화했다.
- **등급: 채택** — AGENTS.md 규칙 수 ≤8, 각 규칙에 강제 수단 열(D12).

## A10. "관례를 통제라고 쓰지 말 것"

- **어디서**: cosmai-old `AGENTS.md:54`, `docs/agent-workflow/README.md:45-47`("섞어 읽으면 프로토콜이 검사되는 대신 믿어진다"), `.claude/agents/adversarial-reviewer.md:7-12`
  ("`effort` 프론트매터는 조용히 무시된다 — 되는 줄 알고 넣지 말 것, 2026-08-18 확인").
- **관찰된 효과**: 에이전트가 자기 능력을 과장하지 않게 하는 가장 싼 규칙. 오늘 P16도 같은 자세("코드 변경 0, DB 로그 기준").
- **등급: 채택** — D12 표와 같은 항목.

## A11. 대화 기록·세션 스냅샷을 트리에 두지 않음

- **어디서**: cosmai-old `AGENTS.md:88-89`. 그런데 실제 트리: `cosmai/.superpowers/`(480K), `Research_Paper/.superpowers/`(148K), `yt-scrapper/docs/superpowers/`(76K),
  `cosmai/docs/superpowers/`(100K) — superpowers 플러그인의 SDD 원장·계획 파일. Research_Paper `docs/judgment-debt.md:4-6`은 `.superpowers/sdd/…/progress.md`를
  "원장"으로 **인용**한다(gitignore 여부와 무관하게 문서가 의존).
- **관찰된 비용**: 규칙과 현실이 다르다. 원장이 트리 안에 있으면 문서가 그것을 가리키기 시작하고, 클론에 없으면 링크가 끊긴다.
- **재사용 형태**: `.gitignore`에 `.superpowers/`·`*.local.md`; 문서는 그 안을 인용하지 않는다(인용할 가치가 있으면 `docs/decisions.md`로 옮긴다).
- **등급: 변형**.

## A12. 분류하고 고친다 / 호출자 없는 기능

- **어디서**: cosmai-old `AGENTS.md:41`("실패한 테스트는 구현·명세·가정·평가·목표 중 어느 실패인지 분류하고 나서 고친다"), yt-scrapper `decisions/003…:1-4, 26-30`
  ("기능을 추가하면 그 이름을 grep해 호출자를 센다"), `tests/conftest.py:54-60`(`--record-payload-shapes`를 CLI가 아니라 pytest 옵션으로 둔 이유 = 호출자 없는 src 코드 금지).
- **관찰된 효과**: `decisions/003` 이후 "워커가 그것을 호출한다"는 테스트가 추가됨(`:21-24`). trend-radar `docs/judgment-debt.md:100-131` "배선돼 있지만 아무도 안 읽는 것"
  정리(5개 삭제, 2개 의도적으로 보존)가 같은 규칙의 실행.
- **관찰된 비용**: 오늘 `architect/README.md` §6에 "미완성/실행 경로 없음" 항목이 10개 — 규칙이 있어도 에이전트는 호출자 없는 코드를 계속 만들었다(`ProxiedEgress`, `PlaceholderScreen.tsx`,
  `Source` 프로토콜 선언만). 사람이 grep하는 규칙은 사람이 잊는다.
- **재사용 형태**: AGENTS.md 두 줄 + 머지 전 `git grep -c <new_symbol>` 한 번. 새 레포 원칙 1("슬라이스가 증명한 경로만")과 같은 말.
- **등급: 채택**.

## A13. 배칭 → 이슈 → 클리어 (2026-08-23, 새 레포에서 처음 채택)

- **어디서**: 구 레포 4개에는 없던 관행. `cosmai` 1단계 세션(2026-08-23)에서 도출: 설계 논의(architect 세션) → `HANDOFF.md` → 구현 세션의 2단 구조가 잘 돌았고, 같은 세션 안에서 사용자의 단편 지시를 즉시 반영한 곳에서는 수정 라운드가 늘었다.
- **무엇**: 에이전트 코딩 중 사용자의 주요 변경 요구를 **즉시 구현하지 않고 큐에 쌓는다**. 결정 목록이 닫히면(미결 0, 또는 단계 경계) 전체 구현 계획을 GitHub 이슈에 정확한 값까지 적고, 컨텍스트를 clear한 새 세션이 이슈만 읽고 구현한다. 이슈 코멘트가 원장(판정·수정 라운드·완료 커밋)이다.
- **관찰된 효과**: `[측정]` 1단계 Task 2 — 조정자가 즉석에서 정한 `site_axis_map` 규칙이 2라운드 뒤 철회됨(25→21→34행). 원본 소스를 보고 계획했으면 1라운드. 같은 세션의 Task 1은 브리프가 값을 verbatim으로 담아 수정 0라운드. 계획의 정확도가 곧 라운드 수다.
- **관찰된 비용**: 작은 수정(오타·1줄)을 배칭하면 지연이 더 비싸다. "기다림"의 종료 조건이 없으면 결정이 영원히 안 닫힌다. 이슈가 계약·결정 기록과 또 한 벌의 정본이 되면 드리프트가 재발한다.
- **재사용 형태**: 배칭 기준 = 계약·DDL·인터페이스·순서·승인 경계를 건드리는 변경. 그 외는 즉시. 종료 조건 = 사용자가 "확정"을 말하거나 단계 경계. 이슈 = 실행 계획 + 사전 승인 + 판정 + 원장이고, 계약·결정 기록은 링크만. 이슈 본문은 서브에이전트 브리프 수준(정확한 값·경로·행 수·금지 목록). 큐는 사용자에게 보여야 한다(이슈 초안 코멘트). 상태 파일(`STATE.md`)은 부팅 순서·현재 사실·경계만.
- **등급: 채택**. 강제 수단: 없음(산문 규칙) — 단 "이슈 없는 구현 브랜치는 머지하지 않는다"를 pre-push가 브랜치명 `feat/<issue#>-…` 형식으로 검사할 수 있다(미설치).

## A14. 역할별 모델·effort를 정의 파일로 고정 (2026-08-23, 효과 미측정)

- **어디서**: 구 레포에는 없던 관행(A04 역할 분리의 가벼운 변형 — packet·의례 없이 모델·effort 선택만). 새 레포 `.claude/agents/{impl-mechanical,impl,reviewer,reviewer-deep,judge}.md`.
- **무엇**: 서브에이전트 역할마다 `model`·`effort`·`disallowedTools`를 프론트매터로 고정한다. 구현자 둘(기계적 = sonnet·medium, 판단 = opus·high), 리뷰어 둘(sonnet·high / opus·high, 읽기 전용), 판정자 하나(fable·high, 읽기 전용 — 컷오버 조건·전체 리뷰·계약 변경만). 조정 세션은 중간 모델(Opus)·high로 두고, 비싼 판단만 `judge`에 위임한다. 세션 모델을 바꾸는 대안은 `/model` 전환 또는 `claude --model …`로 새 세션(이슈·STATE.md가 맥락을 들고 있어 손실 없음).
- **실측된 사실**: ① Agent 도구에는 `model`만 있고 effort가 없다 — 정의 파일 없이는 서브에이전트가 세션 effort를 그대로 상속한다(2026-08-18 측정: 세션 effort가 high→xhigh→low로 바뀌는 동안 통제되지 않았다). ② 정의 파일은 세션 시작 시 고정된다 — 만든 세션에서는 `Agent type not found`(2026-08-18). ③ 1단계 실측: sonnet 구현자 11분·수정 0라운드(값이 브리프에 verbatim), opus 구현자 24분·수정 3라운드(매핑 판단 13개) — 라운드 수는 모델보다 브리프 정확도에 좌우됐다. 리뷰 6회 ≈ 26분 = 전체의 ~25%.
- **가설(다음 세션에서 측정)**: 기계적 유닛을 medium으로 내려도 수정 라운드가 늘지 않는다; 조정자를 Opus로 내려도 판정 품질(1단계 10개 중 철회 1개)이 유지된다; `judge` 위임으로 4차 웨이브의 사람 개입 0을 유지한다. 측정 항목: 유닛별 구현 시간·수정 라운드·리뷰 시간·판정 철회 수를 이슈 코멘트에서 집계.
- **관찰된 비용**: 정의 파일이 세션 시작에 고정되므로 "지금 만들어 지금 쓰기"가 안 된다(clear 경계와 맞춰야). 역할이 늘면 정의도 늘어 드리프트 대상이 된다 — 5개를 상한으로.
- **재사용 형태**: 정의 파일 5개(각 ≤15줄 시스템 프롬프트: 범위·금지·보고 형식만). 선택 규칙은 이슈의 리뷰 등급(A/B/C)과 1:1로 묶는다(`#16`). 등급 없는 레포라면 "값이 브리프에 다 있나"가 유일한 분기.
- **등급: 채택**(효과 미측정 — 측정 후 등급 재평가).
