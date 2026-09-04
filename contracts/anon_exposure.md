# anon 노출 — PostgREST `postgrest_anon` 이 읽는 관계

A single `shared-db-postgrest-1` serves three schemas of database `app` on `0.0.0.0:3000`
(`PGRST_DB_SCHEMAS=trend_radar,tubedepth,needs`, `PGRST_DB_ANON_ROLE=postgrest_anon`,
`PGRST_OPENAPI_MODE=follow-privileges`, `Access-Control-Allow-Origin: *`). What makes it an
anonymous, unauthenticated GET and read-only is `postgrest_anon`'s privileges, not PostgREST's settings.

**Today it is 23** = `needs` 11 + `trend_radar` 9 + `tubedepth` 3. The three sections below name
every one of them. The comparison is `db/grants/postgrest_anon_check.sql` (read-only, seven
sections) and `tests/test_anon_exposure_contract.py`.

`#168` option B (user decision 2026-08-27) was **applied by the coordinator session on 2026-08-27** --
`db/grants/postgrest_anon_old_stack.sql`, one command at a time. Before it there were 36
(`trend_radar` 13 · `tubedepth` 12). Measured after: all 23 that were kept answer 200 and everything
that was blocked answers 401 (`trend_radar` `review`·`run`·`fetch_log`·`run_source` · `tubedepth`
`comments`·`transcripts`·`jobs`·`worker_control`). The seven lines that undo it sit at the tail of
that file -- they restore `relacl` and `nspacl` exactly as they were before.

## 좁히기 전 문이 셋이었다

| schema | the path it opened before the narrowing | where |
|---|---|---|
| `needs` | a whitelist GRANTed directly to `postgrest_anon` | `db/grants/postgrest_anon_needs.sql` + `db/views/pipeline_health.sql` (this repo) |
| `trend_radar` | **role membership** `GRANT trend_radar_reader TO postgrest_anon` | `service/stack/init/20-postgrest-roles.sh:34` (old stack, archived) |
| `tubedepth` | **a direct GRANT `ON ALL TABLES` + DEFAULT PRIVILEGES** (`api_keys` alone REVOKEd) | `service/stack/init/40-postgrest-tubedepth-grants.sh:12-16` (old stack, archived) |

The last two are outside this repo and `db/migrate.sh` does not touch them. That is why the
"Whitelist, not default privileges" of `postgrest_anon_needs.sql` was true of the `needs` section
alone -- `ALTER DEFAULT PRIVILEGES` was alive in both `trend_radar` and `tubedepth`, so a new table
opened to anon by itself.

**Now there is one door.** `postgrest_anon` belongs to **no role at all**, `trend_radar_reader`
included, and takes tables by name in all three schemas. Adding a table means adding a line to
`db/grants/postgrest_anon_needs.sql` for `needs` and to `postgrest_anon_old_stack.sql` for the other
two -- no migration can open one quietly.

### 보이려면 둘이 다 있어야 한다: SELECT 와 USAGE

"What anon sees" does not follow from a table's `SELECT` alone. Without schema `USAGE` PostgREST
answers **401**, and that schema is worth zero tables however many were GRANTed. `has_table_privilege`
returns `t` regardless of schema privileges, so **counting tables does not show this hole.**

The path by which anon gets USAGE differed per schema too (`pg_namespace.nspacl`, measured 2026-08-27):

| schema | USAGE path | if the membership is cut |
|---|---|---|
| `needs` | `postgrest_anon=U/needs_owner` — direct | unchanged |
| `tubedepth` | `postgrest_anon=U/tubedepth_owner` — direct | unchanged |
| `trend_radar` | `trend_radar_reader=U/trend_radar_owner` — **inherited through the membership** | **it goes with it** |

So the narrowing gives `GRANT USAGE ON SCHEMA … TO postgrest_anon` back to `trend_radar` alone.
Right after the 2026-08-27 application this one line was missing and all 9 `trend_radar` relations
answered 401 -- while the table count (section 2) read the intended `9`. Section 6 of
`db/grants/postgrest_anon_check.sql` has measured USAGE separately ever since, and all three schemas
must show `usable = t`.

The reason only `trend_radar` needs something given back is the **same asymmetry** as the DEFAULT
PRIVILEGES section below: this schema's privileges hang on a role, `trend_radar_reader`, and anon was
that role's guest. `tubedepth` was addressed to anon from the start.

### DEFAULT PRIVILEGES 는 두 스키마를 다르게 다룬다 (사용자 결정 2)

The same drift, but the prescription is opposite **because the beneficiary differs**. Measured on `pg_default_acl`:

| schema | beneficiary of the default privileges | what the narrowing does |
|---|---|---|
| `trend_radar` | `trend_radar_reader=r/trend_radar_owner` — not anon | **leaves it.** Cut the membership and anon inherits none of that role's default privileges, so the drift has already stopped |
| `tubedepth` | `postgrest_anon=r/tubedepth_owner` — anon directly | **removes it.** Left in place, the tables the next migration makes attach to anon as they are |

Removing the `trend_radar` one is out because `trend_radar_reader` is, **before** it is anon's
corridor, the role `trend-radar-dashboard` logs in with directly
(`service/stack/docker-compose.yml:172`, `rolcanlogin=t`). Dropping the default privileges would
leave that screen unable to read tables made in this schema from now on -- which goes past the
decision's own grounds, "stop the drift without changing what is open today".

So after the application `pg_default_acl` keeps **one `trend_radar` row rather than zero rows**
(section 4 of `db/grants/postgrest_anon_check.sql` writes that expectation down).

## needs

Eleven. The narrowing does not touch this schema -- before and after are the same. This repo GRANTs
them and the test compares against a real database's `has_table_privilege`.

`needs.metrics_need` · `needs.metrics_wish` · `needs.product_ref` · `needs.analysis_run` ·
`needs.entity_lexicon` · `needs.aspect_lexicon` · `needs.pipeline_stage` · `needs.pipeline_edge` ·
`needs.pipeline_health`(view) · `needs.mention_lineage`(view) · `needs.collection_lineage`(view)

The last two are opened by the `#144` lineage drill-down (`db/views/mention_lineage.sql:149` ·
`collection_lineage.sql:191`). The portal calls them when it descends from one metric cell to the
mentions and from a mention to what was collected (`portal/public/app.js:351,441`).

**`mention_lineage` does not emit original text -- only a 120-character excerpt** (`sentence_excerpt` ·
`doc_excerpt`, view file lines 125 and 132; the full length sits alongside so the fact of truncation
cannot hide). The original-text columns do not even go out by name. User decision 2026-08-27. The
point of that truncation is to keep **this view from becoming a delivery path for original text**, not
that anon cannot see review bodies -- that line is already gone on the `trend_radar.review` side, and
handling it is what this document and `#168` are for.

Two closed ones worth naming: `need_mention` · `labeled_set` -- they hold collected original
sentences, so they are outside the whitelist. `corpus_*` · `*_mention` · `retrieval_chunk` ·
`topic_quarter_*` are the same.

`entity_lexicon` · `aspect_lexicon` are **open but no screen calls them** (`#168` survey 2026-08-27:
zero references across `portal/`). They are rule dictionaries, so sensitivity is low and they are
outside this decision's scope; they stay as they are -- whether to narrow them is a separate matter.

## trend_radar

Nine. Only aggregated facts remain. This schema alone also receives `GRANT USAGE ON SCHEMA` --
cutting the membership takes USAGE with it (the section above).

`trend_radar.product` · `trend_radar.rank_snapshot` · `trend_radar.price_point` ·
`trend_radar.new_product` · `trend_radar.new_products_view` · `trend_radar.review_stats` ·
`trend_radar.review_topic` · `trend_radar.review_answer` · `trend_radar.review_summary`

좁히기가 닫은 넷: `review`(리뷰 **전문** body, 30,044행 — `#144`·`#168` 이 겨눈 노출 그 자체) ·
`run` · `run_source` · `fetch_log`(수집 운영 기록이지 데이터가 아니다). `alembic_version` 은
전부터 닫혀 있었는데 정책이 아니라 순서였다 — DEFAULT PRIVILEGES 보다 먼저 만들어졌다.

No screen ever called this schema through PostgREST. `trend-radar-dashboard` becomes
`trend_radar_reader` via `TREND_RADAR_READONLY_DATABASE_URL` and attaches to the DB **directly**
(`service/stack/docker-compose.yml:172`), bound to `127.0.0.1:8000` alone -- so cutting the
membership did not affect that screen. It is also why that role's DEFAULT PRIVILEGES were left alive
(the section below).

## tubedepth

Three. Only video, channel and listing metadata remain.

`tubedepth.video_snapshots` · `tubedepth.channel_snapshots` · `tubedepth.listing_entries`

좁히기가 닫은 아홉: `comments`(댓글 원문 285,749행) · `transcripts`(자막 전문 5,303행) ·
`jobs`(337,201행) · `artifacts` · `worker_control` · `lane_health` · `source_health` ·
`flatten_progress`(수집기 내부 상태) · `alembic_version`(마이그레이션 원장).

`api_keys` was closed before and the narrowing does not open it either.

This schema is the evidence of the drift: the 2026-08-21 measurement
(`service/data-portal/docs/postgrest-observed.md:60`) had 6, and the 6 that later migrations made
rode DEFAULT PRIVILEGES in quietly to make 12 -- with nobody deciding. `comments` and `transcripts`
opened that way. The narrowing deleted those default privileges, so it cannot happen again.
`tubedepth-api` attaches as `tubedepth_runtime`, so it has nothing to do with anon, and it lives on
`127.0.0.1:8080` alone.

## 유일한 소비자

`data-portal` (`0.0.0.0:3001`) has no fixed table list. It picks a schema with `Accept-Profile`,
draws the `definitions` key of the OpenAPI document as the table list as it stands
(`public/app.js:99,113`), and lets you page a chosen table to the end at `PAGE_SIZE=1000` and take it
as CSV or JSON. That is, **what is open to anon is this screen's feature set**, so a whitelist of
"the tables this screen calls" is undefined. What the narrowing did to this screen is not a fault but
a **reduction in features** -- the `trend_radar` list went from 13 to 9 and `tubedepth` from 12 to 3,
and what remains is still taken as before.

`cosmai-portal-1` (`0.0.0.0:3003`) calls `needs` alone. The four old `cosmai-*` services and
`tubedepth-api` do not use PostgREST. The `0.0.0.0` bind itself is not this file's subject (user
decision 3, separate).
