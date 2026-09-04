# playbook — a development-methodology catalog extracted from the 4 existing repos

**Scope**: `service/trend-radar`, `service/yt-scrapper`(tubedepth), `service/cosmai`(cosmai-old), `service/Research_Paper`(paper-radar) + `service/stack`, as of 2026-08-23, their state right before archiving.
**Principle**: only what is in the code, hooks, tests and git history is written down, not what a document claims. Every item has `path:line` evidence, and every number is in `metrics.md` together with the measurement script.
**Use**: a record of what the new `cosmai` monorepo takes and what it drops, and a list to reuse the next time an unrelated project starts.

## How to read this

- The 6 section files are the body. Each item carries **where / what / observed effect / observed cost / reuse form / grade**.
- Grade — **Adopt**: take it as is. **Adapt**: only the core, in a smaller form. **Drop**: do not take it (one-sentence reason).
- `snippets/` is the smallest artifact carrying each practice (all ≤80 lines, a two-line header: origin path / what to change on reuse). Copy it as is.
  `postgres-bootstrap.sql` has been verified in a throwaway container: run twice (idempotent), runtime DDL rejected, migrator created, and runtime DML all confirmed.
- Constraints when applying this to the new repo (owner's call): tests real and fast · keep the 3-role DB · keep eval-set regression · long docstrings, the decision-record ritual, doc-truth meta-tests, and the scope-lock original are not taken.

| File | Content | Item count |
|---|---|---|
| [01-repo-discipline.md](01-repo-discipline.md) | Hooks, Conventional Commits, worktrees, branches, CHANGELOG, gitleaks | 12 |
| [02-test-discipline.md](02-test-discipline.md) | Socket blocking, real-DB fixtures, meta-tests, golden files, layering guards, live markers | 17 |
| [03-scope-policy-locks.md](03-scope-policy-locks.md) | scope.lock, policy manifest, service-db.json, constants backed by measurement | 8 |
| [04-ops-deploy.md](04-ops-deploy.md) | systemd, compose, 3-role DB bootstrap, secrets storage, health checks, cron | 9 |
| [05-docs-decisions.md](05-docs-decisions.md) | AGENTS.md structure, decision records, judgment-debt, NOTES.local, experiment templates, comment voice | 12 |
| [06-agent-collaboration.md](06-agent-collaboration.md) | What AGENTS/CLAUDE demanded of agents versus actual compliance in git history (+A13 batching→issue→clear · A14 per-role model/effort definitions, new) | 14 |
| [metrics.md](metrics.md) | Prose/code ratio, document volume, test count · time, meta-test count, hook/commit shape, drift list | — |

## Grade summary (70 items: adopt 41 · adapt 21 · drop 8)

| Section | Adopt | Adapt | Drop |
|---|---|---|---|
| 01 repo · branch · commit | R01 commit-msg hook · R02 pre-commit→`tool/checks` · R03 pre-push · R04 exit 69 · R06 doctor.sh · R09 one judgment per commit · R11 gitleaks | R05 CI backstop · R07 worktree.sh · R08 branch model · R10 version · CHANGELOG | R12 Justfile |
| 02 tests | T01 socket blocking · T02 guard-the-guard · T03 schema per test · T04 throwaway PG + bootstrap · T05 AST layering · T06 vacuity guard · T07 golden bytes · T08 fixture scrub · T11 DDL forbidden, negative-proof of permission · T13 loaded-row check · T14 live separation | T10 config truthfulness · T12 shape lock · T15 subprocess/TLS · T17 boundary tests | T09 doc-truth meta-test · T16 acceptance-scenario md |
| 03 scope · policy | S02 scope derived, stored per run · S04 SourcePolicy + measured constants · S05 enum member = collector · S06 walk ≤ depth · S07 honest UA · S08 explicit budget meaning | S01 scope.lock · S03 service-db.json+policy.py | — |
| 04 ops · deploy | O01 3-role bootstrap · O02 idempotent init · O03 one compose file · O05 secrets outside the tree · O06 health check · O07 supercronic UTC | O04 systemd unit · O09 migration script | O08 flake.nix |
| 05 docs · decisions | D02 judgment-debt, three bins · D03 NOTES.local · D10 troubleshooting grep · D12 the "is it enforced" table | D01 AGENTS.md structure · D04 source observation notes · D05 decision record (yt-style) · D07 comment voice · D08 evidence label | D05 DP template · D06 experiment template · D09 status/plan retention · D11 translation pairs |
| 06 agent collaboration | A01 CLAUDE→AGENTS · A03 observable completion · A06 commit on request · A07 BLOCKED · A09 rules as checks · A10 convention ≠ control · A12 classify then fix/caller · A13 batching→issue→clear · A14 per-role model/effort definitions | A02 session start · A05 attacker · A08 project knowledge only · A11 session-record isolation | A04 role separation + packet |

## Recommended minimal set — 10 things to add on day one of a new project

The order is the install order. All of it is in `snippets/`.

| # | Practice | Snippet | Why first |
|---|---|---|---|
| 1 | 3 hooks + `tool/checks/{format,lint,test,prerequisite}` (R01–R04) | `commit-msg`, `pre-commit`, `pre-push`, `tool-checks/` | Compliance split 121/122 vs 23/191 depending on whether the hook existed. Define once, hooks and CI only call it |
| 2 | Socket-blocking conftest + guard-the-guard (T01, T02) | `conftest_no_network.py`, `test_conftest_guard.py` | The premise behind 1,152 tests in 2.7s and 805 tests in 1.3s. 40 lines |
| 3 | Real Postgres, schema per test, throwaway container (T03, T04) | `db_schema_per_test.py`, `tool-checks/test` | "Production runs PG, tests run SQLite" ships dialect bugs. The container is initialized with the production bootstrap SQL |
| 4 | 3-role idempotent bootstrap + runtime-DDL-rejection test (O01, O02, T11) | `postgres-bootstrap.sql`, `test_runtime_role_cannot_ddl.py` | Runtime DDL is impossible at the database level. Running it twice leaves the same state (verified today) |
| 5 | Secrets outside the tree, only names in the repo (O05, R11) | `with-secret-source.sh`, `env.example`, `gitleaks.toml` | The reason zero credentials appear across the four repos' history |
| 6 | AST layering guard + vacuity guard (T05, T06) | `test_layering_guard.py` | A prose rule was broken by 4/4 sources; moved into a test, it became 0. Assert "there was something to check" first |
| 7 | Golden/eval-set byte regression (T07) | `test_golden_files.py` | A rule↔LLM swap of the judgment is verified only by comparing the same input against the same output file. The message names the file and line number |
| 8 | scope derived and stored per run row + a collector and cron entry per enum member (S02, S05, T10) | `test_scope_is_derived.py`, `test_every_enum_member_is_collected.py`, `test_stack_commands_resolve.py`, `crontab` | Today's three P16 incidents (a missing cron entry, a 10-second schedule, an off-by-one) were all "declared but not wired" |
| 9 | AGENTS.md ≤120 lines (an enforcement mechanism per rule) + judgment-debt, three bins + NOTES.local (D01, D02, D03, D12, A09) | `AGENTS.template.md`, `judgment-debt.template.md`, `decision-entry.md` | The more documents, the more drift (cosmai-old md/code ratio 0.88, 8 missing index entries). Add checks, not rules |
| 10 | Done = a loaded row + `tool/checks/data` (A03, T13) | `data-checks.sh` | A green test was wrong 7 times, 6 of them exit 0. It's the one rule that isn't automated, which is why it's #10, not last |

**Clearly not worth adding**: the scope.lock file (S01 — the run row's scope·version answers the same question in SQL), the doc-truth meta-test (T09 — cutting the document is cheaper),
the DP decision template · task packet · role separation (D05/A04 — against 8 days · 5,051 lines · 29 documents, what the slice actually used was two collectors), translation pairs (D11), keeping finished plans (D09).

## The three biggest costs (in numbers)

1. **Prose outgrows code** — yt-scrapper `src/` prose/code ratio 0.70, `database.py` 82 lines of code : 195 lines of prose; cosmai-old 39,861 md lines / 45,319 code lines (0.88), 28/192 src files have prose ≥ code. A test file's docstring grows into an incident narrative (16–25 lines). → D07's "one sentence" rule.
2. **Document/wiring drift** — 9 instances confirmed today (`metrics.md` §5): 2 hook comments pointing at files that don't exist, an AGENTS↔script branch mismatch, a manifest database-name mismatch, a dead-letter decision record, 8 missing index entries, 59 files under an apps/experiments split, and 3 missing cron lines caused by two separate compose files (repo compose vs. stack compose). It occurred even in the repo that **has** a doc-truth test, in a place the test does not look.
3. **The ritual has no execution path** — of cosmai-old's 191 commits, 33 DPs, 29 agent-workflow documents, the code today's slice actually used is the DataLab and blog collectors; no slice uses `experiments/` (`architect/REBUILD.md` §3). With no hook, 114/191 subject lines exceeded 72 characters.
