# Version rules

- `linker_version`, `extractor_version`, `polarity_version` and `aggregate` are package constant strings. Format `rule-vX.Y` or `llm-<model>-<yyyymmdd>`.
- A change to what an output row means (a dictionary version bump included) raises the version and, for the same input, **adds a new-version row** (tables carrying the version in the natural key) or recomputes (for tables without it in the key, `analyze` regenerates that src range).

## Machine-readable inventory (#170)

This block is the executable part of this contract. `version_columns` inventories every semantic
`version`/`*_version` column on a `needs` base table; `natural_key` says whether at least one primary or
unique key contains that column. The migration ledger's `needs.schema_migration.version` is operational
bookkeeping, not a semantic version, and is the one explicit exclusion. `a19_metrics` lists the only
version-shaped columns those run-keyed metric tables may carry: `panel_version` identifies a population,
not the definition that made a metric. The definition versions stay on `analysis_run.versions`.

`analysis_run_version_keys` is the registry of version-bearing keys. A writer names the source file where
the key must occur; a constant, where present, is imported and checked against the declared format.
`launch` is reserved until the first run reads the launch rule table, as the prose below explains. A run
does not carry every key: the core analysis pass writes the first five together, while later or live-lineage
stages add their own keys. Evaluation-only payload such as `scores` is not a version key and is outside this
registry.

```json
{
  "version_columns": {
    "needs.aspect_lexicon.version": {"type": "integer", "natural_key": true},
    "needs.brand_mention.linker_version": {"type": "text", "natural_key": true},
    "needs.entity_lexicon.version": {"type": "integer", "natural_key": true},
    "needs.metrics_topic_quarter.panel_version": {"type": "integer", "natural_key": true},
    "needs.naver_run.collector_version": {"type": "text", "natural_key": false},
    "needs.need_mention.extractor_version": {"type": "text", "natural_key": true},
    "needs.need_mention.polarity_version": {"type": "text", "natural_key": false},
    "needs.panel_channel.version": {"type": "integer", "natural_key": true},
    "needs.panel_roster.version": {"type": "integer", "natural_key": true},
    "needs.price_event.aggregate_version": {"type": "text", "natural_key": false},
    "needs.product_denominator.aggregate_version": {"type": "text", "natural_key": false},
    "needs.product_launch_evidence.axis_version": {"type": "text", "natural_key": false},
    "needs.product_line_mention.linker_version": {"type": "text", "natural_key": true},
    "needs.product_ref.linker_version": {"type": "text", "natural_key": false},
    "needs.product_ref_candidate.linker_version": {"type": "text", "natural_key": true},
    "needs.rank_daily.aggregate_version": {"type": "text", "natural_key": false},
    "needs.topic_quarter_evidence.panel_version": {"type": "integer", "natural_key": true},
    "needs.topic_quarter_judgement.panel_version": {"type": "integer", "natural_key": true},
    "needs.wish_mention.extractor_version": {"type": "text", "natural_key": false}
  },
  "excluded_operational_columns": ["needs.schema_migration.version"],
  "a19_metrics": {
    "needs.metrics_need": [],
    "needs.metrics_wish": [],
    "needs.metrics_topic_quarter": ["panel_version"]
  },
  "analysis_run_version_keys": {
    "linker": {"format": "run-version-list-or-null", "constant": "analysis.linker.LINKER_VERSION", "writer": "analysis/pipeline.py"},
    "extractor": {"format": "run-version-list-or-null", "constant": "analysis.extractor.VERSION", "writer": "analysis/pipeline.py"},
    "polarity": {"format": "run-version-list-or-null", "constant": "analysis.polarity.VERSION", "writer": "analysis/pipeline.py"},
    "aggregate": {"format": "run-version-list-or-null", "constant": "analysis.aggregate.AGGREGATE_VERSION", "writer": "analysis/pipeline.py"},
    "lexicon": {"format": "positive-integer-or-object", "writer": "analysis/pipeline.py"},
    "metric": {"format": "exact", "value": "v0.2", "constant": "analysis.trend.METRIC_VERSION", "writer": "analysis/trend/pipeline.py"},
    "judgement": {"format": "exact", "value": "v0.2", "constant": "analysis.judge.JUDGEMENT_VERSION", "writer": "analysis/judge/pipeline.py"},
    "evidence": {"format": "rule-vX.Y", "constant": "analysis.evidence.EVIDENCE_VERSION", "writer": "analysis/evidence/pipeline.py"},
    "launch": {"format": "rule-vX.Y", "constant": "analysis.launch.LAUNCH_VERSION", "writer": null},
    "snapshot": {"format": "positive-integer", "writer": "db/corpus/match.py"},
    "cutoff": {"format": "iso-8601", "writer": "db/corpus/match.py"},
    "topics": {"format": "positive-integer-or-null", "writer": "db/corpus/match.py"}
  }
}
```

Everything else in this file is prose-only unless it explicitly points back to that block. Historical
measurements, rationale, incident narratives, temporary production state, and the validity window of a
reproduction claim are explanations, not proxy assertions.
- Since 2026-08-24 (005) `need_mention` is **on the version-in-the-key side**: `UNIQUE INDEX (src, ref, need_key, extractor_version, md5(sentence))`. The same sentence under two different `extractor_version` values coexists as two rows, so the seed (`slice-*`) and the analysis (`rule-v*`) never fight over one slot. Clearing away a run's own older version rows is still `analyze`'s DELETE (`extractor_version LIKE 'rule-v%'`).
- `sentence` enters the key as `md5(sentence)` rather than as the original: an unbounded `text` in a btree key lets a long review pass the 2704B row ceiling and stop the whole run (#5, demonstrated in production).
- `needs.analysis_run.versions` records every version of that run. The screen looks at the latest run alone.
- `needs.analysis_run.versions.lexicon.entity` is an object from every `entity_lexicon.kind` with active
  rows to that kind's sole active integer version. Kinds with no active rows are absent, and a newly active
  kind appears without a code list. More than one active version for a kind is ambiguous and the writer
  refuses it before opening or reviving a run; it never chooses the maximum. Historical runs retain two
  display-only numeric shapes: `versions.lexicon.entity` from combined/polarity runs and top-level
  `versions.lexicon` from standalone aggregate runs. A future semantic reader must treat either number as
  a legacy global label, not infer any kind's version from it.
- The keys of `needs.analysis_run.versions` are `{linker, extractor, polarity, aggregate, lexicon, metric, judgement, evidence}`, and since #282 `launch` beside them (its own line below). `metric` is **the definition version of the quarterly grain** and its value is `v0.2`, carried over as it stands from `METRIC_VERSION` in ydc `trend.py` — it is an exception to the two formats above (`rule-vX.Y`, `llm-…`) because it is not an implementation's version but the name of the agreement document the five formulas came from (TEAM_DECISIONS_v0.2). Under A19 `metrics_topic_quarter` has no definition-version column (`panel_version` names its population), so the only place that answers which definition made a row is this one key, the one `run_id` points at. 001's comment predates this list and names five alone; the DDL is additive only, so it is not edited — `versions` is jsonb, so adding a key needs no migration (fork #5).
- `judgement` is **the definition version of the verdict** and its value is `v0.2` for the same reason as `metric` — it is the name of the agreement document (TEAM_DECISIONS_v0.2 §3) the seven type names, the verdict order and the five constants (`TAU`·`DIFFUSION_TAU`·`EVIDENCE_FLOOR`·`W_EVIDENCE`·`W_SCORE`) came from, so it is an exception to the two formats. Standing **apart** from `metric` is meaning: changing the verdict criteria without recomputing the metrics is why this step was split off, and this one key is what moves then. A verdict row uses the same `run_id` as its metric row, so one run's `versions` carries both keys. ydc `judge.py` writes the same fact per row in `tau`, `diffusion_tau` and `metric_version` columns; under A19 that place is the run (fork #40).
- `evidence` is **the definition version of evidence selection** and its value is `rule-v0.1` — that it is
  not an exception to the two formats, unlike `metric` and `judgement`, is the meaning: those two are the
  name of an agreement document (TEAM_DECISIONS_v0.2) while this is the version of four rules the code fixed
  (quality flags · creator exclusion · the topic's origin · the tie-breaking second key,
  `interfaces.md` §Evidence), so it is carried by the implementation rather than by team agreement. An
  evidence row uses the same `run_id` as its verdict row, so one run's `versions` carries three keys. Cards
  make no rows and so have no key (fork #6).
- `launch` is **the version of the launch-evidence rule table** and its value is `rule-v1.1`
  (`analysis.launch.LAUNCH_VERSION`, `interfaces.md` §Launch evidence, #282) — not an exception to the two
  formats, for `evidence`'s reason: it is the version of rules the code fixed, not the name of an agreement
  document. What it covers is the whole read, because all of it changes what a verdict means: the rule
  table's rows and their order, the 3·6·12-month windows, the precision widening, the reference-date clamp
  on the interval's upper end, **how the interval's bounds are folded out of the claims**, the `exact` gate
  a tier passes, and which axes this version leaves out of the verdict (`EXCLUDED_AXES`). It stayed
  `rule-v1.0` through the review round of #282: the key exists so a stored value names the table that made
  it, and since no run has stamped it and no row was ever computed under the reviewed table, a bump would
  name a version that produced nothing. **`rule-v1.1` is the grade-A review of #283**, and it is a bump
  rather than an edit in place because it moves two answers that #282's merged table gave: lower bounds
  now fold **per axis by the earliest** before the tightest-across-axes is taken (several filings of one
  product line are one statement, and only its first is certainly true), and the `exact` gate on the
  deciding lower bound applies **whatever the basis** (an upper bound corroborates that the product
  existed, not that the lower bound names the right product). The addendum from the review of #285 is the
  third: `not_new` needs an **`exact`** upper bound, because a `partial` one can be false rather than
  weak — a sibling line sharing a DataLab term, a member-linked review — and declaring a product old on
  it is a confident wrong answer where the other rows only lose a tier. Still no run has stamped the key, so the
  first value it carries in the database is `rule-v1.1` and nothing computed under v1.0 exists to
  compare. An axis's own `axis_version` is a different thing
  and lives on the claim row — a claim is evidence and survives a rule change, which is why the ledger is a
  table and the rule table is not. The run that reads the verdict stamps this key (#125's
  `unresolved_new`), so two runs under different rule tables are never compared unmarked; the ledger's rows
  are not restated when it moves.
- Exception (A19): `metrics_need`·`metrics_wish` carry no `*_version` column, and `metrics_topic_quarter`
  carries only the population identity `panel_version`, not a definition version — the `run_id` in each
  natural key points at `analysis_run.versions`, and that is every definition version that made the row.
  `product_denominator`·`rank_daily`·`price_event`, which never go through a run, do carry
  `aggregate_version` (002).
- Reproduction (a corollary of A19, #144): the mention set that made one `metrics_*` row is retraced from the version list in `analysis_run.versions.extractor` (`;`-separated) and that row's axes (`scope`·`need_key`·`month`·`product_ref`) alone — except that a `scope='all'` rollup row's `need_key` is already the name folded through `needs.need_key.canonical` (A17), so that one column has to be matched on the canonical rather than on the raw `need_mention.need_key`, or the folded synonym mentions drop out wholesale — and `versions.polarity` is not matched alongside, because one `extractor_version` can hold two polarity versions and it is the version that run's polarity step used rather than a population. The retrace holds **only while no `analyze` run has finished after that one**: `analyze polarity` deletes and re-inserts per `(src, month)` (`analysis/polarity/pipeline.py`) and leaves neither a time window nor a watermark, so a metrics run after it cannot restore the population. In such a cell the screen writes "a run rewrote the mentions after this run" instead of a mention list — it does not show a quietly wrong list.
- `needs.panel_roster.version` (one row per version) and the `panel_channel.version`·`metrics_topic_quarter.panel_version` that point at it by FK are **integers** — an exception to the string rule above (`rule-vX.Y`), and the shape of `version`·`active` on the dictionaries (`entity_lexicon`·`aspect_lexicon`). A panel changes by seed rather than by code, so its version attaches per load (fork #3, values in #31).
- The evaluation sets (`labeled_set`) have no version. When a label changes it is replaced by a new row with a new `labeled_at` and `labeler`.
- `trend_radar` and `tubedepth` have **no version of their own**. Their canonical form is
  `contracts/ddl/current/app.<schema>.sql` (a `pg_dump --schema-only` baseline) plus every
  `contracts/ddl/<schema>/NNN_*.sql` applied in filename order — that composition is the schema, and
  the two dumps are never re-dumped to fold a change back in, or applying the additive file again
  would fail on a column that already exists. Their `alembic_version` table is part of the baseline
  and holds no row: the old repos that ran alembic against these schemas are archived, nothing in
  this repo writes that table, and a rebuilt database is therefore not a database alembic could
  resume. `db/migrate.sh` step (0) composes each schema on a database that has neither and leaves a
  database that has them alone (#178); that question is asked of `alembic_version` rather than of the
  schema name -- the table is the baseline's own marker, so a schema standing without it is a build
  that died part-way and not a schema to leave alone. Each of the two does carry a ledger of its own
  since #223, `<schema>.schema_migration`, the same shape as `needs.schema_migration`: step (0) seeds
  it from the files it applied when it builds a schema, and step (0b) applies to an already-present
  schema every file that ledger does not name. Without it the skip above swallowed a file added after
  the schema existed -- measured on production 2026-09-12, `tubedepth/004` and `/005` had been on
  main for a week and neither column was there. The ledger is not a version: it records which files
  ran, and the composition above is still what the schema *is*. A database that predates the ledger
  gets its first rows from `contracts/ddl/<schema>/applied_before_the_ledger.txt`, a one-time record
  of what was already applied that is read only where the schema is present and has no ledger yet,
  and that is never appended to afterwards.
- DDL file number blocks: upstream holds `contracts/ddl/needs/006~019` and the fork `cosmai-import-ydc` holds `020~`. Someone else's number in the ledger (`needs.schema_migration`) is harmless to a deploy because `db/migrate.sh` walks only the files in the checkout — instead that object is declared in `tool/checks/ddl-drift`'s exclusion list (#75).
- `needs.analysis_run.versions` gains three keys for the live lineage, declared by fork #94 and written by fork
  #95 and #96: `snapshot`, the `corpus_snapshot.snapshot_id` the run read; `cutoff`, the
  `corpus_document.collected_at` ceiling it applied — the same cutoff gives the same answer, which is what
  replaces a frozen copy (#93 D2); and `topics`, the dictionary version whose match produced that run's
  mentions. `versions` is jsonb, so none of the three needs a migration. The archive's run predates all three
  and carries none of them: its snapshot is named inside `analysis_run.note`, and `needs.archive_run` resolves
  it from there rather than from `versions`.
- `corpus_snapshot.instrument.dictionary_fingerprint` (fork #96) rides with `dictionary_version`, because rows can
  be added to an active dictionary version without its number moving; a change of either re-matches that
  snapshot. `trend quarter` writes `snapshot` · `cutoff` · `topics` only when run with a cutoff (the live chain),
  and `judge` takes the quarter in progress from `versions.cutoff` — the UTC calendar quarter of it — when
  present, and the last quarter with rows otherwise, which is the archive's run and keeps its verdicts unchanged.
- `corpus_snapshot.instrument.matcher_version` (fork #97) is the third key of that trigger: the fingerprint hashes
  dictionary rows, not the matching code, so every change to how a term matches bumps
  `analysis.retrieval.topics.MATCHER_VERSION` and re-matches live snapshots. Version 1 is the ungrouped latin
  alternation ydc's matcher also had; 2 guards every alternative on both sides.
- The ydc import pin is **`v0.4.0` `76db718`** of `shk95-1/cosmai-ydc-old` (formerly `slopindustries/youtube-data-collector`), the last commit of that repository — the marker for "seen up to here": every commit up to that tag carries a disposition in the ledger on goal `shk95/cosmai-import-ydc#1` (fork #52). The pin is not the promotion source. Each promoted module's header names the ydc tag its rules were copied from — `v0.1.0` `02440ab` for the v0.1.0 lineage, `v0.3.0` `e5a1b00` for `cross_source.py` · `holdout_commerce.py` · `vector_threshold.py` — and `tests/test_ydc_pin.py` checks that this line names one pin, that every header tag is one of the four ydc tags, and that none is newer than the pin. Nothing in this checkout compares the code against the ydc repository itself: `tool/compare-ydc-*` read the ydc checkout `--ydc` points at, on demand, and a change to the original after the pin is caught only by the next disposition pass.
