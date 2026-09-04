# metrics — measured 2026-08-23

Measurement script: `snippets/measure_prose_ratio.py` (stdlib only; counts docstring lines via AST, whole-line comments via tokenize, and classifies the rest as code.
Excludes `.venv`·`node_modules`·`__pycache__`·`.superpowers`·`var`·`data`·`out`). Run: `python3 snippets/measure_prose_ratio.py trend-radar yt-scrapper cosmai Research_Paper stack`
(from `~/github_prj/Main/service/`). Test count · time is `uv run --frozen pytest` / `--collect-only` in each repo. Commit stats are `git log --format=%s|%b`.

## 1. Prose (docstring + comments) vs. code

| Repo | .py files | code lines | docstring lines | comment lines | **prose/code** | docstring/code | src (or apps) prose/code |
|---|---|---|---|---|---|---|---|
| trend-radar | 138 | 12,450 | 1,937 | 1,487 | **0.28** | 0.16 | src 0.39, tests 0.24, migrations 0.41 |
| yt-scrapper | 104 | 12,940 | 5,280 | 1,617 | **0.53** | 0.41 | src **0.70**, tests 0.44 |
| cosmai-old | 231 | 45,319 | 16,688 | 3,682 | **0.45** | 0.37 | apps 0.46, experiments 0.43, tests 0.59 |
| Research_Paper | 99 | 13,413 | 3,237 | 823 | **0.30** | 0.24 | src 0.61, tests 0.13 |

Files under src/apps/experiments with ≥20 code lines where **prose ≥ code** (same classification as `snippets/measure_prose_ratio.py`, aggregated per file):

| Repo | File count | Top examples |
|---|---|---|
| trend-radar | 3 / 30 | `src/trend_radar/scrub.py` code 26 : prose 39, `storage/repository.py` 64 : 82, `contract.py` 75 : 77 |
| yt-scrapper | 7 / 37 | `src/tubedepth/database.py` 82 : **195**, `watchlist.py` 40 : 70, `transfer.py` 78 : 136, `sources/registry.py` 55 : 91 |
| cosmai-old | 28 / 192 | `apps/addon_host/settings.py` 23 : 49, `apps/platform_core/db/connection.py` 45 : 84, `handlers/synthetic.py` 62 : 107 |
| Research_Paper | 7 / 38 | `src/paper_radar/sources/pubmed.py` 121 : 196, `sources/pubchem.py` 44 : 64 |

## 2. Markdown documents

| Repo | .md files | .md lines | **md/code** | Large documents |
|---|---|---|---|---|
| trend-radar | 20 | 4,369 | 0.35 | `docs/judgment-debt.md` 543, `CHANGELOG.md` 247, `README.md` 217, `docs/working-agreements.md` 184, `AGENTS.md` 145 |
| yt-scrapper | 21 | 8,953 | 0.69 | `docs/status.md` **1,687**, `docs/plan.md` 1,126 (a finished plan), `docs/shared-postgres.md` 695, `CHANGELOG.md` 506, `README.md` 357 |
| cosmai-old | **217** | **39,861** | **0.88** | `docs/decisions/DP-*` 33 files, 5,051 lines (max 349), `docs/agent-workflow/` 29 files, `docs/p1/` 28 files, `docs/open-questions/` 16 files, `docs/conventions/` 8 files, 2,192 lines |
| Research_Paper | 5 | 1,296 | 0.10 | `docs/judgment-debt.md` 86 |
| stack | 1 | 217 | — | README |

Commit count that touched governance documents: trend-radar `AGENTS.md` 14/122, `docs/judgment-debt.md` 26/122, `scope.lock.json` 10/122, `CHANGELOG.md` 20/122;
yt-scrapper `docs/status.md` **62/219**, `CHANGELOG.md` 43/219, `AGENTS.md` 8/219; cosmai-old `docs/decisions/` 40/191, `docs/project-state.md` 34/191, `AGENTS.md` 9/191.

cosmai-old 증거 라벨 출현(docs+experiments): `[측정]` 1,054 · `[확인 사실]` 850 · `[추론]` 664 · `[결정]` 461 · `[가설]` 168; 커밋 본문에도 68회.
cosmai-old `docs/decisions/README.md` 색인 누락: DP-028~035 **8건**(색인 안의 `[측정]` "누락했다" 경고 바로 아래). `docs/open-questions/` 16개 중 OPEN 10.

## 3. Tests

| Repo | Collected | Default run | Time | live | db/postgres markers | Meta-tests (repo structure/doc/config checks) |
|---|---|---|---|---|---|---|
| trend-radar | 1,208 | **1,152 passed**, 45 skipped (no db URL), 11 deselected (live) | **2.70s** | 11 | 46 | 16 files, 1,620 lines → **512 collected** (of those, `tests/dashboard/test_queries_stay_inside_the_boundary.py` 343; the rest 169) |
| Research_Paper | 805 | 805 passed (+20 subtests) | **1.26s** | 0 (`tool/live_smoke.py` separate) | 0 (SQLite) | `test_guards.py` 12 |
| yt-scrapper | 636 | 632 (needs Docker Postgres, not run today) | — | 4 | 31 | 7 files, 2,566 lines → 130 collected (`test_documentation_is_true.py` 606 lines, 17 functions, `test_compose.py` 425, `test_deployment_units.py` 362, `test_payload_shapes.py` 444) |
| cosmai-old apps/ | 1,199 | (needs `cosmai_test` DB provisioning, not run) | — | opt-in flag | all | root `tests/environment/` 8 files + `test_structural_fixtures.py` = 82 functions, 2,414 lines |

trend-radar meta-tests per file: agent_context 11 · collection_scope 15 · docs_references 31 · fixtures_scrubbed 35 · readme_translation 7 · scope_declared 28 ·
sources_layer 6 · schema_derives_nothing 6 · version 4 · every_dataset 11 · dashboard boundary 343.

## 4. Hooks · commits

| Repo | Hooks | Commits | Claude co-author | Conventional compliance | Subject length median / max / over 72 chars |
|---|---|---|---|---|---|
| trend-radar | commit-msg · pre-commit · pre-push | 122 | 85 | 121 (2 exceptions before the hook existed) | 58 / 72 / **0** |
| yt-scrapper | the same 3 (commit-msg · pre-push are byte-identical) | 219 | 165 | 219 | 59 / 72 / **0** |
| cosmai-old | none | 191 | 185 | **23** | **75 / 150 / 114** |
| Research_Paper | none | 61 | 58 | 59 | 51 / 76 / 2 |
| stack | none | 14 | 15 (duplicate body trailer) | 0 (Korean narrative style) | 47 / 82 / 2 |

yt-scrapper type distribution: fix 56 · feat 51 · docs 39 · chore 10 · test 5 · refactor 4 · perf 1. trend-radar: feat 45 · docs 37 · fix 13 · test 9 · chore 9 · refactor 3 · ci 2 · build 2.

## 5. Duplication · drift (confirmed today, by code)

- cosmai-old `apps/` ↔ `experiments/integrated-p0/`: **68** .py files at the same path, of which 9 are byte-identical and **59** diverge. `pyproject.toml:30-34` · `scripts/check-addons.sh:24` still point at experiments (`architect/README.md` §6 #6).
- yt-scrapper `.githooks/pre-commit:29-30` → `decisions/002-hooks-are-opt-in-so-ci-must-backstop.md`, `tool/worktree.sh:70-71` → `decisions/006-verify-the-clone.md`: neither **exists** (`decisions/` only has 001·002·003, under different titles).
- trend-radar `AGENTS.md:79` "master가 유일한 장수 브랜치" ↔ `tool/worktree.sh:22` `INTEGRATION_BRANCH:-dev`; `tool/worktree.sh:43-47` → `tool/checks/install` 없음.
- trend-radar `service-db.json:4` `"database": "trend_radar"` ↔ actually `app`@`shared-postgres:5432` (`stack/docker-compose.yml:152`).
- yt-scrapper `decisions/002`(SQLite 쓰기 락)는 Postgres 이관(#15) 후 조건 소멸 — `decisions/README.md:18` 표에 그대로 "활성".
- yt-scrapper `AGENTS.md:120-125`: "이 줄은 오랫동안 '도커 없음'이었고 두 결정이 그 거짓 전제 위에 있다 — 둘 다 다시 결정해야 한다"(문서 자신의 고백).
- Three separate 3-role bootstraps: trend-radar `tool/db/docker-init.sh` 127 lines + `tool/db/policy.py` **1,451 lines**, yt-scrapper `deploy/postgres-bootstrap.sql` 153 lines, stack `init/50-cosmai-bootstrap.sh` 119 lines. The `search_path` strategy differs between yt (migrator includes the schema too) and cosmai (migrator gets only `pg_catalog`).
- Two separate wirings: the in-repo compose (trend-radar `docker-compose.deploy.yml`, yt `deploy/docker-compose.yml`) + `stack/docker-compose.yml`. The compose-check test only looks at the in-repo one → 3 cron lines went missing for trend-radar during the migration to the stack (`architect/slice-p16-collector-reliability/README.md:46`), and cosmai got a 10-second schedule and a page_limit off-by-one (`:37-38`).
- `.superpowers/`·`docs/superpowers/`: cosmai-old 480K+100K, Research_Paper 148K, yt-scrapper 76K. Research_Paper `docs/judgment-debt.md:4-6` cites `.superpowers/sdd/…/progress.md` as its ledger.
- A hook rejection today: in `trend-radar-wt/review-low` an agent's 91-char subject was rejected by `commit-msg:37-40` and rewritten (final b9ffa95 subject 66 chars). Rejected messages leave no trace in git, so this relies on the owner's report.

## 6. Operational measurements (P16, DB logs)

trend-radar, 4 sources, 5,336 requests: oliveyoung 99.8%, glowpick 1,056/1,057, daisomall 737/737, hwahae 82% (HTTP 500 x15); 1 block. tubedepth queue backlog 232,139, captions 0 since 08-21 05:14.
cosmai `collector.trendradar.rest` 10-second schedule → 17,241 successful runs, `rank_snapshot` duplicated. Source: `architect/slice-p16-collector-reliability/README.md`.
