# playbook — 기존 레포 4개에서 추출한 개발 방법론 카탈로그

**대상**: `service/trend-radar`, `service/yt-scrapper`(tubedepth), `service/cosmai`(cosmai-old), `service/Research_Paper`(paper-radar) + `service/stack`, 2026-08-23 기준 아카이브 직전 상태.
**원칙**: 문서가 주장하는 것이 아니라 코드·훅·테스트·git 이력에 있는 것만 적었다. 모든 항목에 `path:line` 근거가 있고, 숫자는 `metrics.md`에 측정 스크립트와 함께 있다.
**용도**: 새 `cosmai` 모노레포가 무엇을 가져가고 무엇을 버리는지의 기록이자, 다음에 무관한 프로젝트를 시작할 때 다시 쓸 목록.

## 읽는 법

- 섹션 파일 6개가 본문이다. 항목마다 **어디서 / 무엇 / 관찰된 효과 / 관찰된 비용 / 재사용 형태 / 등급**.
- 등급 — **채택**: 그대로 가져간다. **변형**: 핵심만 더 작은 형태로. **제외**: 가져가지 않는다(이유 한 문장).
- `snippets/`는 각 관행을 나르는 가장 작은 산출물이다(전부 80줄 이하, 두 줄 헤더: 출처 경로 / 재사용 시 바꿀 것). 그대로 복사해 쓴다.
  `postgres-bootstrap.sql`은 일회용 컨테이너에서 2회 실행(멱등)·runtime DDL 거부·migrator 생성·runtime DML까지 확인했다.
- 새 레포에 적용할 때의 제약(소유자): 테스트는 진짜이고 빠르게 · 3롤 DB 유지 · 평가셋 회귀 유지 · 긴 docstring·결정 기록 의례·doc-truth 메타테스트·scope-lock 원형은 가져가지 않음.

| 파일 | 내용 | 항목 수 |
|---|---|---|
| [01-repo-discipline.md](01-repo-discipline.md) | 훅, Conventional Commits, 워크트리, 브랜치, CHANGELOG, gitleaks | 12 |
| [02-test-discipline.md](02-test-discipline.md) | 소켓 차단, 진짜 DB 픽스처, 메타테스트, 골든, 계층 가드, live 마커 | 17 |
| [03-scope-policy-locks.md](03-scope-policy-locks.md) | scope.lock, 정책 매니페스트, service-db.json, 실측 근거 있는 상수 | 8 |
| [04-ops-deploy.md](04-ops-deploy.md) | systemd, compose, 3롤 DB bootstrap, 비밀 저장소, 헬스체크, 크론 | 9 |
| [05-docs-decisions.md](05-docs-decisions.md) | AGENTS.md 구조, 결정 기록, judgment-debt, NOTES.local, 실험 템플릿, 주석 목소리 | 12 |
| [06-agent-collaboration.md](06-agent-collaboration.md) | AGENTS/CLAUDE가 에이전트에게 요구한 것과 git 이력상 실제 준수 | 12 |
| [metrics.md](metrics.md) | 산문/코드 비율, 문서 분량, 테스트 수·시간, 메타테스트 수, 훅·커밋 형태, 드리프트 목록 | — |

## 등급 요약 (70항목: 채택 41 · 변형 21 · 제외 8)

| 섹션 | 채택 | 변형 | 제외 |
|---|---|---|---|
| 01 저장소·브랜치·커밋 | R01 commit-msg 훅 · R02 pre-commit→`tool/checks` · R03 pre-push · R04 exit 69 · R06 doctor.sh · R09 판단 하나/커밋 · R11 gitleaks | R05 CI 백스톱 · R07 worktree.sh · R08 브랜치 모델 · R10 버전·CHANGELOG | R12 Justfile |
| 02 테스트 | T01 소켓 차단 · T02 가드의 가드 · T03 테스트당 스키마 · T04 일회용 PG+bootstrap · T05 AST 계층 · T06 공허성 가드 · T07 골든 바이트 · T08 픽스처 스크럽 · T11 DDL 금지·권한 음성증명 · T13 적재 행 검사 · T14 live 분리 | T10 설정 진실성 · T12 shape lock · T15 서브프로세스/TLS · T17 경계 테스트 | T09 doc-truth 메타테스트 · T16 수용 시나리오 md |
| 03 범위·정책 | S02 scope 파생·run 저장 · S04 SourcePolicy+실측 상수 · S05 enum 멤버=수집기 · S06 걷기≤깊이 · S07 정직한 UA · S08 예산 의미 선언 | S01 scope.lock · S03 service-db.json+policy.py | — |
| 04 운영·배포 | O01 3롤 bootstrap · O02 멱등 init · O03 compose 한 파일 · O05 비밀 트리 밖 · O06 헬스체크 · O07 supercronic UTC | O04 systemd 유닛 · O09 이관 스크립트 | O08 flake.nix |
| 05 문서·결정 | D02 judgment-debt 세 통 · D03 NOTES.local · D10 troubleshooting grep · D12 "강제되는가" 표 | D01 AGENTS.md 구조 · D04 소스 관측 노트 · D05 결정 기록(yt형) · D07 주석 목소리 · D08 증거 라벨 | D05 DP 템플릿 · D06 실험 템플릿 · D09 status/plan 보존 · D11 번역 쌍 |
| 06 에이전트 협업 | A01 CLAUDE→AGENTS · A03 관찰 가능한 완료 · A06 요청 시 커밋 · A07 BLOCKED · A09 규칙은 검사로 · A10 관례≠통제 · A12 분류 후 수정/호출자 | A02 세션 시작 · A05 attacker · A08 프로젝트 지식만 · A11 세션 기록 격리 | A04 역할 분리+패킷 |

## 추천 최소 세트 — 새 프로젝트에 첫날 넣을 10개

순서가 곧 설치 순서다. 전부 `snippets/`에 있다.

| # | 관행 | 스니펫 | 왜 이것부터 |
|---|---|---|---|
| 1 | 훅 3종 + `tool/checks/{format,lint,test,prerequisite}` (R01–R04) | `commit-msg`, `pre-commit`, `pre-push`, `tool-checks/` | 준수율이 훅 유무로 121/122 vs 23/191 갈렸다. 정의는 한 곳, 훅·CI는 호출만 |
| 2 | 소켓 차단 conftest + 가드의 가드 (T01, T02) | `conftest_no_network.py`, `test_conftest_guard.py` | 1,152 테스트 2.7초·805 테스트 1.3초의 전제. 40줄 |
| 3 | 진짜 Postgres, 테스트당 스키마, 일회용 컨테이너 (T03, T04) | `db_schema_per_test.py`, `tool-checks/test` | "프로덕션은 PG, 테스트는 SQLite"가 방언 버그를 내보낸다. 컨테이너는 프로덕션 bootstrap SQL로 초기화 |
| 4 | 3롤 멱등 bootstrap + runtime DDL 거부 테스트 (O01, O02, T11) | `postgres-bootstrap.sql`, `test_runtime_role_cannot_ddl.py` | 런타임 DDL이 DB 수준에서 불가. 두 번 실행해도 같은 상태(오늘 검증) |
| 5 | 비밀은 트리 밖, 레포에는 이름만 (O05, R11) | `with-secret-source.sh`, `env.example`, `gitleaks.toml` | 네 레포 이력에 자격증명 0건의 이유 |
| 6 | AST 계층 가드 + 공허성 가드 (T05, T06) | `test_layering_guard.py` | 산문 규칙은 4/4 소스가 어겼고 테스트로 옮기자 0. "검사할 것이 있었다"를 먼저 단언 |
| 7 | 골든/평가셋 바이트 회귀 (T07) | `test_golden_files.py` | 규칙↔LLM 교체 판정은 같은 입력·같은 출력 파일 비교로만. 어느 파일 몇 번째 줄인지 메시지에 |
| 8 | scope 파생·run 행 저장 + enum 멤버마다 수집기·크론 (S02, S05, T10) | `test_scope_is_derived.py`, `test_every_enum_member_is_collected.py`, `test_stack_commands_resolve.py`, `crontab` | 오늘 P16의 세 사고(크론 누락·10초 스케줄·off-by-one)가 전부 "선언은 있고 배선이 없음" |
| 9 | AGENTS.md ≤120줄(규칙마다 강제 수단) + judgment-debt 세 통 + NOTES.local (D01, D02, D03, D12, A09) | `AGENTS.template.md`, `judgment-debt.template.md`, `decision-entry.md` | 문서가 많을수록 드리프트가 늘었다(cosmai-old md/code 0.88, 색인 8건 누락). 규칙 수 대신 검사 수를 늘린다 |
| 10 | 완료 = 적재된 행 + `tool/checks/data` (A03, T13) | `data-checks.sh` | 초록 테스트가 7번 거짓, 6번은 exit 0. 유일하게 자동화 안 되는 규칙이라 마지막이 아니라 열 번째 |

**넣지 않는 이유가 분명한 것**: scope.lock 파일(S01 — run 행의 scope·version이 같은 질문에 SQL로 답한다), doc-truth 메타테스트(T09 — 문서를 줄이는 쪽이 싸다),
DP 결정 템플릿·task packet·역할 분리(D05/A04 — 8일·5,051줄·29문서 대비 슬라이스에 쓰인 산출은 수집기 둘), 번역 쌍(D11), 끝난 계획 보관(D09).

## 가장 큰 비용 세 가지 (숫자)

1. **산문이 코드를 넘는다** — yt-scrapper `src/` prose/code 0.70, `database.py` 코드 82줄 : 산문 195줄; cosmai-old md 39,861줄 / code 45,319줄(0.88), src 파일 28/192가 산문 ≥ 코드. 테스트 파일 docstring이 사고 서사(16~25줄)로 자란다. → D07 "한 문장" 규칙.
2. **문서·배선 드리프트** — 오늘 확인 9건(`metrics.md` §5): 없는 파일을 가리키는 훅 주석 2, AGENTS↔스크립트 브랜치 불일치, 매니페스트 database명 불일치, 사문화된 결정 기록, 색인 누락 8건, apps/experiments 분기 59파일, 레포 compose↔stack compose 두 벌이 만든 크론 누락 3줄. doc-truth 테스트가 **있는** 레포에서도 테스트가 안 보는 곳에서 났다.
3. **의례의 실행 경로 부재** — cosmai-old 191커밋·DP 33개·agent-workflow 문서 29개 중 오늘 슬라이스가 쓴 코드는 DataLab·blog 수집기; `experiments/`는 어떤 슬라이스도 안 씀(`architect/REBUILD.md` §3). 훅이 없어 제목 72자 초과 114/191.
