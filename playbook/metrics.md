# metrics — 2026-08-23 실측

측정 스크립트: `snippets/measure_prose_ratio.py` (stdlib만; AST로 docstring 줄, tokenize로 전체-줄 주석을 세고 나머지를 코드로 분류.
`.venv`·`node_modules`·`__pycache__`·`.superpowers`·`var`·`data`·`out` 제외). 실행: `python3 snippets/measure_prose_ratio.py trend-radar yt-scrapper cosmai Research_Paper stack`
(`/home/user1/github_prj/Main/service/`에서). 테스트 수·시간은 각 레포에서 `uv run --frozen pytest` / `--collect-only`. 커밋 통계는 `git log --format=%s|%b`.

## 1. 산문(docstring + 주석) 대 코드

| 레포 | .py 파일 | 코드 줄 | docstring 줄 | 주석 줄 | **prose/code** | docstring/code | src(또는 apps) prose/code |
|---|---|---|---|---|---|---|---|
| trend-radar | 138 | 12,450 | 1,937 | 1,487 | **0.28** | 0.16 | src 0.39, tests 0.24, migrations 0.41 |
| yt-scrapper | 104 | 12,940 | 5,280 | 1,617 | **0.53** | 0.41 | src **0.70**, tests 0.44 |
| cosmai-old | 231 | 45,319 | 16,688 | 3,682 | **0.45** | 0.37 | apps 0.46, experiments 0.43, tests 0.59 |
| Research_Paper | 99 | 13,413 | 3,237 | 823 | **0.30** | 0.24 | src 0.61, tests 0.13 |

코드 20줄 이상인 src/apps/experiments 파일 중 **산문 ≥ 코드**인 파일 (`snippets/measure_prose_ratio.py`와 같은 분류, 파일별 집계):

| 레포 | 파일 수 | 최상위 예 |
|---|---|---|
| trend-radar | 3 / 30 | `src/trend_radar/scrub.py` 코드 26 : 산문 39, `storage/repository.py` 64 : 82, `contract.py` 75 : 77 |
| yt-scrapper | 7 / 37 | `src/tubedepth/database.py` 82 : **195**, `watchlist.py` 40 : 70, `transfer.py` 78 : 136, `sources/registry.py` 55 : 91 |
| cosmai-old | 28 / 192 | `apps/addon_host/settings.py` 23 : 49, `apps/platform_core/db/connection.py` 45 : 84, `handlers/synthetic.py` 62 : 107 |
| Research_Paper | 7 / 38 | `src/paper_radar/sources/pubmed.py` 121 : 196, `sources/pubchem.py` 44 : 64 |

## 2. 마크다운 문서

| 레포 | .md 파일 | .md 줄 | **md/code** | 큰 문서 |
|---|---|---|---|---|
| trend-radar | 20 | 4,369 | 0.35 | `docs/judgment-debt.md` 543, `CHANGELOG.md` 247, `README.md` 217, `docs/working-agreements.md` 184, `AGENTS.md` 145 |
| yt-scrapper | 21 | 8,953 | 0.69 | `docs/status.md` **1,687**, `docs/plan.md` 1,126(끝난 계획), `docs/shared-postgres.md` 695, `CHANGELOG.md` 506, `README.md` 357 |
| cosmai-old | **217** | **39,861** | **0.88** | `docs/decisions/DP-*` 33개 5,051줄(최대 349), `docs/agent-workflow/` 29개, `docs/p1/` 28개, `docs/open-questions/` 16개, `docs/conventions/` 8개 2,192줄 |
| Research_Paper | 5 | 1,296 | 0.10 | `docs/judgment-debt.md` 86 |
| stack | 1 | 217 | — | README |

거버넌스 문서를 건드린 커밋 수: trend-radar `AGENTS.md` 14/122, `docs/judgment-debt.md` 26/122, `scope.lock.json` 10/122, `CHANGELOG.md` 20/122;
yt-scrapper `docs/status.md` **62/219**, `CHANGELOG.md` 43/219, `AGENTS.md` 8/219; cosmai-old `docs/decisions/` 40/191, `docs/project-state.md` 34/191, `AGENTS.md` 9/191.

cosmai-old 증거 라벨 출현(docs+experiments): `[측정]` 1,054 · `[확인 사실]` 850 · `[추론]` 664 · `[결정]` 461 · `[가설]` 168; 커밋 본문에도 68회.
cosmai-old `docs/decisions/README.md` 색인 누락: DP-028~035 **8건**(색인 안의 `[측정]` "누락했다" 경고 바로 아래). `docs/open-questions/` 16개 중 OPEN 10.

## 3. 테스트

| 레포 | 수집 | 기본 실행 | 시간 | live | db/postgres 마커 | 메타테스트(레포 구조·문서·설정 검사) |
|---|---|---|---|---|---|---|
| trend-radar | 1,208 | **1,152 passed**, 45 skipped(db URL 없음), 11 deselected(live) | **2.70s** | 11 | 46 | 16파일 1,620줄 → **512개 수집**(그중 `tests/dashboard/test_queries_stay_inside_the_boundary.py` 343개; 나머지 169) |
| Research_Paper | 805 | 805 passed (+20 subtests) | **1.26s** | 0(`tool/live_smoke.py` 별도) | 0(SQLite) | `test_guards.py` 12개 |
| yt-scrapper | 636 | 632 (Docker Postgres 필요, 오늘 미실행) | — | 4 | 31 | 7파일 2,566줄 → 130개 수집(`test_documentation_is_true.py` 606줄 17함수, `test_compose.py` 425, `test_deployment_units.py` 362, `test_payload_shapes.py` 444) |
| cosmai-old apps/ | 1,199 | (`cosmai_test` DB 프로비저닝 필요, 미실행) | — | opt-in 플래그 | 전부 | 루트 `tests/environment/` 8파일 + `test_structural_fixtures.py` = 82함수 2,414줄 |

trend-radar 메타테스트 파일별: agent_context 11 · collection_scope 15 · docs_references 31 · fixtures_scrubbed 35 · readme_translation 7 · scope_declared 28 ·
sources_layer 6 · schema_derives_nothing 6 · version 4 · every_dataset 11 · dashboard boundary 343.

## 4. 훅·커밋

| 레포 | 훅 | 커밋 | Claude 공동저자 | Conventional 준수 | 제목 길이 중앙값 / 최대 / 72자 초과 |
|---|---|---|---|---|---|
| trend-radar | commit-msg · pre-commit · pre-push | 122 | 85 | 121 (훅 도입 전 2건 예외) | 58 / 72 / **0** |
| yt-scrapper | 동일 3개(commit-msg·pre-push는 바이트 동일) | 219 | 165 | 219 | 59 / 72 / **0** |
| cosmai-old | 없음 | 191 | 185 | **23** | **75 / 150 / 114** |
| Research_Paper | 없음 | 61 | 58 | 59 | 51 / 76 / 2 |
| stack | 없음 | 14 | 15(본문 trailer 중복) | 0 (한국어 서사형) | 47 / 82 / 2 |

yt-scrapper 타입 분포: fix 56 · feat 51 · docs 39 · chore 10 · test 5 · refactor 4 · perf 1. trend-radar: feat 45 · docs 37 · fix 13 · test 9 · chore 9 · refactor 3 · ci 2 · build 2.

## 5. 중복·드리프트 (오늘 확인, 코드 기준)

- cosmai-old `apps/` ↔ `experiments/integrated-p0/`: 같은 경로의 .py **68개**, 그중 바이트 동일 9, 분기 **59**. `pyproject.toml:30-34`·`scripts/check-addons.sh:24`는 여전히 experiments를 가리킴(`architect/README.md` §6 #6).
- yt-scrapper `.githooks/pre-commit:29-30` → `decisions/002-hooks-are-opt-in-so-ci-must-backstop.md`, `tool/worktree.sh:70-71` → `decisions/006-verify-the-clone.md`: 둘 다 **존재하지 않음**(`decisions/`에는 001·002·003만, 다른 제목).
- trend-radar `AGENTS.md:79` "master가 유일한 장수 브랜치" ↔ `tool/worktree.sh:22` `INTEGRATION_BRANCH:-dev`; `tool/worktree.sh:43-47` → `tool/checks/install` 없음.
- trend-radar `service-db.json:4` `"database": "trend_radar"` ↔ 실제 `app`@`shared-postgres:5432`(`stack/docker-compose.yml:152`).
- yt-scrapper `decisions/002`(SQLite 쓰기 락)는 Postgres 이관(#15) 후 조건 소멸 — `decisions/README.md:18` 표에 그대로 "활성".
- yt-scrapper `AGENTS.md:120-125`: "이 줄은 오랫동안 '도커 없음'이었고 두 결정이 그 거짓 전제 위에 있다 — 둘 다 다시 결정해야 한다"(문서 자신의 고백).
- 3롤 bootstrap 세 벌: trend-radar `tool/db/docker-init.sh` 127줄 + `tool/db/policy.py` **1,451줄**, yt-scrapper `deploy/postgres-bootstrap.sql` 153줄, stack `init/50-cosmai-bootstrap.sh` 119줄. `search_path` 전략이 yt(migrator도 스키마 포함)와 cosmai(migrator는 `pg_catalog`만)에서 다름.
- 배선 두 벌: 레포 안 compose(trend-radar `docker-compose.deploy.yml`, yt `deploy/docker-compose.yml`) + `stack/docker-compose.yml`. compose 검사 테스트는 레포 안 것만 봄 → 스택 이관 때 trend-radar 크론 3줄 누락(`architect/slice-p16-collector-reliability/README.md:46`), cosmai 스케줄 10초·page_limit off-by-one(`:37-38`).
- `.superpowers/`·`docs/superpowers/`: cosmai-old 480K+100K, Research_Paper 148K, yt-scrapper 76K. Research_Paper `docs/judgment-debt.md:4-6`이 `.superpowers/sdd/…/progress.md`를 원장으로 인용.
- 오늘 훅 거부 사례: `trend-radar-wt/review-low`에서 에이전트의 91자 제목이 `commit-msg:37-40`에 거부돼 재작성(최종 b9ffa95 제목 66자). 거부된 메시지는 git에 남지 않으므로 소유자 보고에 의존.

## 6. 운영 실측 (P16, DB 로그)

trend-radar 4소스 5,336요청: oliveyoung 99.8%, glowpick 1,056/1,057, daisomall 737/737, hwahae 82%(HTTP 500 15건); 차단 1건. tubedepth 큐 적체 232,139, 자막 08-21 05:14 이후 0.
cosmai `collector.trendradar.rest` 10초 스케줄 → 17,241회 성공·`rank_snapshot` 복제. 출처 `architect/slice-p16-collector-reliability/README.md`.
