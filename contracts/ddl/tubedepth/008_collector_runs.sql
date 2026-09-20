-- Additive only (epic #16 pre-approval 2: DROP, type changes and other schema changes are excluded).
-- Part of this schema's canonical form since #178, the same composition 004 above describes.
--
-- #280: one row per `cosmai collect youtube` pass that is not a queue of jobs -- today `prune` and
-- `flatten`. Without it a prune that dies every night and a prune that succeeds every night read
-- the same: `db/views/collector_health.sql`'s youtube arm is built entirely from `tubedepth.jobs`
-- grouped by `dataset`, `prune` writes no job row at all, and `needs.pipeline_health` therefore
-- kept `youtube:prune` at `never` -- which that view's own header says the screen's banner does not
-- count. #279's defect (a pass unable to finish past ~1,200 candidates) was found by reading code.
--
-- WHY A TABLE AND NOT A `jobs` ROW. The #275 precedent put flatten's per-artifact failures in
-- `tubedepth.jobs`, and a run row was held against that shape first. Two things it cannot carry:
--
--   `partial`   `jobs.state` is queued|running|succeeded|failed|cancelled, a work item's states,
--               and a capped or partly failed pass is none of them. collector_health's youtube arm
--               reaches `partial` only where one bucket holds both a success and a failure, which
--               one row per pass can never do. Widening `state` would change the job state machine
--               for every reader of the queue to describe something that is not a job.
--   the denominator  N1 of #275's review: only failures reach `jobs`, so one flatten failure in
--               five hundred reads as 0 percent ok. The count of what a pass *examined* has no
--               honest column on `jobs` -- `attempt_count`, `payload_bytes` and `webhook_attempts`
--               all mean something else, and borrowing one is how a number becomes a lie later.
--
-- `tubedepth.source_health`, `lane_health`, `worker_control` and `flatten_progress` were read
-- first, as the issue asked. None of them is a run log: the first three belong to the archived
-- always-on API server and rate-lane daemon and have no writer in this repo at all
-- (collectors/youtube/storage/tables.py declares none of them), and `flatten_progress` holds one
-- cursor row, not history.
--
-- The shape is `needs.naver_run`'s (contracts/ddl/needs/004), which is the arm collector_health
-- already reads that way, minus its `captured_at`/`collector_version`: one identifier, the dataset,
-- the status vocabulary the operations view names, and the pass's own counts.
--
-- `status` carries a CHECK for the reason 004 gives on naver_run: the five words are the operations
-- view's own vocabulary (contracts/entrypoints.md §Common operations view), and a sixth would
-- reach the screen as a status nothing there knows how to colour.
--
-- The four counts are the pass's units of work, and what a unit is belongs to the dataset:
-- `prune` counts batches (#279 made the pass's cost a property of the number of batches, not of
-- the candidates in one), `flatten` counts artifacts examined. `attempted` is the denominator,
-- and `attempted = succeeded + failed + skipped` always, so the view can emit the first three and
-- leave `skipped` as the gap the operations view already documents (the spot a 404 sits in on the
-- commerce arm). NOT NULL with no DEFAULT, so every writer has to say the four numbers out loud: a
-- default would let a caller that forgot them record a pass that looks like it did nothing, which
-- is a thing a real pass can also be (one that died before it examined anything) and so cannot be
-- told apart afterwards.
--
-- `note` is the line the pass prints, so the row that says `partial` also says what was pruned or
-- how the pass stopped short. No payload and no SQL ever reaches it (collectors/youtube/cli.py).
CREATE TABLE tubedepth.collector_runs (
    identifier  varchar(32) PRIMARY KEY,
    dataset     varchar(16) NOT NULL,
    status      varchar(16) NOT NULL
                  CHECK (status IN ('running', 'ok', 'partial', 'blocked', 'failed')),
    started_at  timestamptz NOT NULL,
    finished_at timestamptz,
    attempted   integer NOT NULL,
    succeeded   integer NOT NULL,
    failed      integer NOT NULL,
    skipped     integer NOT NULL,
    note        text
);

-- What collector_health reads: every run of one dataset, newest first. The cron writes 97 rows a day
-- (prune once, flatten every fifteen minutes), so this index is for the view's ordering rather than
-- for volume.
--
-- Nothing ages these rows, and that is deliberate rather than an omission: `prune` deletes expired
-- artifacts and finished jobs, not run history. collector_health's own header rests on a run being
-- left behind forever -- "commerce leaves every past run behind as a row" is why the youtube job arm
-- had to be a fixed hour bucket rather than a window -- so a health log that aged itself out would
-- make a quiet month look like a collector that never ran. At 97 rows a day the table reaches about
-- 35,000 rows a year.
CREATE INDEX ix_collector_run_recent ON tubedepth.collector_runs (dataset, started_at);

-- The collector connects as `tubedepth_runtime`, and that role reaches a table of this schema
-- through the DEFAULT PRIVILEGES db/bootstrap_source.sql sets -- which runs only where the schema
-- was **absent**. Production's `tubedepth` has existed since before #178, so it takes the `built`
-- path on every deploy and may carry no such default at all. This is the first file in this
-- directory to create a table rather than add a column, so nothing has ever exercised that gap, and
-- a deploy is the wrong place to find out: the next prune pass would end in "permission denied for
-- table collector_runs" with the pass itself still correct.
--
-- Stated rather than guarded. A `DO` block is how one would ask whether the role exists, and a
-- source DDL file may not carry the word BEGIN -- the baseline and every file here travel inside
-- one transaction step (0) opens, and tests/test_empty_db_bootstrap.py holds that. There is nowhere
-- this file is applied that has no `tubedepth_runtime`: db/bootstrap_source.sql creates the role
-- before step (0) reaches these files on an empty database (tool/checks/ddl-drift's container), and
-- production and tool/checks/test's harness both carry it already.
GRANT SELECT, INSERT, UPDATE, DELETE ON tubedepth.collector_runs TO tubedepth_runtime;
