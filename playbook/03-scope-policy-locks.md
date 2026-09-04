# 03 — Scope · policy locks

trend-radar's core observation is that "if a single number quietly changes, the time series' meaning changes" (`tests/test_collection_scope_is_recorded.py:3-8`:
page size 20→50, 1 page→2 pages made reviews-per-hour go 400→1,000, and six weeks later only `git log` could answer why). Every lock in this section descends from
that one incident, and while the effect is clear, so is the cost this section concentrates.

| ID | Name | Grade |
|---|---|---|
| S01 | `scope.lock.json` + tying it to CHANGELOG `Unreleased` + a base-ref comparison | Adapt |
| S02 | scope is derived from a constant, stored on every run row | Adopt |
| S03 | `service-db.json` manifest + a connection-budget arithmetic test + `policy.py` render/audit | Adapt |
| S04 | `SourcePolicy` is the sole request-control contract, with a measured reason beside each constant | Adopt |
| S05 | A collector exists for every enum member ("a gap that looks like a feature") | Adopt |
| S06 | Page walks stay within policy depth; a silent truncation shows in the report and exit code | Adopt |
| S07 | Honest User-Agent test | Adopt |
| S08 | Budget tracker — the policy declares what a header means (`budget_is_daily`) | Adopt |

---

## S01. `scope.lock.json` + tying it to CHANGELOG + a base-ref comparison

- **Where**: trend-radar `scope.lock.json:2-14` (the file explains itself: dataset key, `changed_in`), `tests/test_collection_scope_is_recorded.py:70-107`
  (registry↔lock must match; `changed_in` is a release heading or `Unreleased`, and if `Unreleased` the source's name must be in the CHANGELOG's Unreleased section), `:110-172`
  (**compares against the committed lock** — HEAD or CI's `SCOPE_LOCK_BASE_REF`; changing both sides together still fails), `.github/workflows/checks.yml:101-133` (computes the base ref).
  `AGENTS.md:98-109`.
- **Observed effect**: today's b9ffa95 (`review_low`) could not have passed without the lock — a new dataset must record its scope under its own name in the lock
  (`AGENTS.md:101-103`), and the commit body's "scope records it under its own name so the two walks' row counts cannot be confused" is a product of this test.
  The 1.1.0 release commit ba11c24's body, "Stops the ingredients scope from identifying itself as Unreleased", is a sentence this pairing produced too.
- **Observed cost**: (1) changing one number touches three files (the source constant, the lock, the CHANGELOG) + a `changed_in` update at release. (2) 33 lines of yaml for CI to compute the base ref.
  (3) impossible in a repo with no CHANGELOG (Research_Paper's decision) — the test finds the `## Unreleased` string via `index()` (`:64`). (4) it cannot see a change in meaning
  (`:21-24`, which is why `docs/sources/<key>.md` is still needed).
- **Reuse form**: the lock file is not taken. Instead, only S02 (scope stored on the row) + the "scope is derived from a constant" test (`snippets/test_scope_is_derived.py`).
  The **record** of a scope change is already made by the run row's `scope` jsonb and `collector_version` — a retroactive question ("how many pages did it walk back then") is answered in SQL.
- **Grade: Adapt** — owner's call (scope-lock is not taken as is). What's lost: the enforcement that "a person writes a sentence before changing this". Left instead to the commit body (R09).

## S02. scope is derived from a constant, stored on every run row

- **Where**: trend-radar `src/trend_radar/contract.py:139-145` (a comment: why store this on every run), `AGENTS.md:25-28` ("a number written twice, one copy rots"),
  `tests/test_scope_is_declared.py:131-172` (a `CONSTANT_FOR` table checks scope value == the module constant), `:57-65` (the scope key set == the datasets set),
  `tool/checks/data:68-73` (a run collected with no spec is a hygiene violation).
- **Observed effect**: today's P16 built a per-source success-rate · p50 · p90 table using only `trend_radar.run` (140 rows) and `fetch_log` (5,336 rows)
  (`architect/slice-p16-collector-reliability/README.md:7,12-19`). Possible because a row carries its own provenance.
- **Observed cost**: type gymnastics — the `ClassVar` declaration rule (`AGENTS.md:19-23`) and nested `MappingProxyType`. 180 lines of test.
- **Reuse form**: in `contracts/run.md`, as a contract: "a run row has `collector_version`, `schema_revision`, `scope(jsonb)`";
  `snippets/test_scope_is_derived.py`, 30 lines.
- **Grade: Adopt** — this is exactly the run/fetch_log shape entry in the new repo's `contracts/`.

## S03. `service-db.json` manifest + budget arithmetic + `policy.py`

- **Where**: trend-radar `service-db.json` (41 lines: 4 roles, a connection-budget breakdown, session defaults), `tests/test_service_database_manifest.py:32-46`
  (`instances × (pool+overflow) + workers + migration + spare == total`), `tool/db/policy.py:1-7` (the manifest is the sole source, GRANT SQL is generated),
  **1,451 lines**. yt-scrapper `service-db.json` (48 lines: 3 roles, an external object-storage declaration `:12-19`, budget 32), `deploy/tubedepth-worker.service:52-59`
  (`2C + 13 = 25 at C=6`, inside 32). `docs/shared-postgres.md` rule 4 (`:313`).
- **Observed effect**: the connection budget closes by arithmetic, not by guessing. On today's stack, with 14 containers attached to one Postgres, `max_connections` was never exceeded.
- **Observed cost**: (1) `policy.py`'s 1,451 lines put render, audit, and extraction dry-run all in one file, and the new repo would never use all of it. (2) manifest drift: `service-db.json:4`
  says `"database": "trend_radar"`, but the real deployment is `app` (`stack/docker-compose.yml:152`, `architect/README.md` §6 #1) — the manifest is "the sole source" and it's wrong.
  (3) the same information appears again in the `postgres-bootstrap.sql:144-153` comments and in unit-file comments.
- **Reuse form**: `snippets/service-db.json` (one schema = one entry, 3 roles, a budget breakdown) + `snippets/test_connection_budget_adds_up.py` (12 lines).
  No SQL generator — the bootstrap SQL is written directly (04-O01), with one grep to check it matches the manifest's values.
- **Grade: Adapt** — **one** manifest for the whole monorepo (4 schemas × 3 roles × budget), one test.

## S04. `SourcePolicy` — the sole request-control contract, a measured reason beside each constant

- **Where**: trend-radar `src/trend_radar/contract.py:105-136` (interval/concurrency/timeout/attempts/depth/budget + `__post_init__` validation),
  `AGENTS.md:37-41` ("comes from measured response size, latency, and the completion window; changing it needs a new measurement and one sentence on what operational failure it prevents"),
  `src/trend_radar/sources/oliveyoung.py:74-78` ("50 is this endpoint's max. Measured SUCCESS + empty list at 60·70·75·80·100. Do not raise it without re-measuring"),
  Research_Paper `src/paper_radar/contract.py:41-56` (`max_attempts=5` measured at a Retry-After of 39–40s; a measured conflict in what `budget_is_daily`'s header means),
  `docs/sources/oliveyoung.md:28-30` (the evidence for `min_interval_s=5.0, concurrency=1`).
- **Observed effect**: today's P16 — 1 block out of 5,336 requests across trend-radar's 4 sources (`slice-p16…/README.md:12-17`). The counter-case is in the same table:
  tubedepth has no fan-out cap and its queue diverged to 232k (`:26-28`). The gap between a collector with a policy in its contract and one without shows up as a number.
- **Observed cost**: 2–5 comment lines per constant. `AGENTS.md:114-115`: "a constant with no reason is a constant the next person cleans up" — the most convincing comment rule in this repo,
  yet in yt-scrapper's `src/` it swelled to a prose/code ratio of 0.70 (`metrics.md`).
- **Reuse form**: a SourcePolicy field table in `contracts/collector.md` + the rule "one sentence beside a constant: measurement date, value, the failure it prevents". The new repo's
  `collectors/youtube` fan-out cap is the first place to apply it.
- **Grade: Adopt** — the comment is capped at **one sentence** (05-D07).

## S05. A collector exists for every enum member

- **Where**: trend-radar `tests/test_every_dataset_has_a_collector.py:1-19` (`NEW_PRODUCT` · `PRODUCT` — "a dataset that exists with 0 rows", hit twice),
  `:37-45` (every member has a source that collects it), `:48-62` (a source that claims to collect also provides a seed), `docs/judgment-debt.md:23-43` (the story of PRODUCT being deleted, then coming back as issue #21).
- **Observed effect**: 62 lines prevent paying to investigate this twice. Today's P16 "missing cron" (`review`/`review_stats`/`new_product` at 0 since 08-21) is the same shape
  of incident hitting the **wiring layer** — present in the enum, absent from crontab.
- **Reuse form**: `snippets/test_every_enum_member_is_collected.py` + the T10 variant (the cron line exists) covers the wiring too.
- **Grade: Adopt**.

## S06. Page walks stay within policy depth; a silent truncation shows in the report

- **Where**: trend-radar `tests/sources/test_page_walks_fit_their_policy.py:1-12` (3 pages declared + max_depth 2 → the third page is never even requested and the run is still ok),
  `tests/test_scope_is_declared.py:101-115` (the same check from the scope side), `AGENTS.md:68-72` ("No silent truncation… hit twice"),
  `tests/engine/test_report_is_honest_about_stopping_early.py`, `tests/engine/test_a_run_records_only_the_scope_it_walked.py`.
- **Observed effect**: today's b9ffa95 body states it changed `max_depth` to "the deeper of the two walks" — without this test, 4 of `review_low`'s 6 pages would have been silently dropped.
- **Reuse form**: two rule lines (the contract) + a 10-line "declared depth ≤ policy depth" test per collector.
- **Grade: Adopt**.

## S07. Honest User-Agent

- **Where**: trend-radar `tests/test_user_agent_is_honest.py:1-15` (a Chrome UA gets 403; curl / empty UA / Firefox get 200 — measured 2026-08-19), `:25-47`
  (browser tokens forbidden, `trend-radar/<version>` + a GitHub URL).
- **Observed effect**: catches both the policy ("no spoofing") and the bug (a WAF 403) in one test. 47 lines.
- **Grade: Adopt** — one line for the UA format in the new repo's `contracts/collector.md`, plus the test as is.

## S08. Budget tracker — the policy declares what a header means

- **Where**: Research_Paper `src/paper_radar/contract.py:47-56` (`x-ratelimit-remaining` means daily for OpenAlex but per-second for NCBI — indistinguishable by name alone,
  호스트명 분기 대신 정책 필드), `tests/paper_radar/test_budget.py`, git 로그 `f3b06fa fix(paper_radar): NCBI 초당 레이트리밋을 일일 예산으로 오독하던 경고 제거`.
- **Observed effect**: prevented an operator from learning to ignore warnings because of a false-positive one (`:52-55`).
- **Grade: Adopt** — just the pattern: "the policy declares it, don't branch on the hostname".
