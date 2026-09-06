# Entrypoint contract

## Collectors
```
cosmai collect <collector> --dataset <dataset> [--board <board>] [--since <date>]
  collector ∈ {commerce, youtube, naver}
  commerce datasets: ranking | product | review | review_stats | new_product | review_low
  youtube  datasets: watch | work | flatten | prune  (the meaning of the old tubedepth commands is kept)
  naver    datasets: datalab | blog   (no source-row model -- not inherited from cosmai-old; the sources are needs.naver_* (004))
exit codes: 0 ok · 1 partial (some failed or were truncated) · 2 blocked (blocked/refused)   <- the trend-radar observation convention as it stands
cosmai login --source <source>
  Refused with exit code 2 when <source> is not in the registry or is not a browser transport (Transport.BROWSER).
  It opens a real window with headless=False and **runs on the host, from the repository root** (not inside a
  container -- WSL2 shows the window through WSLg, and a cwd other than the repository root is refused with exit
  code 2). That is why that cwd resolves to the same directory as the `COMMERCE_BROWSER_PROFILE_DIR` default in
  `stack/docker-compose.yml` (#27). With no Chromium on the host, run `uv run playwright install chromium` once.
```
- A collector writes **only to tables in its own schema** (`ddl/current`). Reading another schema goes through the reader role alone.
- The UA of the HTTP transport (`DEFAULT_UA` in `collectors/commerce/contract.py`) is **the name by which we identify ourselves**
  — not an imitation of a browser, and not a value chosen to buy passage. A test nails the value down as a literal.
- **The image base changes the TLS fingerprint, so it decides whether collection succeeds.** Before reading a
  challenge as UA, rate or IP, look at the base: with the same code and the same UA the host passed while the
  container was blocked (2026-08-25 oliveyoung), and what parted them was the base image's OpenSSL — that is,
  the ClientHello fingerprint (JA3/JA4). So the base in `stack/Dockerfile` is not a packaging preference but a
  **collection input**, and the build checks that floor inside the image
  (`tests/stack/test_image_tls_stack.py`). Impersonating a browser (fingerprint spoofing) is out of scope.
- The sample design is counted in constants and recorded in `collectors/<c>/scope.json` (a variant of scope.lock: one file, no CHANGELOG obligation, and the test checks only that constants and file agree).
- **One source is walked by one run at a time.** The rate policy is enforced inside a process alone (each
  collector has its own gate), so two overlapping cron lines hit the same site at twice the policy. If another
  run is already walking that source, **that source alone is skipped**, a reason is recorded, and the run ends
  as partial (**1**) — it does not wait (waiting piles up an hourly queue).
  **This is not blocked (2)**: the site did not refuse, we yielded, and the skipped source is taken by the next
  run as it is (every write is a natural-key upsert). commerce implements it as a per-source session-scoped
  advisory lock (`collectors/commerce/storage/locks.py`).

**naver over HTTP (#182).** The transport calls **NAVER Cloud Platform's NAVER API Hub**
(`https://naverapihub.apigw.ntruss.com`): `GET /search/v1/blog` for blog search and
`POST /search-trend/v1/search` for the DataLab search trend, both authenticated with the one
`contracts/secrets.md` key pair in the headers `X-NCP-APIGW-API-KEY-ID` and `X-NCP-APIGW-API-KEY`.
An error body arrives in one of two shapes and `errorCode` is read from either: the gateway's, which
nests it (`{"error": {"errorCode": "200", …}}` on a refused key), and the search service's, which
puts it at the top level beside an `errorMessage` (`{"errorCode": "SE03", …}`); the messages beside
it are never read, in either shape.

One run may send `collectors/naver/scope.json`'s `http.max_requests_per_run` (200) requests, and
**every attempt is charged, a retry included** — a retry is another request NAVER serves. A 5xx or a timeout is retried `retry_max_attempts` (3) times
with `retry_backoff_s` (2.0) doubling (2s, then 4s), under a `timeout_s` of 20.0. What a response
does to the run, in the exit codes above:
- **401 / 403** — the credential is refused: the run stops **blocked (2)**, the note carrying the
  status and the vendor's `errorCode` alone (never its `errorMessage`, which echoes the request).
- **429, or a spent budget** — the run stops with what it has: **partial (1)** when it wrote rows,
  **blocked (2)** when it wrote none. Partial means we yielded, blocked means we were refused, and
  `needs.pipeline_health` reads partial as "it ran".
- **any other 4xx** — that one request failed and the run goes on, ending partial if anything failed.
- **a blog `start` past 1000** — never requested (#110): the ceiling is the vendor's own and
  independent of `total`, so the walk stops there rather than spending budget on an error.

Two gaps this transport still has, recorded here because a reader of `collector_health` would
otherwise mis-read the numbers: a **charged retry writes no `naver_fetch_log` row** today (one row
per request, with a hard-coded 200 and no `elapsed_ms`), so `requests` under-reports and `p90_ms`
stays NULL (#182 M1); and `collector_health` buckets **403 and 429** as blocked, so NAVER's **401**
for a refused key lands in `failed` beside `naver_run.status = blocked` (#182 M2 — the view is a
production object, so the exception is written here rather than silently widened).

**The DataLab anchor (#90).** Every DataLab request carries the one global anchor keyword
`collectors/naver/scope.py:DATALAB_ANCHOR` (`기준_세럼`) as its own `keywordGroups` entry, so at most
`DATALAB_CATEGORY_GROUPS_PER_REQUEST` (4) of a category's own groups ride beside it — the vendor's
cap counts the anchor as one of its five groups. A category of N groups therefore costs `ceil(N / 4)`
requests where it cost `ceil(N / 5)` before (today's `keywords.json`: 2 requests a run, against the
API Hub ceiling of 50,000 search-trend calls a **month** — the search APIs get 775,000 a month, and
one key is capped at 50 RPS: `guide.ncloud-docs.com/docs/apihub-overview`, read 2026-09-06). The collector stores the raw `ratio` and the request
boundary only; the value relative to the anchor is the view `db/views/naver_datalab_rescaled.sql`
(`contracts/formats.md`, NAVER DataLab section), so changing the anchor never means collecting again.

## DB connection knobs (not secrets)
```
COSMAI_DB_HOST   default 127.0.0.1
COSMAI_DB_PORT   default 5434
```
- On the host, `uv run cosmai ...` reaches the database through the published port of
  cosmai-postgres (127.0.0.1:5434); inside the compose network the service name on 5432 reaches
  the **same DB**. Only the host and the port move.
- compose passes a `${VAR}` with no value as an empty string, so **an empty value reads as the default**.
- Three places follow the same rule: `db/runtime.py` (needs_runtime), `collectors/commerce/storage/db.py`,
  `collectors/youtube/storage/db.py`. A host/port argument named on the function beats the env.
- Roles, DB names and secret key names are not knobs (`contracts/secrets.md`).
- **Commit as soon as you read. A server-side cursor holds a transaction open for its whole life.**
  `db/bootstrap.sql` puts `statement_timeout = 30s` · `idle_in_transaction_session_timeout = 15s` ·
  `transaction_timeout = 60s` on the `needs_runtime` role — do slow CPU or IO (tokenising, loading a
  large matrix, an LLM round trip) with a transaction open and it is cut off right there. Fixtures:
  `tests/test_aggregate_scale.py` (`IDLE_LIMIT`) · `tests/test_analyze_polarity.py`
  (`SQUEEZED_TIMEOUTS`) · `tests/test_ollama_predictor_connection.py` (the same name) reproduce the
  three limits in compressed form. A review of new DB code uses this bullet as its checklist.

## Common operations view (the minimum shape every collector must provide)
```sql
-- db/views/collector_health.sql UNIONs three arms: commerce (trend_radar.run+fetch_log),
-- naver (needs.naver_run+naver_fetch_log) and youtube (tubedepth.jobs)
collector text, dataset text, run_id text, started_at timestamptz, finished_at timestamptz,
status text,          -- ok | partial | blocked | failed | running
requests int, ok int, blocked int, failed int, queued int, p90_ms int
```
P16's table has to come out of this one view. `requests` is every fetch attempt, and `ok`·`blocked`·`failed`
are three buckets only — 2xx / 403·429 / (error or 5xx) — so the difference between their sum and
`requests` is the responses that went into no bucket (a 404, say).

**The youtube arm was attached by #77** (restoring what step 3 had removed — the three grounds the
2026-08-24 user decision hung on are all gone with #100·#101·#102: `jobs.error_code` classifies
blocking (#100), `jobs.started_at`·`jobs.elapsed_ms` exist (#101), and `jobs.dataset` carries the CLI
verb (#102 — `queue.enqueue` writes it on every new row, and a follow-up job of a listing job `watch`
made inherits the original's `dataset`. A reverse `kind → dataset` mapping is 1:N and does not hold,
so it is a separate column. There is no backfill, so old rows are NULL).

**One youtube row is one `(dataset, the 1-hour bucket of started_at)`.** `tubedepth.jobs` has no run,
so `run_id` is NULL — not widening that slot is #10 §A-2's ruling — and the view makes the equivalent
of commerce's run, "a finite bundle of work", out of time. It is a bucket rather than a window (`the
last 1h`) because commerce leaves every past run behind as a row: with a window, the moment cron
rests for an hour the youtube arm disappears from the table. A job that was never claimed (waiting, or
an old row from before #101) has no `started_at` and sits on `created_at`.

**`elapsed_ms` means something different per arm — the easiest thing in this view to get wrong.**
commerce's and naver's `fetch_log.elapsed_ms` is one fetch's round trip, while youtube's
`jobs.elapsed_ms` is one job's whole wall clock (claim→finish) (#101: a job answered from cache never
fetches, so there is no round trip to measure). So youtube's `p90_ms` is not "how slow was the
request" but "how long did one unit of work take", and for the same reason `requests` is not a count
of HTTP requests but of finished jobs — a job answered from cache counts as 1. Do not put the two
arms side by side and compare `p90_ms`. Old rows whose `elapsed_ms` is NULL drop out of the
percentile (they are not filled with 0).

`queued` is NULL for commerce and naver: both are batch workers called by cron and have no waiting
queue at all. It is a number for youtube alone, which is why 0 (the queue is empty) and NULL (there is
no queue) part. A queue-specific value like `oldest_pending` is not added as a column — the sql fence
above is canonical for the 12 columns, and widening it would make the other two arms each produce one
more NULL. The age of a queue backlog is read from the `started_at` of the oldest bucket with
`queued > 0`.

`error_code` (`jobs.error_code`, `String(64)`) has been a classification rather than an exception
class name since #100 (`collectors/youtube/cli.py::_classify_error`). This is the vocabulary the
youtube arm above reads as canonical — `blocked` is `quota`·`rate_limited`·`http_403` (a 403 that is
not quotaExceeded)·`http_429` combined, which joins up with the 403/429 definition of commerce's
`fetch_log.status`.
- `quota` — 403 + `error.errors[].reason == "quotaExceeded"` in the body (the YouTube Data API reports
  a spent quota in this shape rather than as a 429).
- `rate_limited` — 429.
- `http_<code>` — any other HTTP status (`http_403` covers a 403 that is not quotaExceeded — forbidden,
  accessNotConfigured and so on — plus `http_500` and the rest).
- `transport` — a failure with no HTTP status at all (DNS, socket, timeout).

`error_message` (`Text`) is `str(error)` as it stands — the original exception text did not move
column, `error_code` merely replaced the class-name slot with a classification. There is no live
transport yet (before #10, `_RaisingFetcher` is the default), so this code has never reached a real
403 response body — the classifier was written against the shape of `urllib.error.HTTPError`
(`.code`·`.read()`), and making whatever transport #10 attaches raise in that shape is #10's job.

The analysis counterpart is `needs.analysis_health` in `db/views/analysis_health.sql`: per run the
started/finished/status/versions and that run's `metrics_need`·`metrics_wish` row counts.
`need_mention`·`wish_mention` carry no run_id (versioning.md A19), so the row counts each step made
are carried by `analysis_run.note` as name=value pairs. `db/migrate.sh` reapplies it on every deploy
(CREATE OR REPLACE).

### What a stage is doing right now — `needs.pipeline_health`

The two above are **a log with one line per run** and cannot answer "what is stuck right now". That
answer is carried by `needs.pipeline_health` in `db/views/pipeline_health.sql`, and the expected period
is declared by `needs.pipeline_stage` (DDL 007) — the crontab (`stack/crontab.d/`) is not in the DB and
the portal reads the DB alone, through PostgREST. The reason the crontab is not parsed in is `enabled`:
`youtube watch` **has** a cron line but does not run, being behind a compose profile. Drift between the
declaration and the crontab is guarded by `tests/test_pipeline_stage.py`.

There is exactly one row per declared stage, and the columns are `stage_key` · `arm` · `dataset` ·
`enabled` · `expected_interval` · `last_success_at` · `last_run_at` · `last_run_status` ·
`overdue_by` · `freshness` · `requests` · `ok` · `blocked` · `failed` · `p90_ms`.

**Two facts are never folded into one.** `freshness` says "it did not run" only; `last_run_status`
says "it ran, and this is how it ended" only. A third fact is not added because a stage that failed
three days ago and has not run since would, in one value, look like only one of the two.

`freshness` is one of five values, measured on the `finished_at` of the last run that **ran**. "Ran"
includes `status = 'ok'` **and `partial`** — it ran and gathered most of it, and how well it finished
is said alongside by `last_run_status`. What it does not include is `yielded` (pushed off every source
by the lock and withdrew having gathered nothing, #78) · `failed` · `blocked`. Narrow this line to
"did it run cleanly" and a stage that runs on time every day but is always `partial` hardens into
`stalled` after two days and stays red forever (#154 caught exactly that by measurement):

| value | meaning |
|---|---|
| `disabled` | `pipeline_stage.enabled = false` — declared not to run. This wins even when there is a recent success |
| `never` | no run has ever succeeded. `overdue_by` is NULL — the question "is it late" does not arise |
| `ok` | the last success is within `expected_interval` |
| `late` | past that, but within `2 × expected_interval` |
| `stalled` | past `2 × expected_interval` |

The scale is a multiple of the period rather than an absolute because periods stretch from five
minutes (`youtube work`) to a month (`naver datalab`) — a constant margin is bound to be wrong at one
end.

The two analysis lines are told apart by `missing=` in `analysis_run.note`. `stage` carries an
implementation version and cannot be used as it stands, and a cron line does not tell them apart
either. `eval:*`·`trend-quarter:*` are not cron stages and never reach this view. The analysis arm has
no external fetch, so `requests`·`ok`·`blocked`·`failed`·`p90_ms` are NULL.

### What feeds what — `needs.pipeline_edge`

`pipeline_stage` is a *list* of stages and carries no relations. The relations are carried by
`needs.pipeline_edge` (DDL 008) — the diagram (#142), state propagation (#143) and lineage tracing
(#144) all stand on it.

**No separate node table.** `pipeline_stage.stage_key` already declares the stages, and for a store
**the normalised table name itself** is the key. Whether that name exists is asked of **this
checkout's DDL** by `tests/test_pipeline_edge.py` — not of a live DB, because that would measure "is
it on that server right now" rather than "is it a table this checkout knows", and then an upstream
contract referencing someone else's object would still be green (#107·#150, the same place).

Both directions are held — `stage → store` **writes**, `store → stage` **reads**. With one direction
only, lineage flows one way and cannot ride back from a metric to what was collected. **Stages are
never joined to each other** (the DDL blocks it with a CHECK): between two stages there is always the
table one left behind, and skipping it costs the lineage its "by way of".

The criterion for choosing a store node is **a table another stage or a screen consumes**. That
criterion forces a minimal set — every stage must have at least one edge (the test asks), so a stage's
only output is necessarily a node. Today that is **13 stages + 14 stores = 27 nodes, 29 edges**
(`analyze:polarity_missing` and its two edges are gone, suspended since #242). What
was left out on purpose, and why, is in the comments of `db/seed/pipeline.py` — among them
`needs.corpus_*`, tables the fork's DDL 023 makes, which the upstream contract does not reference (in
production `analyze` really does read them, so the picture is that much emptier; the fork adds those
edges to its own contract).

Two roles read it: `needs_runtime` (the GRANT in the view file) and `postgrest_anon`
(`db/grants/postgrest_anon_needs.sql`) — the portal asks as anon, so the first alone leaves the screen
with nothing. The two upstream views are not opened to anon: what the screen reads is this one view,
after the verdict.

## Analysis
```
cosmai analyze <stage> [--since <date>] [--scope <category>] [--impl <spec>] [--missing]
  stage ∈ {link, polarity, aggregate, all}
cosmai eval <task>        task ∈ {polarity, wish_class, brand_link, product_match}
cosmai lexicon {load, activate} --kind <kind> --version <n>
cosmai lexicon diff           --kind <kind> {--version <n> | --csv <path>} [--against <n>]
```
- **`lexicon diff` can compare the loaded source CSV against a DB version** (fork #62, `--csv`). Until
  then this command compared **DB versions only** and `--version` was required — so there was no way inside
  the repository to ask "is the repository's CSV the dictionary that is switched on now" (the place that
  question actually became necessary is the version retracing in `interfaces.md` §Retrieval measurements).
  The CSV side rides **the very conversion** `lexicon load` rides (`cosmai.cli._csv_rows`), and both sides'
  keys and values are made by the **same SQL expression** — re-render one side in Python and one key order
  of the `extra` jsonb turns every row into a "change". Without `--against` the active version is the
  partner. **aspect is narrowed to the ruleset the CSV names before comparing**: several rulesets live in
  one aspect version (`formats.md` §ruleset) and a CSV is the loaded source of one of them, so without the
  narrowing every other ruleset comes out as "deleted". Giving `--version` and `--csv` together is
  blocked (2) — when two things say which version it is, there are two answers. The exit code **does not
  change because they differ** (0 = an answer was computed).
- T14: `extract` is not a stage of its own — it only makes candidates and writes no row, so idempotence cannot be observed. Extraction runs inside `polarity` (the `Extractor` protocol is unchanged).
- B11: `eval aspect` was dropped because both the evaluation set and the baseline are 0 rows. Reviving it means the evaluation set and a row in `interfaces.md`'s baseline table arriving in the same PR.
- Every step is idempotent by **natural-key upsert**. A re-run produces the same result.
- An output row always carries a `*_version` (`versioning.md`).
- `analyze --impl <spec>` uses the same registry and the same spec grammar as `eval` (`ollama:gemma4:latest`·`llm:claude-sonnet-5`). Without it the rules run; with it that implementation's version is recorded in `analysis_run.versions.polarity` and on the output rows. **An implementation with no slot of its own in the ownership table is refused without `--scope`** — even a free one (analyze defaults to everything, so one scope-less line of such an implementation is a full relabel, and it costs either money or GPU time). An implementation with a slot (= an owner) may run without `--scope`: that one line covers its own `(scope, period)` alone, and `--scope` only narrows it further. It is refused even when that `--scope` is a `lexicon_category` that still has no owner in the table: registration has to come before the pass, or the result is deleted at the next 05:00. A paid implementation (`registry.is_paid`) is caught once more, ahead of that, on the grounds of money — the same place as `eval`'s enforced `--split`. Both refusals happen before the run opens, so they are blocked (exit code 2), and the verdict is made by `analysis/polarity/ownership.py`.
- `analyze all` makes one `needs.analysis_run` row (polarity opens it and aggregate writes metrics under
  that `run_id`) and records linker·extractor·polarity·aggregate plus `lexicon` (the active version +
  ruleset) in `versions`. If any one step fails, that run is closed with `status='failed'` + a note and
  the exit code is 1.
- The aggregate population of `analyze all` is the single `extractor_version` that run has just written
  — mixing a seed (`slice-*`) into the same scope counts one sentence twice. The chosen population is
  recorded in `versions.extractor`.
- Within that population **there is one polarity implementation per (scope, period)**: the ownership
  table (`analysis/polarity/ownership.py`) assigns one `lexicon_category` to one `polarity_version` and
  the first month that version is responsible for (`since`, the same YYYY-MM as `need_mention.month`),
  and the `need_mention` rows of that scope with `month >= since` are written and deleted by **the
  owner alone**. The reverse holds too: **an owner neither writes nor deletes the months before its own
  `since`.** Both directions are set up by one ownership predicate, standing in the same shape in the
  read skip, the delete statement and the `DO UPDATE`. Ownership is per `(scope, period)` rather than
  per row because 005's natural key does not carry `polarity_version`, and the period is attached to
  keep registration and the pass apart — register with `since` set to next month and the rules keep
  updating the months before it, so there is no reason to wait for a full pass to finish before
  registering. A `lexicon_category` with no owner, the months before an owner's `since`, and rows with
  `lexicon_category IS NULL` (YouTube comments, reviews with no category attached) are updated by the
  rules as they are today.
- So **in a month inside the owner's period that the owner has not reached yet there are no rows** (just
  after registration, and between the owner's passes). The rules do not fill in temporarily because, if
  the two implementations choose a different `need_key` on the same sentence, that temporary row stays
  beside the owner's and the aggregate counts one sentence twice — the 'a sentence whose category moved'
  paragraph below speaks of the same place. This gap lives for one period of the owner's pass.
- **`--missing` is the owner's incremental run**: what it selects on is not a date but **"a source row
  that has no `need_mention` row yet in the shape this run would write
  (`extractor_version`+`polarity_version`)"**. On every page it asks `need_mention` about those
  `(src, ref)` pairs and, where they exist, neither extracts nor judges. A review with no candidate at
  all gets a row from no run, so extraction is redone every time while judgement is never called
  (extraction is rules and cheap). **This mode deletes nothing** — it does not call `replace_stale`, so
  there is neither a window in which a month is left half written nor a rewriting marker. It only adds
  what is missing, so swapping things out (historical correction, a version bump, cleaning old rows
  whose `need_key` changed) is still the job of the full `--scope` path. For a run with no ownership
  (the rules, an implementation absent from the table) "my version's rows" is the whole rule
  population, which is meaningless, so it is **refused** — the same place and the same shape as the
  refusal of someone else's scope (before the run opens, `status='failed'` + exit code 1).
  That run's `note` carries `missing=1` and so parts from a full pass (an incremental always reports
  `replaced=0`).
- **The axis differs from `--since <date>`**: `--since` cuts **reading and deleting together** on
  `coalesce(written_at, captured_at)`, while `--missing` cuts **what has already been done**. Collection
  arrives late (`formats.md` §Time), so the two do not overlap — a rolling `--since` misses an old review
  scraped yesterday, and a fixed cut re-judges everything after the cut every day. What cron runs is the
  `--missing` side.
- **`--since D` narrows the delete too**: only rows with `observed_at >= D` in D's month are deleted
  (`need_mention` and `wish_mention` both). Without the narrowing, that month's rows before D are
  deleted and never rewritten, so every run digs the same hole. The `observed_at` in the delete
  statement is the same value as the source's `coalesce(written_at, captured_at)`, so it names the same
  row set as the read filter.
- **An owner's run does not walk the months before its own `since` at all**: in those months the
  ownership predicate passes no row, so deletes are 0 rows and writes are 0 rows and the walk is pure
  cost. The cut is the earliest `since` among the `(scope, since)` pairs that run touches (the one for
  that scope when `--scope` is given), and `ALWAYS` cuts nothing. It is independent of the mode.
- So one sentence's label belongs to the one implementation that owns that sentence's
  `lexicon_category` — for as long as that category does not move. The latest `rank_snapshot` rows and
  `category_map` are recomputed daily, so products move between categories, and after a move nobody
  deletes the owner's rows left in the old scope (a non-owner run does not touch them, and the owner's
  `--scope` delete leaves its own version's rows). On top of that the new scope's implementation picks
  the same sentence as its own, so **while two implementations choose different `need_key` values one
  sentence has two rows and the aggregate counts both** — with the same `need_key` the natural keys
  collide and the ownership predicate blocks the update, leaving the owner's single row. The old rows
  are cleared by the first run in which the owner's `polarity_version` rises.
- The other way round, when a product moves from someone else's scope **into the owner's scope**, the
  reclaiming party differs. A row the rules wrote before the move still carries the old category in its
  stored `lexicon_category`, and a rules run skips that unit **if the month is in the owner's period**
  (`analysis/polarity/pipeline.py` weighs the `lexicon_category` and that unit's month against the
  ownership predicate and the current `--scope`, and does not judge at all). If the month is before the
  owner's `since`, the rules run that unit as they are and pick it again under the **new** category, so
  in that month the double count below arises from a single rules run. The owner's `--scope` delete
  statement (`NEED_DELETE_SCOPED`) is narrowed to `lexicon_category = <that scope>` and cannot hit the
  row carrying the old category — so what clears the old rows in this direction is not the owner's pass
  but **a run in which the rules' own version rises**: the `NOT (extractor_version = ... AND
  polarity_version = ...)` predicate of `NEED_DELETE` catches that old row as stale and deletes it when
  the rules' `extractor_version`·`polarity_version` change. In the meantime the double count does not go
  away however many times the owner's pass is re-run.
- The `scope` axis of `metrics_need` is the source category rather than the `lexicon_category`, and the
  rollup scope (`all`) sums every category, so **one aggregate row can count the labels of two
  implementations together**. Which one counted which scope is answered by the ownership table: the
  `analysis_run.versions.polarity` of an `analyze all` is the version of **the implementation that ran
  that run**, not of every label that run aggregated.
- `--scope <value>` **accepts both axes** (#38): when the value is a `lexicon_category`, aggregate fans
  it out, over that run's population, into the **set of source categories** of the mentions carrying
  that label and writes to those scopes; when it is a source category string it writes that one scope
  (`scopes_for` in `analysis/aggregate/pipeline.py`). Either way the value left in `metrics_need.scope`
  is, per the line above, **the source category**, and the rows of a fanned-out scope are the same as
  the rows a run without `--scope` writes for that category — scope only chooses which category is
  written, never what is counted inside it. The reverse direction (lexicon → source) is not recoverable
  from `needs.category_map` alone: a leaf absent from the table is the identity (`formats.md`) and a
  `name_keyword` label has no source category at all — so the answer comes from that run's mentions
  rather than from the table.
- Even after the fan-out, **quietly producing 0** is blocked (#38): if a `--scope` run reaches aggregate
  and writes 0 rows to `metrics_need`, that run is closed as `partial` + exit code **1** in the same
  vocabulary and the same place as a run that lost the lock, and the note and stdout name the given
  scope value and the source category strings the mentions carrying that `lexicon_category` actually
  hold (saying so when there are none — a `name_keyword` label is that branch).
  **`metrics_wish` is not part of this predicate** — the wish aggregation in
  `analysis/aggregate/pipeline.py` does not look at `--scope` at all and recounts that population's
  whole wish set every time (`WISH_SCOPES` is independent of the scope argument), so 0 or not it says
  nothing about this scope. A run without `--scope` (the 05:00 cron) never takes this predicate.
- A non-owner run given `--scope <someone else's scope>` is **refused** — not a quiet no-op: that step
  ends in failure (`analysis_run.status='failed'`, exit code 1) and the message names the owner's
  `polarity_version` and the path of the ownership table.
- **Only one analyze run at a time.** The 05:00 cron (`analyze all`) overlapping a polarity pass someone
  runs by hand is normal, and when they overlap they read each other's half-written state: polarity
  deletes a month **and commits**, then rewrites it page by page, while aggregate filters on
  `extractor_version` alone and reads all of need_mention across several transactions (not a snapshot).
  So the lock is **one global lock, neither per scope nor per stage** — narrowing it either way cannot
  separate `polarity --scope <one category>` from an `aggregate` that reads everything. If another run
  holds that lock, this one skips **without running a single step**, leaves one `partial` run row with
  the reason, and ends with exit code **1** — what the operator sees is that row, and it does not wait
  (the same convention as the collectors: we yielded rather than being refused, and every step is a
  natural-key upsert so the next run takes it as it is). It is implemented as a session-scoped advisory
  lock held by the working connection (`analysis/locks.py`).
- Thanks to that lock **a half-rewritten month can be named**. Just before deleting a month, polarity
  writes `rewriting=<src>/<month>[/<scope>]` into `analysis_run.note` and removes it once the month is
  fully written. If the run dies in between, that marker stays, and the next run to hold the lock finds
  it on the fact that **an open marker can only belong to a dead run**: it closes that run as failed (no
  eternal `running` is left behind), writes which month it was into its own note and stdout, and ends
  partial (**1**). The condition for finding it is the marker, not `status` — the commonest death in
  practice (an ollama exception, a `statement_timeout`) is caught and closes the run as `failed`, so
  looking at `running` alone misses that half month entirely. It is said **once only**, and the run that
  said it records the fact by attaching `stale-reported` to that note.
- Whether that "once" is enough differs per scope. The half month of a scope with **no owner** fills itself
  in, because the next night's rule run rewrites that month whole — saying it once is the end of it. The
  half month of a scope **with an owner** (선블록→gemma4) is excluded by the rule run, so nobody fills it in,
  and after it has been said once nobody says it again: the only way to win that month back is for a person
  to run the owner's pass over that month again, and until then the evidence that stays is the `rewriting=`
  marker still attached to the dead run's note.

## Search (#28 → fork cosmai-import-ydc, upstream PR #59)
```
cosmai retrieval chunk  [--since <date>] [--source <s>]...
cosmai retrieval search --query <q> [--engine <e>] [--source <s>]... [--top <n>] [--vectors <path>]
cosmai retrieval eval   --mode <m> [--engine <e>] [--source <s>]... [--out <csv>] [--vectors <path>]
cosmai retrieval embed  [--model <m>] [--device <d>] [--batch <n>] [--vectors <path>]
cosmai retrieval terms  [--source <s>]... [--top <n>]
cosmai retrieval ask    --query <q> [--engine <e>] [--source <s>]... [--top <n>] [--model <m>] [--dry-run] [--vectors <path>]
  source ∈ {youtube_comment, youtube_video, youtube_transcript, commerce_review, mfds}
  engine ∈ {bm25, vector, hybrid}      mode ∈ {literal, heldout}
```
- **`mfds` is the fifth source (fork #77)** — one document per filing of `needs.mfds_registration` (DDL 028,
  fork #55), `doc_id = mfds:<report_seq>`, text = item name · company · `report no. <report_seq>` · `registered <report_date>` · the snapshot label, one chunk each (the line is far under the 500-character split, not an enforced invariant). It is
  **BM25 only**: `retrieval embed` never encodes it (`embed.ENCODED_SOURCES` holds the four text sources), so
  `vector` cannot find a filing and `hybrid` finds it through its lexical arm alone — and the grounding gate on
  `--engine vector` reads the four encoded sources only (`pipeline.index_sources`), so a token grounded by a
  filing alone is still refused there instead of buying a call answered from unrelated text chunks. `chunk` scans it with the
  others; `--since` does not apply (a snapshot has no per-row time). The default `--source` set is all five,
  ledger included (`corpus.SOURCES`, restated in `cosmai/cli.py`), so `ask` with no `--source` can answer a
  report number — `terms` alone is handed an absent `--source` as none and scans the text sources (fork #84,
  its bullet below); a scoped run (`--source <one>`) sweeps only its own source's vanished documents (fork #79).
  The first load in production was one `cosmai retrieval chunk --source mfds` run by the coordinator
  (2026-09-05, 4,735 chunks); a refresh of the ledger needs the same run again. Adding the source moved the index
  fingerprint (`index_signature` hashes the source set), so the first `search`/`ask`/`eval` after #77
  rebuilds the cache (`chunk` and `terms` never read it) — except under `--engine vector`, whose index is narrowed to the
  four encoded sources by `index_sources` in `search`, `ask` and, since fork #82, `eval`, so its signature did
  not move and no 380k-chunk rebuild happens there. Source: rows 8 and 13 of the #74 acceptance table on fork #69 — the seed alone lifted neither.
- **The topic lexicon is the active version of `needs.aspect_lexicon`** (`ruleset='retrieval-topic'`, fork
  #8). Its aliases set both the BM25 token expansion (Kiwi user words + substring expansion) and the
  evaluation gold (`match_topics`), so the one way to change the lexicon is `cosmai lexicon
  load/diff/activate` — the load source is `analysis/retrieval/dict/topics_v1.csv`. One aspect version is
  **the whole aspect lexicon across every ruleset** (`activate` switches per kind), so when the topics go up a
  version the polarity CSV (`eval/lexicon/aspect_lexicon_v1.csv`) is loaded at the same version alongside.
  **Which version is active is not a sentence in this file** (fork #63 — this line once said v2 was active in
  production after v3 already was): retrieval reads it from the DB at run time and every run says which one
  it stood on — `eval` rows and the `ask` note carry the lexicon stamp (`ruleset · version · topics · aliases
  · fingerprint`, fork #62) and `tool/show-lexicon-stamp` prints the active one, so a stale claim here has no
  reader. Last measured 2026-08-27: v3, fingerprint `ae48f7cfb70a60f7`, 63 literal · 62 heldout queries.
  Activating a version invalidates the index cache through that fingerprint (`pipeline.index_signature`);
  v2 → v3 moved the sunscreen topic from 12,197 to 12,418 documents.
- **The index and extraction axis carries no stopword list and no particle list** (fork #37, ydc
  `lexicon.json` disposed of). That axis is the index tokenizer (`bm25.tokenize`) and `terms`. **General terms
  are not removed by anything on it** (fork #59 — this line once credited lift): of ydc's general-word block,
  13 are dropped by `bm25.tokenize` itself (nine carry tags outside `KIWI_TAGS`, four fall to the two-character
  rule) and the 16 that survive stay in the index at full weight, discounted only by idf because they are
  common — `tests/retrieval/test_query_stopwords.py` counts that 13/16 contrast. lift
  (`analysis/retrieval/terms.py`) runs only in the uncaptured-expression report of `terms` and never touches
  BM25 scoring. Particles are told apart by Kiwi's tags: everything outside `KIWI_TAGS` (particles `J*`,
  endings `E*`) is dropped, one-character nouns too, so the 30 particles ydc verified on the corpus change not
  one token when attached to a stem (measured 2026-08-26 · 30/30 · `tests/retrieval/test_particles.py`).
  **The query axis is not decided by this sentence** (fork #46): words that describe the question are not
  separated by df (ydc measurement: the word for consumer at df 289 sits below a real topic at df 338), so
  they need a different ground, and that issue carries the judgment in the two items below. Whichever axis,
  what survives is not a file but **a row that gets a version** (fork #8) — topic surface forms in the lexicon
  above (`ruleset='retrieval-topic'`), brand surface forms in `needs.entity_lexicon` (`formats.md`, the
  lexicon CSV section).
  All **nine aliases of `lexicon.json` were judged** (fork #56): three were already there, one is caught by
  expansion and needs no row, **three** became rows of topic lexicon v3, and two stay unlisted — one because
  its canonical is `tier='stop'` so a row would have no consumer, the other because it sits below the floor
  (`terms.MIN_DOCS` 5) and its three videos are already seen by the sunscreen topic. The same v3 judged #37's
  seven candidates and raised four more. The judgment ledger and the four listing criteria are `formats.md`
  (topic lexicon v3 section), and `tool/measure-lexicon-candidates` re-measures those counts against it.
- **Query tokenisation parts from index tokenisation** (fork #46). The index is `bm25.tokenize`, the query
  is `bm25.tokenize_query` — the same tokenisation with **query stopword removal** laid on it, and
  `Index.search` alone rides that side. The reason they are not taken out of the index is that doing so
  would make a query that looks for `소비자` itself impossible. The reason the grounds for taking them out
  are neither lift nor idf is not that those words are common but that **they describe the question and so
  are not the topic** — statistics say the opposite (`소비자` 289 < `백탁` 338 above). So it is a judgment
  rather than a statistic, and being a judgment it **lives as a row that gets a version**: the canonical
  form is the active version of `kind='stopword'` · `canonical='query'` in `needs.entity_lexicon`, and the
  one way to change it is `cosmai lexicon load/diff/activate --kind stopword` (the loaded source is
  `analysis/retrieval/dict/query_stopwords_v1.csv`, the same place as the topic dictionary). That kind has
  an **active version of its own**, separate from the topic dictionary's — `entity_lexicon`'s `activate`
  turns one kind on and off (`db/lexicon.py` `ENTITY_ACTIVATE`), so a query-stopword revision and an aspect
  dictionary revision do not turn each other off. The version **number tag** is not like that —
  `formats.md` §entity 사전의 `kind='stopword'` writes down that limit and fork #58.
- Three rules hang on that list. (1) **A query that is entirely stopwords is not stripped** — 0 tokens
  means 0 results, which is worse than a ranking with filler in it. (2) **It does not invalidate the index
  cache**: `pipeline.index_signature` does not bite on this list and must not — the index is `tokenize` as
  it stands, so the same index is right after the list changes. `eval.docs_with_tokens`, which fixes the
  heldout answers, using `tokenize` rather than `tokenize_query` is the same reason (the definition of the
  answer is on the index axis). (3) **With no active version the list is empty and that is not blocked** —
  a different place from the topic dictionary: with no topic dictionary the answers are 0 and the score
  becomes false, while a search without query stopwords is the search as it was before this list. So
  `search` says one stderr line only when tokens were removed, and does not change the exit code (the same
  place as the coverage warning below). **v1 has not been loaded yet** (2026-08-26) — until then the list
  `search` sees is empty and the three rules above are observable only after a load and an activate.
- **No router that picks an engine per query** (fork #47). `--engine` goes through as the person gave it.
  ydc `v0.3.0`'s rule router (`rag/router.py`) was not promoted, and the grounds — two of the four signals
  have no source so two branches are blocked · **the canonical decision on an ingredient name is not the
  tokeniser dictionary** · the misrouting measurement on our own dictionary — are carried by
  `interfaces.md` §Query routing. Neither a new subcommand nor a new exit code is added.
- **No similarity floor on vector search** (fork #48). `--engine vector`·`hybrid` fill the top `--top`
  whatever the cosine is — it is a thing **decided not** to add, and the ground is the measurement that the
  top-cosine distributions of real queries (topic aliases, **61 of them** — that measurement stands on the
  sample of the then-active dictionary v2. Today's active v3 has 63, and until it is measured again that
  table is a record of the v2 version) and of ingredient names absent from the corpus do not part
  (`interfaces.md` §Vector floor). ydc `v0.3.0`'s `vector_threshold.py` was not promoted, and neither a new
  option nor a new exit code is added.
- **Instead a query with no grounding is stopped by chunk frequency — on `vector`·`hybrid` alone** (fork #48,
  `analysis/retrieval/grounding.py`). If any query token of length 4 or more has frequency 0, `search` does
  not rank at all and answers with one stderr line and 0 results — it means the corpus has never once said
  that name, so even a result that did come out would be a document unrelated to it.
  **The exit code is the `1` (no results) that already exists and no new code is added.**
  A query with 0 tokens (`톤 업`, a Cyrillic notation) is not judged by frequency but let through — stopping
  it would stop the one place where the vector side is the only one that answers.
- **`bm25` behaves as it did before this issue.** Lexical search ignores a word of frequency 0 as idf 0 and
  **answers with the words that are left**, so putting the gate on it would turn the partial answer that used
  to come out of a "a real topic + a new product name not yet in the corpus" query into 0 results. Nobody
  measured that loss (there is no query log), and there is no reason to accept an unmeasured loss.
- So **`--engine vector` opens the BM25 index too** (the frequency the gate looks at is there). The cost
  `bm25`·`hybrid` were already paying is now paid by the vector side as well, and in exchange a name absent
  from the corpus no longer fills the top k and gets printed as evidence. With a cache it is one pickle,
  without one it is the ten-odd minutes of morphologically analysing 380,000 chunks — and **the cache is
  separate per `--source` combination** (`pipeline.index_signature` bites on `sources`). A narrowed vector
  search has never opened the index until now, so a cache for that combination has never existed, and **the
  first call is unconditionally those ten-odd minutes**. A host with no vector file sees blocked (2) only
  **after** paying that cost — the gate comes before the store. `retrieval eval` does not ride this gate —
  and even if it did, 0 of the 61 topic aliases are stopped (`interfaces.md` §Vector floor. The same v2
  version's sample as above, and the two aliases v3 added have not been measured on this axis yet).
- **`--source` only narrows the candidates; it does not give a per-source share** (fork #54). Even after the
  narrowing the answer is the global top k of what is left. ydc draws per source and merges (92% of its index
  is short comments, so `mfds` was pushed down to rank 293) but our corpus has no such skew — the dominant
  source is 75.64% of the index while the top 10 takes only 71.11% of it, and `commerce_review`, 6.06% of the
  index, is 21.03% of the top 10. The measurement and the criterion are
  `interfaces.md` §Per-source allocation, and the way to measure it is `tool/measure-source-mix`.
- `terms` emits, as two stdout tables, the high-frequency nouns that dictionary **misses** and the
  document counts of the dictionary's own surfaces — material for a person to read and fix the CSV above.
  With no `--source` it scans the four text sources (`corpus.ENCODED_SOURCES`, the set `embed` encodes — the
  CLI passes the absent option through as none for `terms` alone, every other worker gets the five), not
  the ledger: the report grows the dictionary from consumer speech, and a filing's item and company names are
  not speech — `--source mfds` opts the ledger in (fork #84; #77 had widened the default to five without
  saying so).
  It is not dropped to a file: it is a snapshot of a corpus that grows daily, so keeping it in the repo
  makes it stale and, worse, makes it look like a second dictionary. Redirect it if you want to keep it.
- `chunk` alone writes (`needs.retrieval_chunk`). The other four read that table and the files. The sources are in other schemas and are reached only by the SELECT in
  `db/grants/needs_runtime_reader.sql` — the other side of the rule that a collector writes only to its own schema.
- Idempotence: `chunk` does not touch a row whose `text_md5` is unchanged (a re-run = 0 changes). `embed` is a full re-encode.
- **`chunk`'s delete verdict stands only within the range this run walked** (fork #23). The tail of a
  shortened document and the chunks of a document whose body went empty are always deleted — the ground
  is in the document that was walked. A document whose **row disappeared** at the source is deleted only
  by a full run without `--since`: in an incremental run "it did not come up" cannot be told from "it was
  out of range and not looked at". A source that yielded 0 documents on the walk is excluded for the same
  reason ("they all disappeared" looks the same as "it could not be read"). What was skipped is said by
  the run's note.
- **`--vectors` means the same thing in all three subcommands** (the vector store path). `--out` is used by `eval` alone and means the score CSV.
- **The default `--engine bm25` is the criterion for the literal purpose** — in heldout, bm25 is P@10 0.000
  and Hit 0% while vector is 0.062 and 25%, but in literal bm25 is the highest at P@10 0.864 (all six lines
  are `contracts/interfaces.md` §Retrieval measurements). The default for the exploratory purpose is decided
  in fork issue #11.
- **`ask` summarizes retrieval results; it is not a verdict** (fork #73, ydc `rag/generate.py`). The same
  evidence a person gets from `search` — gate included — is folded to one item per document (chunks of a
  document concatenated in rank order) and an LLM writes three fixed sections, `## Core` · `## Evidence
  summary` · `## Limits`, in the language of the query, citing `[Source: doc_id]`. Nothing downstream reads
  the answer: it stands on retrieved chunks, a different denominator from the verdict table (§Evidence's
  three grounds), and the Limits section says so. Engine as `search` (`--engine`, default `bm25`, no router);
  sources as `search`. `--model` defaults to `claude-sonnet-5`; `--dry-run` prints the prompt and the folded
  evidence and calls nothing. Every real call is reserved on the shared `needs.llm_usage` ledger **before**
  it goes out and settled after (`purpose='retrieval_ask'`, the $10 hard stop of `analysis/polarity/pricing`),
  and leaves one row in `needs.retrieval_ask_log` (DDL 026) written after the round trip; the prompt rules,
  the note and the log columns are `interfaces.md` §Answer layer.
- exit codes: 0 ok · 1 partial (`chunk`'s contract violation, `search`'s no results — a query stopped for
  having no grounding is here too, `eval`'s 0 scored queries and `terms`'s 0 documents walked — both mean the
  chunks were empty) · 2 blocked (connection refused, the vector store unreadable — the file being absent, and
  the manifest missing `model`·`query_prefix`·`l2_normalized`·`dim` or having them disagree with the matrix,
  are the same place, **no active topic dictionary** — it means `cosmai lexicon load/activate` has not been
  run yet, so it is blocked rather than a failure). `embed` has no partial — it is a full re-encode, so it
  leaves no half-finished store, and when it finishes it is 0.
  `ask`: 0 an answer (or a dry run that had evidence) · 1 no evidence — the gate blocked the query or it had
  0 hits, and the fixed refusal still goes to stdout — and an answer the model cut off at `max_tokens` or
  left empty, which is settled and logged but never passed off as complete · 2 blocked — no active topic
  lexicon, the vector store unreadable, the ledger's hard stop (`BudgetExceeded`, before any call), a model
  `pricing.py` has no price for, or no `CLAUDE_API_KEY` outside `--dry-run`. stdout carries only the
  three-section markdown (or the refusal, or the dry-run dump); the version note and the cost line go to
  stderr, like `cards`.
- **A coverage warning goes to stderr and does not change the exit code** — the vector and hybrid paths of
  `search`·`eval` compare the chunk count the store covers and the manifest's `chunked_at_max` against
  **the same query** the BM25 cache key uses (`count(*)`·`max(chunked_at)`), and on a mismatch print one
  line and carry on — stopping would also block the legitimate use of deliberately searching an older
  corpus. `eval` carries the same line in the CSV `note` column and the stdout summary (which corpus the
  score is on). `chunked_at_max` is **not a required key** — without it only the count is compared and
  that fact is warned about (refusing would stop every search running on a store baked before that key).
  A mismatch is fixed by a full `embed` re-encode.
- **An evaluation row carries the store version even when nothing is out of step** (fork #49). `eval`'s
  vector·hybrid carry the manifest's `model`·`revision`·vector count·`chunked_at_max` in the
  CSV `store` column and in one line of the stdout summary — **a different axis** from the coverage
  warning right above: that one speaks only when something is out of step, so when all is well the
  version is left nowhere, and that is the place where the delta ydc labelled "1st → 2nd" was really
  "no MFDS vectors → 2nd" (`v0.3.0` fixed it by putting the version into the output file name — we
  produce rows rather than files, so the same place is a row).
  **A row with no version cannot come out**: if the store cannot be opened that whole run is blocked (2), and
  a store whose `model` is empty is refused by `load` (the blocked item above). The bm25 rows are empty — no
  store is opened, so there is no version to invent. This column does not change the exit code.
- **An evaluation row writes down the topic dictionary version itself too** (fork #62). `eval` puts the
  `ruleset`·`version`·topic count·alias count·**content fingerprint** of the active dictionary that run
  actually read into the CSV `dictionary` column and one line of the stdout summary. It is **a different axis
  from** the store version just above, **and the set of rows it fills differs too**: `store` is on vector and
  hybrid alone, which open the store, while `dictionary` is on **all three engines** — the gold
  (`match_topics`) and the queries (the topic aliases) are both made by the dictionary, so even a bm25 row,
  which opens no store, stands on the dictionary. The reason it does not carry the number tag alone is that
  **rows can be added to the version that is switched on** (for the same reason `pipeline.index_signature`
  bites on the number and the fingerprint together). The alias count counts `ko`+`latin` only — `mfds_inci`
  is used neither for matching nor for querying, so counting it in would make one word speak for two axes.
  **A row with no version cannot come out**: with no active dictionary that whole run is blocked (2) (the
  blocked item above). This column does not change the exit code. The way to print the version again is
  `tool/show-lexicon-stamp`.
- **Vectors are files** — `var/retrieval/vectors/e5base.{npy,ids.csv,manifest.json}`. pgvector is deferred
  to #28 step 4b. The BM25 index is cached as `var/retrieval/bm25/index-<sha16>.pkl` too (key = the chunk
  count + the newest `chunked_at` + the hash of the two Kiwi dictionaries + **the active topic
  dictionary's version and content fingerprint**). Since the topic dictionary stopped being a file, a key
  hanging on file hashes alone misses a topic change — and a version number alone is not enough either
  (rows can be added to the version that is switched on). Both live under `var/`, so they never enter the
  repository and are rebuilt when deleted.
- **`embed` is run by a person on a GPU host, not by cron.** So `sentence-transformers` and `torch` are in
  the `embed` extra alone and enter neither `stack/Dockerfile` nor `tool/checks/test` — the tests have to
  run on the set the image carries. It is run as
  `uv run --extra retrieval --extra embed cosmai retrieval embed …`. Installing it with
  `uv sync --extra embed` gets removed by the next `tool/checks/test` (which is the right behaviour).
- For the same reason as `analyze all` it is exempt from the cron-interval rule — it is a DB-and-file job with no external fetch.

## Quarterly time series (fork #5, ydc `trend.py` promoted)
```
cosmai trend quarter [--url <url>]
```
- It reads the active corpus snapshot (`corpus_snapshot.active`) and the active panel roster (the active
  version of `panel_channel`) and writes `needs.metrics_topic_quarter`. **Neither the snapshot nor the
  roster is an argument** — two ways of choosing means two denominators, and the place that picks the
  active version is one each: `db/corpus.active_snapshot` and `db/seed/panel.active_version` (the latter
  stops instead of answering when there are two active versions).
- The population is the manifest rule as it stands: videos carrying a `content_type='video_long'` ·
  `panel_role='product'` · `topic_id='선크림'` mention, and the comments attached to those videos. The
  `scope` of an output row is the same vocabulary as `metrics_need.scope` (`선블록`) and its `content_type`
  is `long_form`.
- **One run rewrites the rows of that (run, scope, roster) wholesale.** Not updating in part is how the
  grid is kept dense — a re-run produces the same rows under the same `run_id` (found by the note).
- After writing it asks `needs.metrics_topic_quarter_violation` back about that run. If the view says
  anything, the exit code is **1** (partial) and stdout carries that line — the table stands, but what the
  table means differs from the contract.
- exit codes: 0 ok · 1 partial (the invariant violation above) · 2 blocked (connection refused, **no active
  roster**·**no active snapshot**, the population being empty so there is no row to produce — all three mean
  `db/seed --only panel`·`db/corpus load` has not been run yet, so it is blocked rather than a failure).
- `analysis_run.versions.metric` carries the definition version of those rows (`versioning.md`).

## Verdict (fork #40, ydc `judge.py` promoted)
```
cosmai trend judge [--url <url>]
```
- It reads the `needs.metrics_topic_quarter` rows **of the run** `cosmai trend quarter` produced and
  writes `needs.topic_quarter_judgement`. The run is found by **the same path** as `quarter` (the note
  made from the active snapshot and the active roster) — the same reason there are no arguments. With no
  metric rows there is nothing to judge.
- **It does not recompute the metrics.** The verdict's criteria (`TAU`, the weights, the type names)
  change by team agreement, and splitting the two steps so the metrics need not be recounted then is
  ydc's design; this command takes it as it stands.
- One run rewrites the verdict rows of that (run, scope, roster) wholesale — not updating in part is how
  the 1:1 with the metric rows is kept.
- After writing it asks `needs.topic_quarter_judgement_violation` back about that run. If the view says
  anything, the exit code is **1** (partial) and stdout carries that line.
- exit codes: 0 ok · 1 partial (the invariant violation above) · 2 blocked (connection refused, no active
  roster or snapshot, **that run has no metric rows** — it means `cosmai trend quarter` has not been run yet,
  so it is blocked rather than a failure).
- `analysis_run.versions.judgement` carries the definition version of those rows (`versioning.md`).

## Sensitivity and backtest (fork #41, ydc `panel_sensitivity.py`·`backtest.py`·`spam_ad_flags.py` promoted)
```
cosmai trend sensitivity [--url <url>]
```
- It asks whether the conclusion **of the run** `cosmai trend quarter` produced wobbles under three
  choices: the panel composition (product only vs all 43 channels) · the cutoff (recounted as if only the
  past quarters were known) · ad and sponsorship marking (recounted with them removed). The run is found
  by **the same path** as `quarter` and `judge` (the note made from the active snapshot and the active
  roster) — the same reason there are no arguments.
- **It writes nothing.** The rows the three measurements make belong to a counterfactual population and have
  no place either in 022's `panel_role` vocabulary or in `analysis_run` (`interfaces.md` §Sensitivity). The
  answer is stdout rather than a table, and being read-only it is run against the production DB as it is.
  That the stored tables are untouched is held by a fingerprint in `tests/test_sensitivity_pipeline.py`.
- The baseline is recounted, and if that baseline differs from the stored `metrics_topic_quarter` rows
  that fact (`baseline_drift`) comes first — every difference this command reports is meaningless then.
- exit codes: **0 ok — an answer was computed** · 1 partial (**do not trust this output** — `baseline_drift`,
  or fewer than two directional verdict cases so there is nothing to call a backtest (`thin_backtest`)) ·
  2 blocked (connection refused, no active roster, snapshot or topic dictionary, **that run has no metric
  rows** — it means `cosmai trend quarter` has not been run yet, so it is blocked rather than a failure. The
  corpus being empty while only metric rows remain, so there is no quarter for the window to stand on
  (`ShortHistory`), is the same place).
- **"The conclusion wobbles" is not a 1.** That is the **finding** this command exists to give, not a
  failure of the run, and in the shared convention at the top of this file
  (`0 ok · 1 partial (some failed or were truncated) · 2 blocked`) a 1 means "the output is not intact".
  A wobble is carried by `panel_flips=`·`ad_flips=` in the `note` and by the three tables, not by the exit
  code — over everything a wobble is the normal state (drop the ads and sponsorships and 19 cells change
  type), so reporting 1 would make a `set -e` shell, make, or a one-line CI read a normal run as a
  failure. The source, ydc, is in the same place: `panel_sensitivity.py`·`spam_ad_flags.py` are always 0
  and only `backtest.py` uses 1 for fewer than 2 cases.
- It is safe on cron (read-only, and 0 is the normal state). But the answer changes only when the corpus
  or the roster changes, so for now a person asks it once and records it on the issue.
- **`cards` in §Evidence and cards below is the same place** — "no cell was caught by the rules" is a finding, not a failure.

## Evidence and cards (fork #6, ydc `evidence_comments.py`·`cards.py` promoted)
```
cosmai trend evidence [--url <url>]
cosmai trend cards --quarter <q> [--url <url>]
```
- `evidence` writes the evidence comments attached to the cells **of the run** `cosmai trend judge`
  judged into `needs.topic_quarter_evidence`. The path to that run is the one note `quarter` and `judge`
  use, which is why the only argument is `--url`.
- **The population is the very predicate that built the metrics** — it takes the `POPULATION` CTE in
  `analysis/trend/pipeline.py` as it stands. Pick the evidence from a different population and a card's
  quotes and a card's numbers stand on different denominators.
- **It commits as soon as it has read the candidates and does not look at the DB after that.** Unlike the
  verdict, evidence is a step that walks the corpus, so it runs straight into `needs_runtime`'s
  `idle_in_transaction_session_timeout` (15 seconds) — fold with a cursor open and it is cut off (the same
  place as `analysis/trend/pipeline.py`). What it reads in is not the body but pointers and like counts only,
  and over everything it was really measured at 15,602 candidate rows · 0.52s · 73MB (`interfaces.md`
  §Evidence's "full measurement").
- One run rewrites the evidence rows of that (run, scope, roster) wholesale — with a partial update the
  ladder of slots (rank) would silently develop holes.
- After writing it asks `needs.topic_quarter_evidence_violation` back about that run. If the view says
  anything, the exit code is **1** (partial) and stdout carries that line.
- exit codes: 0 ok · 1 partial (the invariant violation above) · 2 blocked (connection refused, no active
  roster or snapshot, **that run has no verdict rows** — it means `cosmai trend judge` has not been run yet,
  so it is blocked rather than a failure).
- `cards` **writes nothing.** It reads the three tables above and emits a bundle of markdown cards on
  stdout. It is not dropped to a file for the same reason as `retrieval terms` (a snapshot of a growing
  corpus goes stale in the repo) — redirect it if you want to keep it. `--quarter` is required: a card is
  the unit in which someone decides "should this topic get more attention this quarter", so without a
  quarter the question does not stand.
- `cards` exit codes: **0 ok — the cards were computed (even when there are none)** · 1 partial (**a cell
  matched a rule but could not stand as a card because the evidence's original text is missing** — that
  alone is a truncated output) · 2 blocked (connection refused, the run has no verdict rows, that quarter
  is not in this run's grid — the message tells the last two apart).
- **"No cell caught by the rules" is not a 1.** It is the normally computed answer after every rule has
  run, and in this file's common convention at the top a 1 means "the output is not whole" — the same seat
  and the same sentence as "shaking is not a 1" in the sensitivity section just above. The measurement says
  so too: in the sample golden **9 of 13 quarters have no card**, so a 1 would read 69% of the normal state
  as failure, and `cards` is not an exploratory command a person runs once but the last cell of
  `quarter → judge → evidence → cards`, where a `set -e` shell, make or cron would stop on that line
  (upstream #55's start condition is "S6 automatic consumer"). How many cards there were is carried by the
  stderr `note`, not by the exit code.

- **stdout is the markdown output and nothing else.** The `note` and the truncated-cell lines go to
  stderr — a redirected `.md` must not have `trend cards run=…` left in it, so that the file is the
  document as it stands.
- `analysis_run.versions.evidence` carries the definition version of the evidence rows (`versioning.md`).
  Cards make no rows and so leave no version — which definition's evidence a card carried is answered by
  this key on the run it read.

## Crosscheck (fork #7, ydc `source_composition.py`·`commerce_crosscheck.py`·`cross_source.py` promoted)
```
cosmai trend crosscheck [--url <url>]
```
- It puts four sources side by side and looks for the places they disagree: composition (the topic
  composition per source under the same dictionary) · rating (the commerce platforms' attribute rating
  against that run's verdict) · ingredients (three ingredient discourses and an ingredient-key audit). It
  does not sum them — the denominator differs per source (`interfaces.md` §Crosscheck).
- **It writes nothing.** A row of the three answers is keyed by one (topic) or one (ingredient), while
  022's quarterly grain is keyed by eight columns and the commerce side has neither the quarter nor the
  roster among them. The answer is stdout rather than a table, and being read-only it is run against the
  production DB as it is. That the stored tables are untouched is pinned by a fingerprint in
  `tests/test_crosscheck_pipeline.py`.
- The run is found by the **same path** as `quarter`·`judge`·`sensitivity` (the note made from the active
  snapshot and the active roster) — which is also why `--url` is the only argument. The quarter it
  crosschecks is the **second from last** of that run's grid (the last is the quarter in progress, which the
  verdict leaves as `미확정(진행 중)`, so it is undercounted).
- It walks the chunk index once — measured for real at 381,950 chunks, 48MB, **11.3 seconds** over
  everything (2026-08-27, keyset pages of 20,000 rows with a commit per page). Walking it in one stream
  hits `needs_runtime`'s `transaction_timeout` (60 seconds), so it uses the same method as
  `gold_from_chunks` in `analysis/retrieval/eval.py`.
- exit codes: **0 ok — the crosscheck table was computed** · 1 partial (**do not trust this output** —
  either an ingredient key caught an ingredient name a person read once and forbade (`key_mismatch`; the
  `시카` incident of §Crosscheck is this place), or the topic of ours that a commerce `topic_group` points
  at is not in the active dictionary (`group_map_drift`)) · 2 blocked (connection refused, no active
  roster·snapshot·topic dictionary, **no metrics run on that snapshot and roster** (`cosmai trend quarter`
  has not been run yet), **no verdict row on that run** — which means `cosmai trend judge` has not been run
  yet, so it is a block rather than a failure. An empty chunk store (`cosmai retrieval chunk`) and no
  suncare product in the ranking (`cosmai collect commerce`) are the same place — there is no source to
  crosscheck yet). **All eight branches** are one of the code's `NoPopulation`·`NoCrosscheck`·`NoDictionary`,
  and the message says which.
- **"The sources disagree" is not a 1.** That is the **finding** this command exists to give, not a failure
  of the run, and in the common convention at the top of this file a 1 means "the output is not whole" —
  **the same place and the same sentence** as "shaking is not a 1" in §Sensitivity and backtest above and
  "no cell caught by the rules is not a 1" in §Evidence and cards. The measurement says so too: over
  everything, several of the 13 topics carry a disagreement reading (for example `백탁` commerce 9.80%
  against comments 1.55%), so emitting a 1 would read the normal state as a failure. A disagreement is
  carried by the table's `reading` column and the `note`, not by the exit code.
- **Thin evidence is not a 1 either.** A topic with fewer than `MIN_PRODUCTS` (5) attribute-rated products
  gets no reading written, and `thin=` in the `note` counts them. Thin is a computed answer, not a
  truncated output.
- It is safe on cron (read-only, and 0 is the normal state). But the answer changes only when the corpus
  or the collection changes, so for now a person asks it once and records it on the issue.

## Holdout (fork #51, ydc `holdout_commerce.py` promoted)
```
cosmai trend holdout [--url <url>]
```
- **It asks the existing conclusion again with newly piled-up commerce reviews — it does not replace the
  numbers.** It splits the reviews of the same population (the suncare ranking predicate of §Crosscheck) into
  two arms and counts them with the same code: the reviews in the chunk index (`seen`, what we have seen) and
  the reviews not in it (`holdout`, **what has never once been seen**). When they part, why they part is
  measured split three ways — the window, the platform composition and the product basket
  (`interfaces.md` §Holdout).
- **Why the only argument is `--url`**: the cutoff is not a date but the roster of commerce `doc_id`
  values in `needs.retrieval_chunk` — two ways of choosing means two denominators (the same convention as
  `quarter`·`judge`·`crosscheck`). ydc took a `--cutoff`, but that repo had no row for "what have we
  seen".
- **It writes nothing.** A row of this answer is keyed by (arm, topic), and the boundary of `arm` is the
  chunk index, so it moves every time `cosmai retrieval chunk` runs (today's holdout is tomorrow's seen).
  The answer is stdout rather than a table, and being read-only it is run against the production DB as
  it is. That the stored tables are untouched is pinned by a fingerprint in
  `tests/test_holdout_pipeline.py`.
- **The four reads (the commerce chunk roster · the review key roster · the empty-body count · the
  population) happen inside one transaction snapshot (`REPEATABLE READ`).** Left outside it, the four would
  point at different populations while a collector runs, and `seen + holdout + empty` would be the size of no
  population at all. Where each of the three things ydc laid on by hand — the stop, the total-order sort and
  the row-count comparison — goes here is carried by the table in `interfaces.md` §Holdout; carrying the three
  over as they are would be an identity in this place rather than a check.
- exit codes: **0 ok — an answer was computed (that holds even when it does not reproduce)** · 1 partial
  (**do not trust this output** — there is a commerce chunk with no source review (`chunk_orphan`). Chunks
  carry no foreign key (020), so the seen arm is then not the arm the analysis actually saw) · 2 blocked
  (connection refused, no active topic dictionary, no suncare product in the ranking
  (`cosmai collect commerce`), not one commerce chunk — **it means there is no reference point at all**, so
  run `cosmai retrieval chunk`, not one review with a chunk inside the suncare population — there is no
  existing arm to compare against, **not one unseen review** — there is no new sample to ask again with).
  **All six branches** are one of the code's `NoHoldout`·`NoDictionary`, and the message says which.
- **"It does not reproduce" is not a 1.** That is the **finding** this command exists to give, and in the
  common convention at the top of this file a 1 means "the output is not whole" — **the same place and the
  same sentence** as "shaking is not a 1" in §Sensitivity above and "disagreeing is not a 1" in §Crosscheck.
  ydc's `report`, the source, always emits 0 too.
  Which branch it is (`재현`·`순위 재현`·`순위 변동`·`순위 없음`) is carried by the `note` and the table.
- **A thin sample is not a 1 either.** A topic whose seen arm has fewer documents than `MIN_MENTIONS` (5)
  gets no rank (`-`), and `ranked=` in the `note` counts how many topics do have one.
- **Not put on cron. "0 is the normal state" from §Crosscheck above is false for this command.** Read-only it
  is, equally, but the normal state does not stand on one exit code: once `cosmai retrieval chunk` runs the
  holdout moves wholesale into the seen arm, so **the normal state right after that is `2`
  (`no unseen sample`)**, and it stays so until the commerce collector piles up again. A cron that watches
  alarms by exit code reads that stretch as a failure. There is **no** line in `stack/crontab.d/` that runs
  `retrieval chunk` (measured 2026-08-27), so today's risk is not cron but **the order of hands** — chunk
  before asking again and the question itself disappears. A person asks this command once and leaves the
  answer on the issue.

## Schedule (stack/crontab.d/, UTC)
The rule for the commerce lines is not "avoid minute 0" but **the gap between two adjacent lines is wider
than the earlier line takes**. That duration is not written here as a number — it comes out of the code.
`engine.collect` runs the sources that declare that dataset (and `--board`) **concurrently, one lane per
source** (#25), and one source takes `SourcePolicy.min_interval_s` × (requests − `burst`). So a line's
duration is **the slowest source's, not the sum of the sources'**. The lane count has a ceiling
(`MAX_CONCURRENT_LANES` in `collectors/commerce/storage/db.py`) and it is a connection budget rather than
a preference — each lane holds one source-lock connection for the whole walk, so a line with more sources
than lanes has a second floor as well, "total work ÷ lane count". There are two bases for the request
count, so there are two durations: the **seed basis**, which walks only the length of `seeds()`, and the
**budget basis**, which fills up to `max_requests_per_run`. On the budget basis the hourly ranking alone,
with its slowest source (daisomall), runs past and occupies the start times of 02:10 product and 04:15
review — and moving the cron does not close it. This is not an overlap to untangle by moving the cron; it
is the overlap a per-source advisory lock closes (#10 §A-8-1, `collectors/commerce/storage/locks.py`), and that lock is already wired
unconditionally into the production entry point (`collectors/commerce/cli.py`, with
`tests/collectors/commerce/test_source_lock.py` holding that place). Interval arithmetic cannot see the
lock, so `tests/collectors/commerce/test_every_dataset_is_collected_and_scheduled.py` always checks the
seed basis alone and the budget basis of those two pairs stays **permanently** xfail(strict) — what that
strict catches is not the lock landing but the day the budget shrinks and the overlap disappears
altogether.

**Both are a lower bound, not an upper one.** The calculation above uses the pace the policy *declares*,
but `Gate._back_off` widens the live interval up to `Gate.MAX_INTERVAL_S` (300 seconds) when the site
answers 403·429·503 — daisomall's 30 seconds becomes 300 seconds. Response latency and retries are not in
the value either, and a source with no `max_requests_per_run` is counted by its seed count even on the
budget basis (since #10 all four sources declare one, so there is no such source today). The lane
arithmetic is optimistic in the same way: a real run hands the lanes out in registration order rather than
longest source first, so a line with more sources than lanes can take longer than either of the two lower
bounds above. So this number is "at least this much", not "at most this much". What guarantees they do not
overlap is the lock, not the interval. `analyze all` is a DB-only job with no outside fetch, so it is
harmless even overlapping the hourly run and is out of this rule. `analyze all` does have one interval
rule, though, and the lock is what sets it: overlapping an owner's polarity pass that takes the same lock
makes whichever arrived second skip that whole night, so the gap between that line and `0 5` has to be
wider than that pass's worst-case duration T. **That line now exists** (`0 8`, #32) — the command is
incremental (`--missing`, #98) and **that T is still unmeasured**: running in operations is the
coordinator's part, so the work that put the line in could not measure it. So the time was chosen by
widening the gap to its maximum instead of by knowing T — `0 8` gives `0 5` 3h and takes 21h for itself.
The only value that has a measurement is the full pass's (run 16, going round 선블록 alone, at **6h44m**),
and 21h is three times that. The coordinator measures T on the first night and on a normal night, and moves
these two times if it threatens the 21h. The calculation and the GPU window (08:00–16:00 UTC, which
`retrieval embed` avoids) are written down in `stack/crontab.d/analyze`.

**Suspended as of 2026-09-06 (#242).** The `0 8` line above is gone: the model host is gone and this host
cannot run the model, so `OWNERS` in `analysis/polarity/ownership.py` holds no gemma4 scope and the fenced
schedule below carries no `--impl` line either — the two only ever move together. The paragraph above (T,
the interval choice, the GPU window) is historical, kept in `stack/crontab.d/analyze` for the day the line
returns.

naver's DataLab line was commented out from #182 until #90: with no anchor in the requests, a
monthly pull could not be compared with the one before it. #90 put the anchor in every request, so
the line runs again and `db/seed/pipeline.py` declares `naver:datalab` enabled to match — the
crontab and that declaration only ever move together (`tests/test_pipeline_stage.py`).

youtube's `work` was added to this table on 2026-08-24 (before that there were three, and no line
drained the queue). It is cron rather than a resident daemon because
`collectors/youtube/cli.py:_run_work` is a batch that claims `DEFAULT_WORK_BATCH` at a time and stops —
the repetition comes from outside. Overlapping runs are safe: `_claim` takes rows with a single
`FOR UPDATE SKIP LOCKED` statement.
```
0 * * * *   cosmai collect commerce --dataset ranking
10 2 * * *  cosmai collect commerce --dataset product
30 3 * * *  cosmai collect commerce --dataset review_low --board suncare   (the board is the scope.json list)
15 4 * * *  cosmai collect commerce --dataset review
45 4 * * *  cosmai collect commerce --dataset review_stats
30 5 * * *  cosmai collect commerce --dataset new_product
0 5 * * *   cosmai analyze all
youtube: watch 1h · work 5m · flatten 15m · prune 1d  (after the fan-out cap is applied)
naver:   datalab once a month (by the keyword dictionary) · blog once a month
```
