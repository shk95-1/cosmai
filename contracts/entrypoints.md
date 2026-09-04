# 엔트리 규약

## 수집기
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

## DB 접속 노브 (secret 아님)
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

## 공통 운영 뷰 (각 수집기가 제공해야 하는 최소 형태)
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

### 단계의 지금 상태 — `needs.pipeline_health`

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

### 무엇이 무엇을 먹이는가 — `needs.pipeline_edge`

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
only output is necessarily a node. Today that is **14 stages + 14 stores = 28 nodes, 31 edges**. What
was left out on purpose, and why, is in the comments of `db/seed/pipeline.py` — among them
`needs.corpus_*`, tables the fork's DDL 023 makes, which the upstream contract does not reference (in
production `analyze` really does read them, so the picture is that much emptier; the fork adds those
edges to its own contract).

Two roles read it: `needs_runtime` (the GRANT in the view file) and `postgrest_anon`
(`db/grants/postgrest_anon_needs.sql`) — the portal asks as anon, so the first alone leaves the screen
with nothing. The two upstream views are not opened to anon: what the screen reads is this one view,
after the verdict.

## 분석
```
cosmai analyze <stage> [--since <date>] [--scope <category>] [--impl <spec>] [--missing]
  stage ∈ {link, polarity, aggregate, all}
cosmai eval <task>        task ∈ {polarity, wish_class, brand_link, product_match}
cosmai lexicon {load, activate} --kind <kind> --version <n>
cosmai lexicon diff           --kind <kind> {--version <n> | --csv <path>} [--against <n>]
```
- **`lexicon diff` 는 적재 원본 CSV 를 DB 버전과 맞댈 수 있다**(포크 #62, `--csv`). 그전까지 이 명령은
  **DB 버전끼리만** 비교했고 `--version` 이 필수였다 — 그래서 "레포의 CSV 가 지금 켜져 있는 사전인가"를
  물을 길이 레포 안에 없었다(그 물음이 실제로 필요해진 자리는 `interfaces.md` §검색 실측 의 판본 되짚기다).
  CSV 쪽은 `lexicon load` 가 타는 **그 변환**(`cosmai.cli._csv_rows`)을 그대로 타고, 양쪽 키·값은 **같은 SQL
  식**으로 만든다 — 한쪽을 파이썬으로 다시 렌더하면 `extra` jsonb 의 키 순서 하나로 전 행이 "바뀜"이 된다.
  `--against` 없으면 활성 버전이 상대다. **aspect 는 CSV 가 말하는 룰셋으로 좁혀 맞댄다**: 한 aspect 버전에는
  룰셋이 여럿 살고(`formats.md` §aspect 사전의 ruleset) CSV 는 그중 하나의 적재 원본이라, 안 좁히면 다른
  룰셋 전부가 "지워짐"으로 나온다. `--version` 과 `--csv` 를 함께 주면 blocked(2) — 어느 쪽이 그 판본인지
  둘이 말하면 답이 둘이다. 종료 코드는 **갈렸다고 바뀌지 않는다**(0 = 답이 계산됐다).
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
- `--since <date>` 와는 축이 다르다: `--since` 는 `coalesce(written_at, captured_at)` 로 **읽기와
  삭제를 함께** 자르고, `--missing` 은 **이미 한 일**을 자른다. 수집이 늦게 오므로(`formats.md` §시간)
  그 둘은 겹치지 않는다 — 어제 긁힌 옛 리뷰는 롤링 `--since` 가 놓치고, 고정 컷은 컷 이후 전부를 매일
  다시 판정한다. 크론이 도는 것은 `--missing` 쪽이다.
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
- 그 "한 번"이 충분한지가 scope 마다 다르다. **주인 없는** scope 의 반쪽 달은 다음 밤 규칙 실행이 그 달을
  통째로 다시 써서 스스로 메워진다 — 한 번 말하면 그것으로 끝이다. **주인 있는** scope(선블록→gemma4)의
  반쪽 달은 규칙 실행이 배제하므로 아무도 메우지 않고, 한 번 말한 뒤로는 아무도 다시 말하지 않는다:
  그 달을 되찾는 길은 사람이 주인의 패스를 그 달에 다시 돌리는 것 하나뿐이고, 그때까지 남는 증거는 죽은
  run 의 note 에 계속 붙어 있는 `rewriting=` 표식이다.

## 검색 (#28 → 포크 cosmai-import-ydc, upstream PR #59)
```
cosmai retrieval chunk  [--since <date>] [--source <s>]...
cosmai retrieval search --query <q> [--engine <e>] [--source <s>]... [--top <n>] [--vectors <path>]
cosmai retrieval eval   --mode <m> [--engine <e>] [--source <s>]... [--out <csv>] [--vectors <path>]
cosmai retrieval embed  [--model <m>] [--device <d>] [--batch <n>] [--vectors <path>]
cosmai retrieval terms  [--source <s>]... [--top <n>]
cosmai retrieval ask    --query <q> [--engine <e>] [--source <s>]... [--top <n>] [--model <m>] [--dry-run] [--vectors <path>]
  source ∈ {youtube_comment, youtube_video, youtube_transcript, commerce_review}
  engine ∈ {bm25, vector, hybrid}      mode ∈ {literal, heldout}
```
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
- **질의 토큰화는 색인 토큰화와 갈린다**(포크 #46). 색인은 `bm25.tokenize`, 질의는 `bm25.tokenize_query`
  — 같은 토큰화에 **질의 불용어 제거**만 얹은 것이고, `Index.search` 만 그쪽을 탄다. 색인에서 빼지 않는
  이유는 그러면 `소비자` 를 직접 찾는 질의를 못 하게 되기 때문이다. 뺄 근거가 lift 도 idf 도 아닌 이유는
  그 말들이 흔해서가 아니라 **질문을 서술하는 말이라 주제가 아니어서**다 — 통계로는 반대로 나온다(위의
  `소비자` 289 < `백탁` 338). 그래서 통계가 아니라 판단이고, 판단이므로 **버전을 받는 행으로 산다**:
  `needs.entity_lexicon` 의 `kind='stopword'` · `canonical='query'` 활성 버전이 정본이고, 고치는 길은
  `cosmai lexicon load/diff/activate --kind stopword` 하나다(적재 원본은 주제 사전과 같은 자리의
  `analysis/retrieval/dict/query_stopwords_v1.csv`). 그 kind 는 주제 사전과 **활성 버전이 따로**다 —
  `entity_lexicon` 의 `activate` 는 kind 하나만 켜고 끄므로(`db/lexicon.py` `ENTITY_ACTIVATE`), 질의
  불용어 개정과 aspect 사전 개정이 서로를 끄지 않는다. 버전 **번호표**는 그렇지 않다 —
  `formats.md` §entity 사전의 `kind='stopword'` 가 그 한계와 포크 #58 을 적는다.
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
- **질의마다 엔진을 고르는 라우터는 두지 않는다**(포크 #47). `--engine` 은 사람이 준 값 그대로 간다.
  ydc `v0.3.0` 의 규칙 라우터(`rag/router.py`)를 승격하지 않았고 그 근거 — 신호 넷 중 둘의 원천이 없어 갈래 둘이 막힌다 ·
  **성분명 판정의 정본이 토크나이저 사전이 아니다** · 우리 사전 위의 오라우팅 실측 — 은 `interfaces.md`
  §질의 라우팅 이 진다. 새 하위명령도 새 종료 코드도 늘지 않는다.
- **벡터 검색에 유사도 하한선을 두지 않는다**(포크 #48). `--engine vector`·`hybrid` 는 코사인이 얼마든
  상위 `--top` 을 채운다 — 넣지 **않기로 한** 것이고, 진짜 질의(주제 별칭 **61개** — 그 실측이 선 활성
  사전 v2 의 표본이다. 오늘 활성인 v3 는 63개이고, 다시 재기 전까지 그 표는 v2 판본의 기록이다)와 코퍼스에
  없는 성분명의 최고 코사인 분포가 갈리지 않는다는 실측이 그 근거다(`interfaces.md` §벡터 하한선). ydc `v0.3.0` 의
  `vector_threshold.py` 를 승격하지 않았고, 새 옵션도 새 종료 코드도 늘지 않는다.
- **대신 근거 없는 질의를 청크빈도로 막는다 — `vector`·`hybrid` 에만**(포크 #48,
  `analysis/retrieval/grounding.py`). 길이 4 이상인 질의 토큰 중 빈도가 0 인 것이 있으면 `search` 는 순위를
  매기지 않고 stderr 한 줄 + 결과 0건으로 답한다 — 코퍼스가 그 이름을 한 번도 말한 적이 없다는 뜻이라,
  결과가 나와도 그 이름과 무관한 문서다.
  **종료 코드는 이미 있는 `1`(결과 없음)이고 새 코드가 늘지 않는다.**
  토큰이 0개인 질의(`톤 업`·키릴 표기)는 빈도로 판정하지 않고 통과시킨다 — 막으면 벡터가 유일하게
  답하는 자리를 막는다.
- **`bm25` 의 동작은 이 이슈 전과 같다.** 어휘 검색은 빈도 0 인 낱말을 idf 0 으로 무시하고 **남은 낱말로
  답하므로**, 게이트를 걸면 "진짜 주제 + 코퍼스에 아직 없는 신제품 이름" 질의에서 예전에 나오던 부분 답이
  0건이 된다. 그 손해는 아무도 재지 않았고(질의 로그가 없다), 재지 않은 손해를 감수할 이유가 없다.
- 그래서 **`--engine vector` 도 BM25 색인을 연다**(게이트가 보는 빈도가 거기 있다). `bm25`·`hybrid` 가
  이미 내던 비용을 vector 쪽도 내게 된 것이고, 그 대가로 코퍼스에 없는 이름이 상위 k 를 채워 근거로
  인쇄되는 일이 없어진다. 캐시가 있으면 피클 한 벌, 없으면 38만 청크를 형태소 분석하는 십수 분인데 —
  **캐시는 `--source` 조합마다 따로다**(`pipeline.index_signature` 가 `sources` 를 문다). 좁혀 쓰는 vector
  검색은 지금까지 색인을 연 적이 없으므로 그 조합의 캐시가 존재한 적이 없고, **첫 호출이 무조건 십수
  분**이다. 벡터 파일이 없는 호스트는 그 비용을 치른 **뒤에야** blocked(2)를 본다 — 게이트가 저장소보다
  앞이다. `retrieval eval` 은 이 게이트를 타지 않는다 — 타더라도 주제 별칭 61개 중 막히는 것이 0개다
  (`interfaces.md` §벡터 하한선. 위와 같은 v2 판본의 표본이고, v3 가 더한 두 별칭은 아직 이 축으로 재지 않았다).
- **`--source` 는 후보를 좁힐 뿐 소스별 몫을 주지 않는다**(포크 #54). 좁힌 뒤에도 답은 남은 것 중 전역
  상위 k 다. ydc 는 소스마다 따로 뽑아 합치지만(색인의 92%가 짧은 댓글이라 `mfds` 가 293위로 밀렸다) 우리
  코퍼스에는 그 쏠림이 없다 — 지배 소스는 색인의 75.64% 인데 상위 10 은 71.11% 만 가져가고, 색인 6.06% 인
  `commerce_review` 가 상위 10 의 21.03% 다. 실측과 판정 기준은 `interfaces.md` §소스별 분배, 재는 길은
  `tool/measure-source-mix` 다.
- `terms` emits, as two stdout tables, the high-frequency nouns that dictionary **misses** and the
  document counts of the dictionary's own surfaces — material for a person to read and fix the CSV above.
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
- **기본 `--engine bm25` 는 literal 용도 기준이다** — heldout 에서 bm25 는 P@10 0.000·Hit 0%, vector 는
  0.062·25% 인데 literal 에서는 bm25 가 P@10 0.864 로 가장 높다(여섯 줄 전부는 `contracts/interfaces.md`
  §검색 실측). 탐색 용도의 기본값은 포크 이슈 #11 에서 정한다.
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
- 종료 코드: 0 ok · 1 partial(`chunk` 의 계약 위반, `search` 의 결과 없음 — 근거 없는 질의가 막힌 것도 여기다, `eval` 의 채점된 질의 0개와
  `terms` 의 훑은 문서 0건 — 둘 다 청크가 비었다는 뜻이다) · 2 blocked(연결 거절, 벡터 저장소를 읽을 수
  없음 — 파일이 없는 것과, 매니페스트에 `model`·`query_prefix`·`l2_normalized`·`dim` 이 빠졌거나 그것이
  행렬과 어긋난 것이 같은 자리다, **활성 주제 사전 없음** — `cosmai lexicon load/activate` 를 아직 안
  돌렸다는 뜻이라 실패가 아니라 막힘이다). `embed` 에는 partial 이 없다 — 전량 재인코딩이라 반쯤 된 저장소를 남기지 않고, 끝나면 0 이다.
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
- **평가 행은 저장소 판본을 어긋나지 않아도 싣는다** (포크 #49). `eval` 의 vector·hybrid 는 매니페스트의
  `model`·`revision`·벡터 수·`chunked_at_max` 를 CSV `store` 열과 stdout 요약 한 줄에 싣는다 — 바로 위
  커버리지 경고와 **축이 다르다**: 그쪽은 어긋날 때만 말하므로 정상일 때는 판본이 아무 데도 안 남고, 그
  자리가 ydc 에서 "1차 → 2차" 로 라벨한 델타가 실은 "식약처 벡터 없음 → 2차" 였던 사고다(`v0.3.0` 은
  산출 파일명에 판본을 붙여 고쳤다 — 우리는 파일이 아니라 행으로 내므로 같은 자리가 행이다).
  **판본 없는 행은 나올 수 없다**: 저장소를 못 열면 그 실행이 통째로 blocked(2)이고, `model` 이 빈
  저장소는 `load` 가 거절한다(위 blocked 항목). bm25 행은 비어 있다 — 저장소를 열지 않으니 지어낼 판본이
  없다. 이 열은 종료 코드를 바꾸지 않는다.
- **평가 행은 주제 사전 판본도 스스로 적는다** (포크 #62). `eval` 은 그 실행이 실제로 읽은 활성 사전의
  `ruleset`·`version`·주제 수·별칭 수·**내용 지문**을 CSV `dictionary` 열과 stdout 요약 한 줄에 싣는다.
  바로 위 저장소 판본과 **축이 다르고, 채우는 행의 집합도 다르다**: `store` 는 저장소를 여는 vector·hybrid
  에만 있지만 `dictionary` 는 **세 엔진 전부**에 있다 — 정답(`match_topics`)도 질의(주제 별칭)도 사전이
  만들므로 저장소를 안 여는 bm25 행도 사전 위에 서 있다. 번호표만 싣지 않는 이유는 **켜져 있는 버전에 행을
  더할 수 있어서**다(같은 이유로 `pipeline.index_signature` 도 번호와 지문을 함께 문다). 별칭 수는 `ko`+`latin`
  만 센다 — `mfds_inci` 는 매칭에도 질의에도 쓰이지 않아 함께 세면 한 낱말이 두 축을 말한다.
  **판본 없는 행은 나올 수 없다**: 활성 사전이 없으면 그 실행이 통째로 blocked(2)다(위 blocked 항목).
  이 열은 종료 코드를 바꾸지 않는다. 판본을 다시 찍는 길은 `tool/show-lexicon-stamp` 다.
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

## 분기 시계열 (포크 #5, ydc `trend.py` 승격)
```
cosmai trend quarter [--url <url>]
```
- It reads the active corpus snapshot (`corpus_snapshot.active`) and the active panel roster (the active
  version of `panel_channel`) and writes `needs.metrics_topic_quarter`. **Neither the snapshot nor the
  roster is an argument** — two ways of choosing means two denominators, and the place that picks the
  active version is one each: `db/corpus.active_snapshot` and `db/seed/panel.active_version` (the latter
  stops instead of answering when there are two active versions).
- 모집단은 매니페스트 규칙 그대로다: `content_type='video_long'` · `panel_role='product'` ·
  `topic_id='선크림'` 언급이 있는 영상, 그리고 그 영상들에 달린 댓글. 산출 행의 `scope` 는
  `metrics_need.scope` 와 같은 어휘(`선블록`)이고 `content_type` 은 `long_form` 이다.
- **One run rewrites the rows of that (run, scope, roster) wholesale.** Not updating in part is how the
  grid is kept dense — a re-run produces the same rows under the same `run_id` (found by the note).
- After writing it asks `needs.metrics_topic_quarter_violation` back about that run. If the view says
  anything, the exit code is **1** (partial) and stdout carries that line — the table stands, but what the
  table means differs from the contract.
- 종료 코드: 0 ok · 1 partial(위 불변식 위반) · 2 blocked(연결 거절, **활성 명부 없음**·**활성 스냅샷
  없음**, 모집단이 비어 산출할 행이 없음 — 셋 다 `db/seed --only panel`·`db/corpus load` 를 아직 안
  돌렸다는 뜻이라 실패가 아니라 막힘이다).
- `analysis_run.versions.metric` carries the definition version of those rows (`versioning.md`).

## 판정 (포크 #40, ydc `judge.py` 승격)
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
- 종료 코드: 0 ok · 1 partial(위 불변식 위반) · 2 blocked(연결 거절, 활성 명부·스냅샷 없음, **그 run 에
  지표 행이 없음** — `cosmai trend quarter` 를 아직 안 돌렸다는 뜻이라 실패가 아니라 막힘이다).
- `analysis_run.versions.judgement` carries the definition version of those rows (`versioning.md`).

## 민감도·후향 검증 (포크 #41, ydc `panel_sensitivity.py`·`backtest.py`·`spam_ad_flags.py` 승격)
```
cosmai trend sensitivity [--url <url>]
```
- It asks whether the conclusion **of the run** `cosmai trend quarter` produced wobbles under three
  choices: the panel composition (product only vs all 43 channels) · the cutoff (recounted as if only the
  past quarters were known) · ad and sponsorship marking (recounted with them removed). The run is found
  by **the same path** as `quarter` and `judge` (the note made from the active snapshot and the active
  roster) — the same reason there are no arguments.
- **아무것도 쓰지 않는다.** 세 측정이 만드는 행은 반사실 모집단의 것이고 022 의 `panel_role` 어휘에도
  `analysis_run` 에도 자리가 없다(`interfaces.md` §민감도). 답은 표가 아니라 stdout 이고, 읽기 전용이라 운영 DB 에
  그대로 돌린다. 저장된 표가 그대로인 것은 `tests/test_sensitivity_pipeline.py` 가 지문으로 붙든다.
- The baseline is recounted, and if that baseline differs from the stored `metrics_topic_quarter` rows
  that fact (`baseline_drift`) comes first — every difference this command reports is meaningless then.
- 종료 코드: **0 ok — 답이 계산됐다** · 1 partial(**이 산출을 믿지 마라** — `baseline_drift`, 또는 방향성 판정
  사례가 둘 미만이라 후향 검증이라 부를 것이 없다(`thin_backtest`)) · 2 blocked(연결 거절, 활성 명부·스냅샷·주제
  사전 없음, **그 run 에 지표 행이 없음** — `cosmai trend quarter` 를 아직 안 돌렸다는 뜻이라 실패가 아니라
  막힘이다. 코퍼스가 비었는데 지표 행만 남아 창이 설 분기가 없는 것(`ShortHistory`)도 같은 자리다).
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
- **아래 §근거·카드 의 `cards` 도 같은 자리다** — "규칙에 걸린 셀이 없다"는 발견이지 실패가 아니다.

## 근거·카드 (포크 #6, ydc `evidence_comments.py`·`cards.py` 승격)
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
- **후보를 읽자마자 커밋하고 그 뒤로는 DB 를 보지 않는다.** 근거는 판정과 달리 코퍼스를 훑는 단계라
  `needs_runtime` 의 `idle_in_transaction_session_timeout`(15초)에 그대로 걸린다 — 커서를 연 채 접으면
  끊긴다(`analysis/trend/pipeline.py` 와 같은 자리). 읽어 오는 것은 본문이 아니라 포인터와 좋아요뿐이고,
  전량에서 후보 15,602행 · 0.52s · 73MB 로 실제로 재 봤다 (`interfaces.md` §근거 "전량 실측").
- One run rewrites the evidence rows of that (run, scope, roster) wholesale — with a partial update the
  ladder of slots (rank) would silently develop holes.
- After writing it asks `needs.topic_quarter_evidence_violation` back about that run. If the view says
  anything, the exit code is **1** (partial) and stdout carries that line.
- 종료 코드: 0 ok · 1 partial(위 불변식 위반) · 2 blocked(연결 거절, 활성 명부·스냅샷 없음, **그 run 에
  판정 행이 없음** — `cosmai trend judge` 를 아직 안 돌렸다는 뜻이라 실패가 아니라 막힘이다).
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

## 대조 (포크 #7, ydc `source_composition.py`·`commerce_crosscheck.py`·`cross_source.py` 승격)
```
cosmai trend crosscheck [--url <url>]
```
- 네 소스를 나란히 놓고 어긋나는 자리를 찾는다: 구성(같은 사전으로 소스마다 주제 구성비) · 평가(커머스
  플랫폼의 속성 평가 대 그 run 의 판정) · 성분(성분 담론 셋과 성분 키 감사). 합산하지 않는다 —
  분모가 소스마다 다르다(`interfaces.md` §대조).
- **It writes nothing.** A row of the three answers is keyed by one (topic) or one (ingredient), while
  022's quarterly grain is keyed by eight columns and the commerce side has neither the quarter nor the
  roster among them. The answer is stdout rather than a table, and being read-only it is run against the
  production DB as it is. That the stored tables are untouched is pinned by a fingerprint in
  `tests/test_crosscheck_pipeline.py`.
- run 은 `quarter`·`judge`·`sensitivity` 와 **같은 길**로 찾는다(활성 스냅샷·활성 명부에서 만든 note) —
  인자가 `--url` 하나인 이유도 같다. 대조하는 분기는 그 run 격자의 **마지막에서 두 번째**다(마지막은
  판정이 `미확정(진행 중)` 으로 두는 진행 중 분기라 과소 집계된다).
- It walks the chunk index once — measured for real at 381,950 chunks, 48MB, **11.3 seconds** over
  everything (2026-08-27, keyset pages of 20,000 rows with a commit per page). Walking it in one stream
  hits `needs_runtime`'s `transaction_timeout` (60 seconds), so it uses the same method as
  `gold_from_chunks` in `analysis/retrieval/eval.py`.
- 종료 코드: **0 ok — 대조표가 계산됐다** · 1 partial(**이 산출을 믿지 마라** — 성분 키가 사람이 한 번
  읽어 금지한 성분명을 잡았거나(`key_mismatch`, §대조 의 `시카` 사고가 이 자리다), 커머스
  `topic_group` 이 가리키는 우리 주제가 활성 사전에 없다(`group_map_drift`)) · 2 blocked(연결 거절, 활성
  명부·스냅샷·주제 사전 없음, **그 스냅샷·명부에 지표 run 이 없음**(`cosmai trend quarter` 를 아직 안
  돌렸다), **그 run 에 판정 행이 없음** — `cosmai trend judge` 를 아직 안 돌렸다는 뜻이라 실패가 아니라
  막힘이다. 청크가 비었거나(`cosmai retrieval chunk`) 랭킹에 선케어 제품이 없는 것
  (`cosmai collect commerce`)도 같은 자리다 — 대조할 소스가 아직 없다). **여덟 갈래 전부** 코드의
  `NoPopulation`·`NoCrosscheck`·`NoDictionary` 셋 중 하나이고, 메시지가 어느 것인지 갈라 말한다.
- **"소스가 어긋난다"는 1 이 아니다.** 그것은 이 명령이 답하려고 존재하는 **발견**이지 실행의 실패가
  아니고, 이 파일 맨 위의 공통 규약에서 1 은 "산출이 온전하지 않다"는 뜻이다 — 위 §민감도 의 "흔들린다는
  1 이 아니다", §근거·카드 의 "규칙에 걸린 셀이 없다는 1 이 아니다"와 **같은 자리, 같은 문장**이다. 실측
  으로도 그렇다: 전량에서 13주제 중 어긋남 해석이 붙는 주제가 여럿이라(예: `백탁` 커머스 9.80% 대 댓글
  1.55%) 1 로 내면 평상 상태가 실패로 읽힌다. 어긋남은 종료 코드가 아니라 표의 `reading` 열과 `note` 가
  싣는다.
- **Thin evidence is not a 1 either.** A topic with fewer than `MIN_PRODUCTS` (5) attribute-rated products
  gets no reading written, and `thin=` in the `note` counts them. Thin is a computed answer, not a
  truncated output.
- It is safe on cron (read-only, and 0 is the normal state). But the answer changes only when the corpus
  or the collection changes, so for now a person asks it once and records it on the issue.

## 홀드아웃 (포크 #51, ydc `holdout_commerce.py` 승격)
```
cosmai trend holdout [--url <url>]
```
- **새로 쌓인 커머스 리뷰로 기존 결론을 되묻는다 — 숫자를 갈아치우지 않는다.** 같은 모집단(§대조 의
  선케어 랭킹 술어)의 리뷰를 두 팔로 가르고 같은 코드로 센다: 청크 색인에 있는 리뷰(`seen`, 우리가 본
  것)와 없는 리뷰(`holdout`, **한 번도 안 본 것**). 갈리면 왜 갈리는지를 창·플랫폼 구성·제품 바스켓
  셋으로 갈라 잰다(`interfaces.md` §홀드아웃).
- **Why the only argument is `--url`**: the cutoff is not a date but the roster of commerce `doc_id`
  values in `needs.retrieval_chunk` — two ways of choosing means two denominators (the same convention as
  `quarter`·`judge`·`crosscheck`). ydc took a `--cutoff`, but that repo had no row for "what have we
  seen".
- **It writes nothing.** A row of this answer is keyed by (arm, topic), and the boundary of `arm` is the
  chunk index, so it moves every time `cosmai retrieval chunk` runs (today's holdout is tomorrow's seen).
  The answer is stdout rather than a table, and being read-only it is run against the production DB as
  it is. That the stored tables are untouched is pinned by a fingerprint in
  `tests/test_holdout_pipeline.py`.
- **네 읽기(커머스 청크 명부 · 리뷰 키 명부 · 빈 본문 수 · 모집단)는 한 트랜잭션 스냅샷
  (`REPEATABLE READ`) 안에서 한다.** 밖에 두면 수집기가 도는 동안 그 넷이 서로 다른 모집단을 가리켜
  `seen + holdout + empty` 가 어떤 모집단의 크기도 아니게 된다. ydc 가 손으로 얹은 정지·전순서
  정렬·행수 대조 셋이 여기서 각각 어디로 가는지는 `interfaces.md` §홀드아웃 의 표가 든다 — 그 셋을
  그대로 옮기면 이 자리에서는 항등식이라 검사가 아니다.
- 종료 코드: **0 ok — 답이 계산됐다(재현되지 않아도 그렇다)** · 1 partial(**이 산출을 믿지 마라** —
  원천 리뷰가 없는 커머스 청크가 있다(`chunk_orphan`). 청크에는 외래키가 없으므로(020) 그때 기존 팔은
  분석이 실제로 본 그 팔이 아니다) · 2 blocked(연결 거절, 활성 주제 사전 없음, 랭킹에 선케어 제품이
  없음(`cosmai collect commerce`), 커머스 청크가 하나도 없음 — **기준 시점 자체가 없다는 뜻이라**
  `cosmai retrieval chunk` 를 돌려라, 선케어 모집단 안에 청크가 있는 리뷰가 하나도 없음 — 비교할 기존
  팔이 없다, **안 본 리뷰가 하나도 없음** — 되물을 새 표본이 아직 없다). **여섯 갈래 전부** 코드의
  `NoHoldout`·`NoDictionary` 둘 중 하나이고, 메시지가 어느 것인지 갈라 말한다.
- **"재현되지 않는다"는 1 이 아니다.** 그것은 이 명령이 답하려고 존재하는 **발견**이고, 이 파일 맨 위의
  공통 규약에서 1 은 "산출이 온전하지 않다"는 뜻이다 — 위 §민감도 의 "흔들린다는 1 이 아니다", §대조 의
  "어긋난다는 1 이 아니다"와 **같은 자리, 같은 문장**이다. 출처인 ydc 의 `report` 도 언제나 0 을 낸다.
  어느 갈래인지(`재현`·`순위 재현`·`순위 변동`·`순위 없음`)는 `note` 와 표가 싣는다.
- **A thin sample is not a 1 either.** A topic whose seen arm has fewer documents than `MIN_MENTIONS` (5)
  gets no rank (`-`), and `ranked=` in the `note` counts how many topics do have one.
- **크론에 걸지 않는다. 위 §대조 의 "0 이 평상 상태" 가 이 명령에서는 거짓이다.** 읽기 전용인 것은 같지만
  평상 상태가 종료 코드 하나로 서지 않는다: `cosmai retrieval chunk` 가 돌면 홀드아웃이 통째로 기존 팔로
  넘어가 **그 직후의 평상 상태는 `2`(`no unseen sample`)** 이고, 커머스 수집기가 다시 쌓을 때까지 그렇다.
  종료 코드로 알람을 보는 크론은 그 구간을 실패로 읽는다. `stack/crontab.d/` 에 `retrieval chunk` 를 도는
  줄은 **없으므로**(2026-08-27 실측) 오늘의 위험은 크론이 아니라 **손 순서**다 — 되묻기 전에 청킹부터
  돌리면 물음 자체가 사라진다. 이 명령은 사람이 한 번 물어 이슈에 남긴다.

## 스케줄 (stack/crontab.d/, UTC)
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

**둘 다 상한이 아니라 하한이다.** 위 계산은 정책이 *선언한* 페이스를 쓰는데, `Gate._back_off`는 사이트가
403·429·503으로 답하면 살아 있는 인터벌을 `Gate.MAX_INTERVAL_S`(300초)까지 벌린다 — daisomall의 30초가
300초가 된다. 응답 지연과 재시도도 값에 없고, `max_requests_per_run`이 없는 소스는 예산 기준에서도
씨드 수로만 계산된다(#10 이후 네 소스 모두 선언하므로 오늘 그런 소스는 없다). 레인 산술도 마찬가지로
낙관적이다: 실제 실행은 레인을 등록 순서대로 나눠 주지 긴 소스부터 주지 않으므로, 소스가 레인보다 많은
줄은 위 두 하한 중 어느 쪽보다도 오래 걸릴 수 있다. 그러니 이 숫자는 "적어도 이만큼"이지 "많아야 이만큼"이 아니다. 겹치지 않는다는 보장은
간격이 아니라 락이 준다. `analyze all`은 외부 fetch가 없는 DB 전용 작업이라 매시 실행과 겹쳐도
무해하므로 이 규칙에서 제외된다. 다만 `analyze all` 에도 간격 규칙이 하나 있고 그것은 락이 세운다:
같은 락을 쓰는 주인의 극성 패스와 겹치면 뒤에 온 쪽이 그 밤을 통째로 건너뛰므로, 그 줄과 `0 5` 사이의
간격은 그 패스의 최악 소요 T 보다 넓어야 한다. **그 줄은 이제 있다**(`0 8`, #32) — 명령은 증분
(`--missing`, #98)이고 **그 T 는 아직 미측정이다**: 운영 실행이 조정자의 몫이라 줄을 넣은 작업이 재지
못했다. 그래서 시각은 T 를 아는 대신 간격을 최대로 벌려 골랐다 — `0 8` 은 `0 5` 에 3h 를 주고 자기는
21h 를 받는다. 실측이 있는 유일한 값은 전량 패스의 것이고(선블록 하나를 도는 run 16 이 **6h44m**),
21h 는 그 3배다. 조정자가 첫 밤과 정상 밤의 T 를 재고, 21h 를 위협하면 이 두 시각을 옮긴다. 계산과
GPU 창(08:00–16:00 UTC, `retrieval embed` 가 피한다)은 `stack/crontab.d/analyze` 에 적혀 있다.

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
0 8 * * *   cosmai analyze polarity --impl ollama:gemma4:latest --missing
youtube: watch 1h · work 5m · flatten 15m · prune 1d  (after the fan-out cap is applied)
naver:   datalab once a month (by the keyword dictionary) · blog once a month
```
