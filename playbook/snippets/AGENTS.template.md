<!-- origin: service/trend-radar/AGENTS.md (structure) + yt-scrapper AGENTS.md:9-18 (session start) + working-agreements.md:168-184 (enforcement column)
     reuse: fill the four sections; hard cap 120 lines; every rule names what enforces it (hook | test | none). CLAUDE.md contains only `@AGENTS.md`. -->
# <project>

One paragraph: what this repository does, in words that survive the data sources being swapped out.

## Every session starts here

1. `tool/doctor.sh` — hooks, toolchain, database reachability. A clone has no hooks until `core.hooksPath` is set.
2. `NOTES.local.md` — gitignored; what the last session was in the middle of. Absent in a fresh clone, and that is fine.
3. `docs/decisions.md` — rules that were paid for, with the number they cost.

## The rules that are expensive to break

| Rule | Why (one sentence, with the failure it prevents) | Enforced by |
|---|---|---|
| Every instant is UTC; `captured_at` is the run start truncated to its bucket | a re-run of the same bucket is a no-op, not a duplicate | test |
| `parse` is pure: bytes in, records out; no network, no DB, no `now()` | every source line is tested offline against a saved fixture | conftest socket guard |
| `collectors/*` import `contracts/` only; `analysis/` imports `contracts/` + `db/` | the engine calls collectors, never the reverse | `tests/test_layering_guard.py` |
| A collector's `scope` is derived from its constants and stored on every run row | a number written twice goes stale on one side | `tests/test_scope_is_derived.py` |
| Request bounds live in `SourcePolicy`; a constant carries date, measurement, failure prevented — in one sentence | a constant with no reason is a constant the next person tidies away | none |
| A collection that stopped short must not read like one that finished | reaches the run report and the exit code | test |
| Lexicons and eval sets are versioned tables; a row change without a version bump fails | the regression set is what decides rule vs LLM | test |
| Secrets live in `~/.config/<project>/env` (0600); the repo holds key names only | `tool/with-secret-source.sh` refuses a store inside the tree | script + gitleaks hook |

## How we work here

- `main` is the only long-lived branch. `feat|fix/<name>` from `main`, merged back with `git merge --no-ff`, branch deleted after. (hook: none)
- One judgement per commit; the subject is a Conventional Commit ≤72 chars; the body says what the change prevents. (hook: commit-msg)
- Loaded rows are the definition of done: a change to what gets collected ends with a query and a number. Green tests are not it. (none — `tool/checks/data` helps)
- An exploration result goes in one of three bins in `docs/judgment-debt.md`: not done / explored and empty / decided against. (none)
- If you cannot verify something on this host, write BLOCKED in `NOTES.local.md` with what is missing. A qualified pass is not a pass. (none)
- Commit and push only when asked. Add `Co-Authored-By` trailers. (none)

## Layout

| path | what is there |
|---|---|
| `contracts/` | the whole shared interface — read first |
| `collectors/<source>/` | one source per package; `NOTES.md` beside it holds dated observations of that site |
| `analysis/` | linker · extractor · polarity · aggregate |
| `db/` | `bootstrap.sql` (3 roles per schema), migrations per schema, `service-db.json`, `checks.sql` |
| `stack/` | the only compose file, crontab, `env.example` |
| `eval/` | labeled sets and golden outputs; `tests/test_golden_files.py` compares bytes |

## Checks

`tool/checks/format`, `tool/checks/lint`, `tool/checks/test` — what the hooks and CI run. `tool/checks/contract` reaches real sites and
`tool/checks/data` reads the real database; a person runs those after a change to what gets collected.
