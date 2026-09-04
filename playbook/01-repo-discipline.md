# 01 — Repo · branch · commit discipline

Of the four repos, only trend-radar and yt-scrapper have hooks, and the two repos' `.githooks/commit-msg` · `pre-push` are
byte-identical (a copy). cosmai-old and Research_Paper have no hooks. That difference shows up directly in the commit history
(see `metrics.md`): the two repos with hooks have Conventional Commit compliance of 121/122 and 219/219, 0 subjects over 72 chars;
cosmai-old, with no hook, has 23/191, 114 subjects over 72 chars, longest 150.

| ID | Name | Grade |
|---|---|---|
| R01 | commit-msg hook — Conventional Commits + a 72-char cap | Adopt |
| R02 | pre-commit = format → lint → gitleaks, defined in one place, `tool/checks/*` | Adopt |
| R03 | pre-push = tests, skipped for a delete push | Adopt |
| R04 | exit 69 "cannot verify" ≠ exit 1 "broken" (`tool/checks/prerequisite`) | Adopt |
| R05 | CI is a backstop running the same scripts as the hooks, plus a "did the DB tests actually run" guard | Adapt |
| R06 | `tool/doctor.sh` — the first command of a session; an unset hooksPath fails, not warns | Adopt |
| R07 | `tool/worktree.sh` — one worktree per branch in the sibling directory `<repo>-wt/` | Adapt |
| R08 | Branch model: one long-lived branch, short branches, `--no-ff` merges | Adapt |
| R09 | One judgment per commit; the body says "what this prevents" | Adopt |
| R10 | Single source of truth for version + CHANGELOG `Unreleased` + a version test | Adapt |
| R11 | `.gitleaks.toml` — extends the default rules + the project's own secret patterns | Adopt |
| R12 | Justfile — a command catalog that delegates the real work to `tool/checks/*` | Drop |

---

## R01. commit-msg hook — Conventional Commits + a 72-char cap

- **Where**: trend-radar `.githooks/commit-msg:1-40`, yt-scrapper the same file (no diff).
- **What**: rejects the subject unless it matches `<type>(<scope>)!: <desc>`; `Merge `/`Revert `/`fixup!` pass through (`:11-13`),
  over 72 chars is rejected (`:37-40`). The type list is a fixed 11 (`:15`).
- **Observed effect**: repos with the hook match the pattern in 121 of 122 commits (the 1 exception predates the hook), 219/219. Subject-length median 58·59,
  max 72 — sitting exactly at the cap (measured from `git log`, `metrics.md`). `git log --oneline` reads by type, and
  `sed 's/^\([a-z]*\).*/\1/' | sort | uniq -c` produces a distribution like feat 45 / docs 37 / fix 13 directly.
- **Observed cost**: today, in `trend-radar-wt/review-low`, an agent's 91-char subject was reported rejected by this hook and rewritten
  (the final commit b9ffa95's subject is 66 chars). A rejected message leaves no trace in git and cannot be reproduced — a hook's cost always
  piles up somewhere invisible like this. A Korean subject is fine since it counts characters, not bytes (`${#subject}`).
  A narrative subject in the style of cosmai-old's "Record the owner's GO on the batch: dev goes to main as v0.1.0" is rejected outright by this hook.
- **Reuse form**: `snippets/commit-msg` (copy as is; needs `core.hooksPath .githooks` set).
- **Grade: Adopt** — stack-independent, 40 lines, the effect is measured directly from history.

## R02. pre-commit = format → lint → gitleaks, defined in one place

- **Where**: trend-radar `.githooks/pre-commit:19-27` (`run_check` runs `tool/checks/format` · `lint`, skipped if absent),
  `:34-43` gitleaks. yt-scrapper `Justfile:9-13` ("if the Justfile inlines the command, that's a fourth definition").
- **What**: the hooks, CI, and Justfile all call the `tool/checks/<name>` scripts. "Clean" is defined in exactly one script.
  `tool/checks/format` is `ruff format --check` (`:13`), `tool/checks/lint` is `ruff check` + `basedpyright` (`:18-19`).
- **Observed effect**: trend-radar `.github/workflows/checks.yml:135-145` calls the same three scripts in the same order —
  the CI yml has not a single command of its own. yt-scrapper's `tool/checks/*` is copied from trend-radar, differing only in `prerequisite`.
- **Observed cost**: yt-scrapper `.githooks/pre-commit:29-30` points at `decisions/002-hooks-are-opt-in-so-ci-must-backstop.md`,
  but that file does not exist (`decisions/` has 001·002·003 under different names). A copied hook carried the dangling reference along with it,
  and trend-radar built `tests/test_docs_references_resolve.py` to catch this (`:5-7` tells the story). It is not the hook's own cost but
  the cost of the habit of "writing a document path in a hook comment".
- **Reuse form**: `snippets/pre-commit` + `snippets/tool-checks/{format,lint,test}`.
- **Grade: Adopt** — as is, minus the document-path references in the comments.

## R03. pre-push = tests, skipped for a delete push

- **Where**: trend-radar `.githooks/pre-push:28-40` (reads the ref from stdin; if the local sha is all zeros it's judged a delete push).
- **What**: runs `tool/checks/test` if present and blocks the push on failure. A push that only deletes a branch skips the tests.
- **Observed effect**: no `--no-verify` is needed when cleaning up a branch after a merge (the reason given at `:20-24`). `tool/checks/test:18-19` runs
  `uv sync --frozen` then pytest — the lock file is under test too.
- **Observed cost**: in yt-scrapper, `tool/checks/test:34-69` brings up Postgres via Docker, so every push starts a container
  (`docker run … postgres:18-alpine`, `pg_isready` polling for 60s). The hook takes tens of seconds. trend-radar takes 2.7s.
- **Reuse form**: `snippets/pre-push`.
- **Grade: Adopt** — the new repo's tests use a "real DB", so paying this once before a push is the right cost.

## R04. exit 69 "cannot verify" ≠ exit 1 "broken"

- **Where**: trend-radar `tool/checks/prerequisite:12-27`. `require_command` exits 69 when a tool is missing, or 1 if `REQUIRE_NATIVE=1`.
  CI `checks.yml:20-29` sets `REQUIRE_NATIVE: 1`. `tool/checks/data:29-33` also exits 69 with no DB URL.
- **What**: distinguishes "this host cannot run this check" from "ran the check and it found a problem" by exit code. If the former blocks a push,
  people learn `--no-verify` (`:3-7`).
- **Observed effect**: yt-scrapper `docs/definition-of-done.md:46-47` names this behavior as an M0 done item. In CI, a failed uv install
  does not turn into a silent green (`checks.yml:27-28`: "this project's signature bug is a green that means nothing").
- **Observed cost**: close to none. 27 lines.
- **Reuse form**: `snippets/tool-checks/prerequisite`.
- **Grade: Adopt**.

## R05. CI is the hooks' backstop — the same scripts, plus a "did it actually run" guard

- **Where**: trend-radar `.github/workflows/checks.yml`. The key parts: `:1-8` (hooks are opt-in, so CI must exist), `:67-79`
  gitleaks **before** installing dependencies (to avoid scanning a 40MB `.venv`), `:101-133` computes the base ref for the scope.lock comparison, `:147-165`
  greps the `-rs` output for "did the db tests actually run".
- **What**: the same 3 `tool/checks/*` as the hooks + a Postgres service container + skip detection.
- **Observed effect**: `:54-57` "32 db tests silently skip with no URL" — today's local run actually skipped 45 of them with
  `TREND_RADAR_TEST_DATABASE_URL is not set` (`metrics.md`). Without this guard, CI's green would be 45 tests short.
- **Observed cost**: of 165 lines, only 20 are yaml commands, the rest are comments. The scope.lock base-ref computation (`:101-133`) is entirely unneeded if scope.lock is not taken.
- **Reuse form**: `snippets/checks.yml` (drop the scope.lock step, keep the skip detection).
- **Grade: Adapt** — take only the skip detection and "reuse the scripts" idea. The new repo hasn't decided whether it uses GitHub Actions,
  so yt-scrapper's approach of `tool/checks/test` bringing up a container locally (R03) stands in for CI for now.

## R06. `tool/doctor.sh` — the first command of a session

- **Where**: yt-scrapper `tool/doctor.sh:24-30` (the hooksPath check fails, not warns), `:45-71` (DB URL parsing + `pg_isready`),
  `AGENTS.md:11-14` ("Do not skip it").
- **What**: checks, at session start, things like hook activation, uv, DB reachability, filesystem (drvfs) — the kind of thing that later blows up somewhere unrelated.
- **Observed effect**: `:6-10` "a fresh clone has no hooksPath, so the first commit silently skips format, lint, and secret scanning" —
  trend-radar has no doctor script; today's `trend-radar-wt/review-low` worktree had working hooks thanks to sharing `.git`, but
  a fresh clone would not.
- **Observed cost**: half of the 110 lines are WSL/SQLite history comments (`:73-80`). Host-specific trivia gets baked into the script.
- **Reuse form**: `snippets/doctor.sh` (only the 4 checks: hooksPath · uv · docker · DB).
- **Grade: Adopt**.

## R07. `tool/worktree.sh` — a worktree in a sibling directory

- **Where**: trend-radar `tool/worktree.sh:10-15` (the reason it's built in a sibling `<repo>-wt/` instead of inside the repo: duplicate file watchers/LSPs),
  `:37-38` (a `kind/name` branch off `origin/<integration>`), `:55-62` (list prints "a shared resource can't be parallelized").
- **Observed effect**: today, `feat/review-low` work proceeded in `service/trend-radar-wt/review-low` without touching the original checkout
  (commit b9ffa95). Because `.git` is shared, hooks still work (`:14-15`).
- **Observed cost**: (1) `:22` `integration=${INTEGRATION_BRANCH:-dev}` — but trend-radar `AGENTS.md:79` says "`master` is
  the only long-lived branch". The script was copied from yt-scrapper (master←dev) and its default disagrees with this repo's own rule.
  (2) `:43-47` calls `tool/checks/install`, but that file exists in neither repo (`architect/README.md` §6 #8).
  (3) `service/yt-scrapper-wt/` is left as an empty directory (no registered worktree).
- **Reuse form**: `snippets/worktree.sh` (drop the install call, default the integration branch to `main`).
- **Grade: Adapt** — the new repo is a monorepo, so parallel sessions per slice will be more frequent. Only the default branch needs fixing.

## R08. Branch model — one long-lived branch, short branches, `--no-ff`

- **Where**: trend-radar `AGENTS.md:79-85` (only master is long-lived, `--no-ff`, delete after merge, the reason fast-forward is forbidden).
  yt-scrapper `AGENTS.md:104-106` (master ← dev ← feature/fix). cosmai-old `docs/branching.md:8-14` (area/what → dev → main,
  main only at gates), `:31` ("no squash, no rebase, no fast-forward").
- **Observed effect**: in all three repos a `Merge`/`chore: merge` commit marks the boundary — in trend-radar's `git log`, the 5 commits under
  `chore: merge oliveyoung-ingredients (#22)` read as one unit. A revert is one merge commit.
- **Observed cost**: cosmai-old's two-stage integration (dev→main) itself asks "is this a ritual?" (`branching.md:19-21`). yt-scrapper's
  `dev` has no role beyond the release merge `Merge dev into master for v1.3.0`. In a single-worker-plus-agent setting, an intermediate integration branch leaves only cost behind.
- **Reuse form**: one AGENTS.md paragraph (`snippets/AGENTS.template.md` §How we work).
- **Grade: Adapt** — one `main` + `feat|fix/<name>` + `--no-ff`. No `dev`.

## R09. One judgment per commit; the body says "what this prevents"

- **Where**: trend-radar `AGENTS.md:111-112`, `docs/working-agreements.md:134-140` ("the diff says what was done. The body says why it must be this shape and
  what would break in another shape"; use `git commit -F` when there's a double quote).
- **Observed effect**: trend-radar ba11c24's body ("Minor, not patch: … Stops the ingredients scope from identifying itself as Unreleased") is
  the example. Today's b9ffa95 body, 4 paragraphs, wrote down why `review_low` walks differently from `review` and why the page cap rides on the request,
  and became the evidence for change #1 in `architect/REBUILD.md` §3.
- **Observed cost**: not enforceable (`working-agreements.md:184`: "commit content ✗"). An agent-written body tends to grow — yt-scrapper
  165/219, cosmai-old 185/191 are Claude co-authored, and with no hook cosmai-old's subject-length median is 75 chars, body content leaking into the subject.
- **Reuse form**: two lines in AGENTS.md + the R01 hook keeps the subject length in check.
- **Grade: Adopt**.

## R10. Single source of truth for version + CHANGELOG `Unreleased` + a version test

- **Where**: trend-radar `tests/test_version_is_managed.py:1-10` (pyproject is the source, the CLI and CHANGELOG derive from it), `:34-47` (detects hardcoding via AST,
  a `len(modules) > 20` vacuity guard). `CHANGELOG.md:3-5` ("the why lives in the commit message, the decision in judgment-debt, this file is neither").
  yt-scrapper `docs/releasing.md:3-6` (one place, `__init__.py`), `CHANGELOG.md:9-17` (the package version and the `/v1` contract version are separate).
- **Observed effect**: a release is one `chore(release): prepare 1.1.0` commit. Every row carries a `collector_version` that traces back to a CHANGELOG heading
  (`test_collection_scope_is_recorded.py:89-96`).
- **Observed cost**: Research_Paper `docs/judgment-debt.md:22` decided **not** to build a CHANGELOG — "git history and the ledger already play that role,
  two sources only risk drifting apart". A real drift: yt-scrapper's `CHANGELOG.md` runs 506 lines, doubled by a `.ko.md` translation. In a repo with no external consumer,
  the `Unreleased` section becomes a hostage of the scope.lock test (S01).
- **Reuse form**: `snippets/test_version_is_managed.py` (only the AST hardcoding detection).
- **Grade: Adapt** — the version lives in pyproject alone, plus a test. No CHANGELOG until there's an external consumer (a PostgREST schema consumer, say).
  Leaving `collector_version` on each row (S02) stays.

## R11. `.gitleaks.toml` — extend the default rules + the project's own patterns

- **Where**: yt-scrapper `.gitleaks.toml:11-22` (`useDefault = true` + a WireGuard key regex + a fixture allowlist). Hook `pre-commit:34-43`,
  CI `checks.yml:67-79` (scans the full history, downloads the binary directly — gitleaks-action needs an org license).
  `tests/test_repository_hygiene.py:26-30` looks at the same patterns again as a test (a signed googlevideo URL, a WireGuard key).
- **Observed effect**: yt-scrapper `AGENTS.md:53-57`: "put an expiring URL in a fixture and gitleaks reads it as a credential" — a case where the hook actually caught something.
- **Observed cost**: the hook, the test, and CI hold the same regex three times over (`test_repository_hygiene.py:29` ≡ `.gitleaks.toml:17`).
- **Reuse form**: `snippets/gitleaks.toml` (the default extension + the new repo's `COSMA_SRC_*=` value pattern).
- **Grade: Adopt** — the hook layer only. The duplicate test is not taken.

## R12. Justfile — a command catalog

- **Where**: yt-scrapper `Justfile:16-44` (doctor/check/format/lint/test all delegate to `tool/checks/*`), `:133-150` (fixture capture),
  `:161-166` (`update-ytdlp`).
- **Observed effect**: `just --list` acts as a runbook (`definition-of-done.md:49`). A comment saying "why this flag" sits next to the recipe (`:93-104`).
- **Observed cost**: one more tool (just). `:77-82`: "there used to be a `dev` recipe promising `serve --with-worker`, but that option never existed" —
  a recipe rots too. trend-radar does the same job with just `tool/checks/*`, no Justfile.
- **Grade: Drop** — a `tool/checks/*` shell script + one README table is enough. Revisit if a catalog is ever needed.
