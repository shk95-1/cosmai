-- 030: which lineage a corpus snapshot belongs to, what instrument made it, and which run the
-- archive is read through (fork issue #94, from decision #93 D0 and D5). Additive only
-- (tests/test_ddl_additive_only.py). 029 is taken by another issue in flight.
--
-- #93 D0 splits the population in two. The 2026-08-19 handover (snapshot 1) is an **archive**: its
-- rows are read and never recomputed. A **live** lineage grows beside it in the same tables out of
-- the collector's side. Nothing on a row says today which of the two a snapshot is -- 023's `active`
-- says only "not the current one", and once live snapshots exist that is just as true of last
-- month's live snapshot.
--
-- Why the default is 'archive' and not 'live'. Two reasons, and the second is the louder one.
-- (1) The one row that exists becomes the archive without a production row write: this file may not
-- write rows, and defaulting to 'archive' means snapshot 1 is the archive the moment the column
-- exists. (2) A later snapshot inserted without naming its lineage then fails loudly on the partial
-- unique index below, instead of quietly standing as a second archive -- or, worse, being picked up
-- by the archive views, which would leave the fixed archive reading a live run.
--
-- `instrument` records how the observation was made, which is what makes two snapshots comparable
-- at all (#38, fork #91 decision 2). The keys goal shk95-1/cosmai#255 names are listing_route,
-- comment_depth, refetch_window_days and dictionary_version. There is no CHECK on the key set: the
-- instrument grows with the collector, and a vocabulary frozen in DDL would cost a migration per knob.
ALTER TABLE needs.corpus_snapshot
  ADD COLUMN lineage text NOT NULL DEFAULT 'archive' CHECK (lineage IN ('archive', 'live'));
ALTER TABLE needs.corpus_snapshot
  ADD COLUMN instrument jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Exactly one archive, by the same mechanism 023 gives `active`: this table has one row per version,
-- so "at most one archive" is a key and a partial unique index can carry it. panel_channel could not
-- do this (43 rows per version) and needed 027's constraint trigger instead.
CREATE UNIQUE INDEX corpus_snapshot_one_archive
  ON needs.corpus_snapshot (lineage) WHERE lineage = 'archive';

-- ---------- the archive's run ----------
-- The archive is read through the run that produced it, and that run is **resolved**, never pinned:
-- the snapshot marked 'archive' names the snapshot id, and analysis/trend/pipeline.py's note_of()
-- writes that id into the run's note -- 'trend-quarter:<metric>:<scope>:snapshot<id>:panel<v>'. So
-- no row of needs.analysis_run is written to make this work, which is the point: #94's "must hold"
-- is that snapshot 1's rows and its run's output are not touched.
--
-- What is skipped between 'trend-quarter:' and ':snapshot<id>' is **two** segments, not one: the
-- metric version and the scope both sit there (production's only such row today is
-- 'trend-quarter:v0.2:<scope>:snapshot1:panel1'). Reading it as the version alone is the easy
-- mistake, and it is the one that would make this view answer with nothing.
--
-- The grammar is read with starts_with()/strpos() rather than LIKE. Two reasons: the wildcard
-- character is a placeholder to the driver that applies these files in the test fixture (the same
-- reason db/views/pipeline_health.sql gives), and the match wanted here is an exact one on a
-- delimited segment, which is what strpos of ':snapshot<id>:panel' says and a LIKE pattern only
-- approximates -- 'snapshot1' would otherwise also be a prefix of 'snapshot10'.
--
-- Ordering by started_at and then run_id: a re-run of the archive's analysis is a new row, and the
-- surface has to follow the newest **successful** one. A failed or still-running row never takes it.
--
-- Why this view sits in a migration while the three views that read it sit in db/views/:
-- db/migrate.sh stage (f) recreates db/views/*.sql in **file order**, and every file's stem has to
-- be the view's own name (tests/test_migrate_view_sweep.py). 'archive_metrics_topic_quarter' sorts
-- before 'archive_run', so a helper living there would not exist yet when its first reader is
-- created and every deploy would stop. Created once here, it is a dependency of those three rather
-- than a peer, and the sweep's DROP ... CASCADE of a dependent never reaches it.
CREATE VIEW needs.archive_run AS
SELECT r.run_id, s.snapshot_id
  FROM needs.corpus_snapshot s
  JOIN needs.analysis_run r
    ON starts_with(r.note, 'trend-quarter:')
   AND strpos(r.note, ':snapshot' || s.snapshot_id || ':panel') > 0
 WHERE s.lineage = 'archive'
   AND r.status = 'ok'
 ORDER BY r.started_at DESC, r.run_id DESC
 LIMIT 1;

GRANT SELECT ON needs.archive_run TO needs_runtime;
