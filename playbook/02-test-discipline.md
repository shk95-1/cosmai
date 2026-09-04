# 02 — Test discipline

Measured (`metrics.md`): trend-radar 1,152 passed / 45 skipped (db) / 11 deselected (live) in **2.7s**; Research_Paper 805 passed in **1.3s**;
yt-scrapper 632 collected (4 live, 31 postgres markers); cosmai-old `apps/` 1,199 collected. In all four repos the default run is offline,
and for the same reason — `conftest.py` blocks sockets. Fast for the same reason too — the parser is a pure function and the fixture is stored bytes.

| ID | Name | Grade |
|---|---|---|
| T01 | Socket-blocking autouse conftest (exempts the `live` marker, allows the test DB port) | Adopt |
| T02 | Guard-the-guard — a test that verifies the block actually blocks | Adopt |
| T03 | Real Postgres, schema per test (`database_url_for_tests`) | Adopt |
| T04 | `tool/checks/test` brings up a throwaway Postgres container and initializes it with the **production bootstrap SQL** | Adopt |
| T05 | AST layering guard — "source only imports downward" | Adopt |
| T06 | Vacuity guard — assert "there was something to check" first | Adopt |
| T07 | Golden-file byte identity (synthetic fixture → 5 CSVs) | Adopt |
| T08 | Fixture-scrub test — no real names or profile keys in a committed capture | Adopt |
| T09 | Doc-truth meta-tests (doc-truth, translation sync, references resolve, context files forbid a domain) | Drop |
| T10 | Config-truthfulness test (does the compose/systemd command line exist in `--help`) | Adapt |
| T11 | DDL forbidden on the boot path + negative-proof of runtime-role permissions | Adopt |
| T12 | payload shape lock — append-only, forces a bump | Adapt |
| T13 | Loaded-row check (`tool/checks/data`: hygiene / placeholder, two families) | Adopt |
| T14 | live/contract checks separated — run by a person | Adopt |
| T15 | A test that spawns a worker as a subprocess + an openssl TLS stub | Adapt |
| T16 | Acceptance-scenario markdown (`tests/acceptance/JOB-001…`) | Drop |
| T17 | Dashboard/view boundary test — inspects the compiled query object | Adapt |

---

## T01. Socket-blocking autouse conftest

- **Where**: trend-radar `tests/conftest.py:27-41` (patches three functions — connect/connect_ex/create_connection — the message carries the nodeid),
  yt-scrapper `tests/conftest.py:70-104` (only the **port** from `LOCAL_DATABASE_HOSTS` × `TUBEDEPTH_TEST_POSTGRES_URL` passes; `:37-51` gives the reason for narrowing to a port),
  Research_Paper `tests/conftest.py:26-39` (the same three patches, Korean messages), cosmai-old `tests/conftest.py:31-55` (not a block but
  skips the `network` marker unless a `--run-network` flag is passed — a weaker variant).
- **What**: a test with no `live` marker that opens a socket raises RuntimeError. The failure message carries the test's name.
- **Observed effect**: across the three repos, ~2,600 tests finish in under 5 seconds with no network. trend-radar `tests/conftest.py:8-11`:
  "a parser quietly growing a request is how this rule breaks, and without it the symptom is 'a suite that gets slow and occasionally turns red'".
  `pyproject.toml:71-78`'s `-m "not live"` is in addopts — "not a preference, load-bearing".
- **Observed cost**: a test using a DB needs an exception path. trend-radar `tests/storage/conftest.py:35-40` lifts the block entirely (allows everything)
  on the `db` marker with `monkeypatch.undo()`; yt-scrapper opens only one port. The latter is right — the former would not notice a `db` test
  reaching an actual site. yt-scrapper's comment at `:41-43`: "Task 7 widened this to 'any localhost port' and then reverted it".
- **Reuse form**: `snippets/conftest_no_network.py` (yt-scrapper's port-allow approach combined with trend-radar's three-function patch, 40 lines).
- **Grade: Adopt**.

## T02. Guard-the-guard

- **Where**: trend-radar `tests/test_conftest_guard.py:6-13` (13 lines: does it block, does the message carry the test's name),
  Research_Paper `tests/paper_radar/test_guards.py`'s `SocketGuardTest`, pointed to by `tests/conftest.py:11-12`.
- **Observed effect**: if editing conftest loses the block, these two tests go red first. Cost is close to zero.
- **Reuse form**: `snippets/test_conftest_guard.py`.
- **Grade: Adopt**.

## T03. Real Postgres, schema per test

- **Where**: yt-scrapper `tests/conftest.py:128-139` (nodeid → a 63-byte identifier, a 10-char sha1 to avoid collisions), `:142-197`
  (`DROP SCHEMA … CASCADE; CREATE SCHEMA`, carried in the URL via `options=-csearch_path=<schema>,pg_catalog`, yielded, dropped at teardown;
  the `render_as_string(hide_password=False)` trap `:187-191`). `:145-148`: "18 files that used `Database(tmp_path/…)` 59 times, now one fixture".
  cosmai-old `apps/tests/conftest.py:128-186` resets the `cosmai` schema **once per session** and re-grants default privileges
  (`:141-158`, the OID-changes-so-permissions-vanish trap). trend-radar `tests/storage/conftest.py:8-11` runs a **real migration** instead of `create_all`.
- **What**: tests connect to the same Postgres 18 as production, not SQLite or a fake. Isolation is per schema, not per database (seconds vs. milliseconds).
- **Observed effect**: yt-scrapper `tool/checks/test:18-21`: "production runs Postgres, tests run SQLite — that's how dialect bugs ship".
  `decisions/002` dealt with a lock problem from the SQLite era and became dead letter with the Postgres migration (`decisions/002…:29-33` foresaw this itself).
- **Observed cost**: needs Docker (`tool/checks/test:30` `require_command uv docker pg_isready`). cosmai-old needs the `cosmai_test` database
  pre-provisioned (`apps/tests/conftest.py:12-14`), so a manual step precedes the first run. yt-scrapper's approach needs
  `GRANT CREATE ON DATABASE` on the migrator (`tool/checks/test:61-65`, "harness-only, not put in the bootstrap file").
- **Reuse form**: `snippets/db_schema_per_test.py` (yt-scrapper's 45-line fixture, made psycopg/SQLAlchemy-neutral).
- **Grade: Adopt** — fits the new repo's "tests real and fast" constraint exactly. In a monorepo, split schema names per package with a `<pkg>_t_…` prefix.

## T04. Throwaway Postgres + the production bootstrap SQL

- **Where**: yt-scrapper `tool/checks/test:34-69` (brings up a `postgres:18-alpine` container if `TUBEDEPTH_TEST_POSTGRES_URL` is unset, pipes
  `deploy/postgres-bootstrap.sql` in as is, exports the URL, cleans up via trap). `:40-53` gives the reason for polling `pg_isready` from the host side
  (during initdb the temporary server answers ready only on the container's unix socket — an actually-hit flake).
- **Observed effect**: `deploy/postgres-bootstrap.sql:18-20`: "what CI checks is production's shape". `tests/test_postgres_privileges.py:1-17` runs
  the negative-proof of the runtime role (DDL rejected) on top of it.
- **Observed cost**: a container starts on every push (R03). On this machine, where `shared-postgres` is already up on local port 5434, only the URL needs passing.
- **Reuse form**: `snippets/tool-checks/test` (includes the container-startup section).
- **Grade: Adopt**.

## T05. AST layering guard

- **Where**: trend-radar `tests/test_sources_stay_at_their_layer.py:39-43` (declares `ALLOWED` · `INVERTED` sets by name), `:50-64` (collects imports via AST —
  catches a deferred import inside a function too, `:15-17`), `:89-96` (whether the AGENTS.md rule names the same set). Research_Paper `tests/paper_radar/test_guards.py:58-79`
  (derives the layer from the module path), `:82-103` (AST). cosmai-old `tests/environment/test_p1_isolation.py:73-84` (`apps/` forbidden from importing `experiments`),
  `test_addon_layer_direction.py`, yt-scrapper `tests/test_repository_hygiene.py:21-30` (transport is created in only two places — regex).
- **Observed effect**: trend-radar `:9-13`: "the rule was wrong the whole time it was AGENTS.md prose — it said contract·models only, but 4 sources imported registry
  and 3 imported scrub. A rule the code visibly breaks does not read as a rule". Research_Paper `test_guards.py:3-4` calls this "trend-radar playbook
  pattern 3" and ports it — already reused once, on record.
- **Observed cost**: the allowed set lives in two places, the test file and AGENTS.md (which is why trend-radar adds one more test, `:89-96`). yt-scrapper's regex version
  can be dodged by a variable name (`test_queries_stay_inside_the_boundary.py:13-15` rejects regex for the same reason).
- **Reuse form**: `snippets/test_layering_guard.py` (60 lines, only the package names and layer table need changing).
- **Grade: Adopt** — this is what enforces the new repo's directional rules: `collectors/*` → `contracts/` only, `analysis/` → `contracts/` + `db/` only.

## T06. Vacuity guard

- **Where**: trend-radar `test_sources_stay_at_their_layer.py:67-69` (`len(_modules()) == len(SOURCES)`), `test_version_is_managed.py:44-47`
  (`len(modules) > 20` — "the glob is the whole test"), `test_scope_is_declared.py:175-180` (`total >= 5`, `:17-22` tells the story of it actually passing as 0==0),
  `test_docs_references_resolve.py:64-66`, `test_fixtures_are_scrubbed.py:48-50`, `test_every_dataset_has_a_collector.py:30-34`.
- **What**: every parametrized/glob-based check first asserts "the set being checked is not empty".
- **Observed effect**: `test_scope_is_declared.py:17-22`: "once scope nested under a dataset key, three checks passed while seeing nothing" — added after actually hitting it.
- **Observed cost**: 3 lines per check. None.
- **Reuse form**: a pattern — next to every `@pytest.mark.parametrize(..., _discovered())`, add `def test_there_is_something_to_check(): assert _discovered()`.
- **Grade: Adopt**.

## T07. Golden-file byte identity

- **Where**: Research_Paper `tests/paper_radar/test_trend_golden.py:1-17` (38 synthetic OpenAlex records → 5 CSVs, compared byte for byte against the golden made by the old code),
  `:41-50` (the message names the file and line number), `tests/fixtures/make_trend_golden.py:1-24` (the regeneration procedure and a warning: "regenerating with the new code loses the regression's teeth").
  `docs/judgment-debt.md:33-48` (why synthetic data replaced the missing 303MB original, and "the cost of getting it wrong").
- **Observed effect**: even after deleting the old `papers_trend/` code (T7), the test pins down that the new `trend/` package produces the same CSVs.
  The CSV's columns/order/encoding are "a public contract a join with past output depends on" (`:10-11`).
- **Observed cost**: changing the fixture forces regenerating the golden, and at that moment the test becomes a comparison against itself (`make_trend_golden.py:14-19`).
  A golden has to be designed to "rarely need to change".
- **Reuse form**: `snippets/test_golden_files.py`. The same shape for the new repo's `eval/`: labeled_set 660 and 80 product-mapping pairs —
  compare the same input against the same output file every time a rule/LLM changes.
- **Grade: Adopt** — the implementation of the "keep eval-set regression" constraint.

## T08. Fixture-scrub test

- **Where**: trend-radar `tests/test_fixtures_are_scrubbed.py:1-16` (scrubs `profileKey` and nicknames; `:12-16` — after looking only at the reviews directory,
  61 real names turned up on the ranking page → widened to the whole fixture set), `:53-57` (checks the `SCRUBBED-` prefix). 35 parameters.
- **Observed effect**: even if scrubbing is forgotten during a fixture recapture, this catches it before the commit.
- **Observed cost**: a site-specific key name (`profileKey`) is baked into the test.
- **Reuse form**: the pattern as is (just swap the key list for the new source's). `snippets/test_fixtures_are_scrubbed.py`.
- **Grade: Adopt** — essential for the new repo, which handles review bodies.

## T09. Doc-truth meta-tests

- **Where**: yt-scrapper `tests/test_documentation_is_true.py` (606 lines, 17 test functions; slices the Korean/English pairs of README·api·status·troubleshooting·AGENTS
  by the HTML comment marker `<!-- kinds:start -->` and compares routes·kind·error codes·version, `:1-17`). trend-radar `tests/test_readme_translation_stays_in_step.py`
  (compares code spans, links, the `TREND_RADAR_*` variable set), `tests/test_docs_references_resolve.py` (`docs/*.md` references resolve, 31 parameters), `tests/test_agent_context_is_project_only.py`
  (forbids a source key or a Korean site name in AGENTS.md's rules section).
- **Observed effect**: a track record of actually forcing README fixes — yt-scrapper `:3-6`: "the README's first example called a route that never existed, and the milestone table said work hadn't even started".
  trend-radar `test_docs_references_resolve.py:3-7`: "architecture.md was missing for weeks".
- **Observed cost**: (1) 16 meta-test files, 1,620 lines (trend-radar), 7 files, 2,566 lines (yt-scrapper). (2) all it catches is a "mechanical claim", not meaning
  (`test_payload_shapes.py:25-27`). (3) yet drift confirmed today anyway: trend-radar `AGENTS.md:79` master-only vs `tool/worktree.sh:22`'s dev default;
  `service-db.json:4` database `trend_radar` vs actually `app` (`architect/README.md` §6 #1); the `decisions/002-hooks-are-opt-in…md` · `decisions/006-verify-the-clone.md`
  that yt-scrapper's `.githooks/pre-commit:30` and `tool/worktree.sh:70-71` point to do not exist (the references-resolve test exists only in trend-radar,
  and trend-radar deleted that comment from its own hook — the drift is left on the side with no test). (4) the cost of keeping two translations: README · CHANGELOG · AGENTS · api each have a `.ko.md`.
- **Grade: Drop** — owner's decision. Instead of pinning a document down with a test, cut the document itself (see 05). A one-line "the referenced file exists"
  check costs close to zero, though, and can go into 20 lines using the T06 pattern if it's ever needed.

## T10. Config-truthfulness test — compose/systemd command lines

- **Where**: yt-scrapper `tests/test_deployment_units.py:1-8` (verifies a unit file's ExecStart options against `tubedepth <sub> --help`; `:23-37` gives the reason it runs with an emptied environment),
  `tests/test_compose.py:1-13` (asks the same question of compose's `command:`, down to yaml-anchor identity, `:34-40`).
- **Observed effect**: catches offline the kind of mistake that "only shows up at reboot" — a deleted option, a wrong data directory. 425 + 362 lines.
- **Observed cost**: the operational incidents found by today's P16 — trend-radar's `review`/`review_stats`/`new_product` cron entries went missing during the `stack` migration
  (`architect/slice-p16-collector-reliability/README.md:46`), cosmai `trendradar` got a 10-second schedule (`:37`), tubedepth got a page_limit off-by-one (`:38`) —
  went uncaught **even in the repo that has these tests**. The tests only look at the in-repo compose, while the real wiring lived in `stack/docker-compose.yml`.
- **Reuse form**: since the new repo has `stack/` inside the same repo, one test is enough: "every `command:`/cron line in `stack/docker-compose.yml` and crontab
  uses only subcommands and options that exist in the CLI's `--help`" + "there is one cron line per declared dataset" (combined with S05). `snippets/test_stack_commands_resolve.py`.
- **Grade: Adapt**.

## T11. DDL forbidden on the boot path + negative-proof of permissions

- **Where**: yt-scrapper `tests/test_no_ddl_on_the_boot_path.py:1-18` (the era when `_database()` called `create_schema()` → a `duplicate column` incident),
  `:35` watches `DDL_LEADERS = ("create","alter","drop","truncate")` via a SQLAlchemy event. `tests/test_postgres_privileges.py:1-17` (connects as the runtime role and checks
  DDL is rejected). cosmai-old `apps/tests/test_migrate.py`, trend-radar `tests/storage/test_database_policy_acl.py`.
- **Observed effect**: the test proves the 3-role DB (04-O01) is a boundary the database enforces, not a convention. `docs/shared-postgres.md:411`, rule 6.
- **Observed cost**: a `postgres`-marked test needs a DB with real roles — T04 solves this.
- **Reuse form**: `snippets/test_runtime_role_cannot_ddl.py` (15 lines: attempt `CREATE TABLE` via the runtime URL → `InsufficientPrivilege`).
- **Grade: Adopt**.

## T12. payload shape lock

- **Where**: yt-scrapper `tests/test_payload_shapes.py:1-28` (fails if the model's shape changed but `schema_version` did not; the lock is append-only,
  rejecting an attempt to record a shape different from the current version), `tests/payload_shapes.json`, `Justfile:138-145`, `docs/releasing.md:29-33`.
- **Observed effect**: `:5-9` — prevents a repeat of the incident where commit 31e87bc added `published_date` without bumping the version, and a cached artifact shipped that field null.
- **Observed cost**: 444 lines. "Green" means "no unrecorded shape change", not "no bump needed" (`:25-27`) — it cannot see a change in meaning.
- **Reuse form**: in the new repo, the same discipline applies not to a raw payload but to the **version of a dictionary/eval-set table** — a test that fails
  if a row changes without bumping `entity_lexicon.version`. 40 lines is enough (`snippets/test_versioned_table_bumps.py` is only the shape).
- **Grade: Adapt**.

## T13. Loaded-row check — hygiene / placeholder

- **Where**: trend-radar `tool/checks/data:8-24` (the list of 7 incidents that were green and wrong anyway, what the two families mean, exit 69), `:40-142` (one batch of SQL —
  10 hygiene checks + 8 placeholder checks: a whole board with the same star rating, all-one-sign, a column entirely null, every product the same round number), `:152-160` (only hygiene fails the run).
  `docs/working-agreements.md:12-27` (the incident table: all 7 cases green in tests, 6 of them exit 0).
- **What**: the executable half of "it isn't done until you query the loaded rows and say a number". A person runs it after a collection.
- **Observed effect**: `NOTES.local.md:22-24`, today's entry: "tool/checks/data anomalies 0; 1 LOOK (review round-number, n=2)" — actually run on every change.
  Today's P16 analysis used the same approach (a collector-reliability table from DB logs alone, `slice-p16…/README.md:47`: "the ops meta tables are already enough").
- **Observed cost**: per-source placeholder queries pile up (163 lines). Not automatable — `working-agreements.md:178`: "✗ cannot. A person runs it".
- **Reuse form**: `snippets/data-checks.sh` (the hygiene/placeholder skeleton + the exit-code convention; write the queries for the new schema).
- **Grade: Adopt** — as a per-schema `checks.sql` under the new repo's `db/`. P16's collector-health table goes in the same file.

## T14. live/contract checks separated

- **Where**: trend-radar `tool/checks/contract:1-16` (`pytest -m live`, forbidden in CI, "a blocked source is a result, not an error"), `tests/test_live_checks_are_paced.py:1-17`
  (checks via AST that a live test doesn't build its own fetcher and instead passes through the gate — an incident of 13 unthrottled requests), yt-scrapper `Justfile:46-53` (only over a residential connection).
- **Observed effect**: the default suite is green regardless of a site's state. live is small — 11 (trend-radar), 4 (yt-scrapper).
- **Observed cost**: a live test rots too — the reason `docs/sources/` observations carry a date.
- **Reuse form**: a marker + 3 lines of `tool/checks/contract`. `pyproject` addopts `-m "not live"`.
- **Grade: Adopt**.

## T15. Subprocess worker test + a TLS stub

- **Where**: cosmai-old `apps/tests/conftest.py:430-520` (`start_worker`/`wait_for_worker`/`run_worker`; kills on timeout and puts both streams in the message),
  `apps/tests/test_outbound_transport.py:104-122` (a self-signed cert via `openssl req -x509` once per session — avoids a cryptography/trustme dependency), `:288-310` (a loopback TLS server stub).
  yt-scrapper `tests/test_deployment_units.py:23-40` (`--help` as a subprocess, `env=` swapped out wholesale).
- **Observed effect**: verifies process-boundary behavior — lease expiry, SIGINT, two workers claiming at once — with an actual process (cosmai-old `tests/acceptance/JOB-005…007`).
- **Observed cost**: a 653-line conftest. The cost of spawning a process slows the suite (1,199 collected; run time unmeasured today because it needs DB provisioning).
- **Reuse form**: the `start/wait/run` three-function pattern (`snippets/subprocess_helpers.py`, 40 lines). The TLS stub only when an outbound policy is enforced in code.
- **Grade: Adapt** — only in `collectors/youtube`, the one place the new repo keeps a job queue/worker.

## T16. Acceptance-scenario markdown

- **Where**: cosmai-old `tests/acceptance/JOB-001…SEC-004`, 16 files + `SCENARIO-TEMPLATE.md`, `docs/agent-workflow/task-packets/`, 12 files.
- **Observed cost**: two copies, the scenario document and the test code. `docs/agent-workflow/README.md:51-62`: "only one item is enforced — a PASS link, to claim a packet is ACCEPTED".
- **Grade: Drop** — the test function's name is the scenario (naming in the style of trend-radar's `tests/engine/test_report_is_honest_about_stopping_early.py`).

## T17. Dashboard/view boundary test

- **Where**: trend-radar `tests/dashboard/test_queries_stay_inside_the_boundary.py:1-16` (inspects the compiled Select object — regex loses to a variable name), 343 parameters.
  `tests/storage/test_the_schema_derives_nothing.py` (the migration-SQL side), `docs/judgment-debt.md:80-95` (a view was a blind spot for query-object inspection → the view got narrowed).
- **Observed effect**: the boundary "the collection repo does not analyze" was held by code, and thanks to that, today's split of `analysis/` into its own package was an easy call.
- **Observed cost**: the new repo's **purpose is analysis**, so this boundary doesn't exist as such. A large share of the 343 tests are this one file.
- **Grade: Adapt** — keep only the direction: `collectors/` does not aggregate, only `analysis/` does → the T05 layering guard is enough.
