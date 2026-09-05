-- 023: the 2026-08-19 YouTube corpus snapshot (fork issue #4). Additive only
-- (tests/test_ddl_additive_only.py).
--
-- The 261,317 documents ydc handed over cannot be remade by re-collecting: comments keep piling up
-- and view and like counts are values as of collected_at. So these rows are not "YouTube now" but
-- **an observation of 2026-08-19**, and that fact has to be readable off the row. That a
-- re-collection (#38) landing on the same unique key does not overwrite these rows is the one
-- invariant this file carries.
--
-- The number 023 is in the long-lived branch feat/ydc-import's block (020~, contracts/versioning.md).
-- Everything up to 022 is in the production ledger needs.schema_migration, so changing a number or
-- editing an earlier file makes it try to apply again.

-- ---------- snapshot version ----------
-- One row per observation version. The shape of panel_roster (a version table plus a content table
-- pointing at it by FK): only with the version standing as one parent row can "which observation
-- do these rows belong to" be forced by an FK.
CREATE TABLE needs.corpus_snapshot (
  snapshot_id  int  NOT NULL,
  label        text NOT NULL UNIQUE,        -- yt-handoff-20260819
  produced_by  text,                        -- what made this version (to_common_schema.py)
  -- Which collection runs the version came from. The manifest's source_runs as they stand.
  source_runs  text[] NOT NULL CHECK (cardinality(source_runs) > 0),
  collected_at timestamptz NOT NULL,        -- the earliest collection time among those runs = this snapshot's instant
  note         text,
  -- The version the analysis reads by default. Since this table means the snapshot and the
  -- re-collection **both stay alive** (a re-collection does not replace a snapshot), 'current' is
  -- said by this one column rather than by deleting.
  active       boolean NOT NULL DEFAULT false,
  imported_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_id)
);
-- There is always exactly one active version. panel_channel could not use this as an index (43 rows
-- per version, so "the active rows have one distinct version" is not a unique key); this table has
-- one row per version and can.
CREATE UNIQUE INDEX corpus_snapshot_one_active ON needs.corpus_snapshot (active) WHERE active;

-- ---------- documents ----------
-- The unique key being (source, source_item_id) is rule 1 of the manifest. **snapshot_id standing
-- in front of it is why a snapshot is not overwritten**: a re-collection arrives under a different
-- snapshot_id, so a new observation of the same video becomes a different row from the old one. The
-- key makes that impossible, not a flag or loader discipline.
CREATE TABLE needs.corpus_document (
  snapshot_id    int  NOT NULL REFERENCES needs.corpus_snapshot,
  source         text NOT NULL CHECK (source IN ('youtube_video','youtube_comment')),
  source_item_id text NOT NULL,
  -- Rule 1's second sentence ("doc_id is the two joined by a colon") is a generated column rather
  -- than prose -- mention joins on this value, so having the loader build it would open a place
  -- where two versions of doc_id could drift apart.
  doc_id         text NOT NULL GENERATED ALWAYS AS (source || ':' || source_item_id) STORED,
  content_type   text NOT NULL
                 CHECK (content_type IN ('video_long','video_short','video_unknown','comment')),
  parent_item_id text,                      -- the video the comment hangs on; rule 3's quarter attribution rides this column
  channel_id     text NOT NULL,             -- a comment carries its parent video's channel too (where the panel join happens)
  published_at   timestamptz NOT NULL,
  url            text,
  -- A normalised surface form. The rule is the manifest's text_rule (contracts/formats.md §Corpus snapshot).
  -- Empty strings exist (quality_flags = 'empty_text'): not deleting the row is rule 8.
  text            text NOT NULL,
  quality_flags   text NOT NULL DEFAULT '',
  source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Raises a value that sat inside source_metadata to a column. To read the limitation that view and
  -- like counts are "the value as of this moment" (interfaces.md §Limitations of the population), that
  -- moment has to be a column of the row rather than a key inside the JSON.
  collected_at   timestamptz NOT NULL,
  source_run     text NOT NULL,             -- one element of corpus_snapshot.source_runs
  PRIMARY KEY (snapshot_id, source, source_item_id),
  -- Where mention points by FK. doc_id is a generated column, so this UNIQUE adds no second unique key.
  UNIQUE (snapshot_id, doc_id)
);
-- The path back from a comment to its parent video (rule 3). Video rows have no parent_item_id, so
-- this is a partial index.
CREATE INDEX ON needs.corpus_document (snapshot_id, parent_item_id) WHERE content_type = 'comment';
-- The path that counts the panel x long-form denominator (rules 4 and 5).
CREATE INDEX ON needs.corpus_document (snapshot_id, content_type, channel_id);

-- ---------- mentions ----------
-- One (document, topic) pair is one row. All 15 topics come in and the 13 used for judgement are
-- filtered by trend_use (rule 7).
CREATE TABLE needs.corpus_mention (
  snapshot_id  int  NOT NULL,
  doc_id       text NOT NULL,
  topic_id     text NOT NULL,
  topic_type   text NOT NULL,               -- product_category | attribute | spec | event | genre
  trend_use    boolean NOT NULL,
  matched_term text,
  span_start   int,
  PRIMARY KEY (snapshot_id, doc_id, topic_id),
  -- The DB carries "there are no orphan mentions", not a loader check. With snapshot_id in the key,
  -- a mention of one version attaching to another version's document is blocked too.
  FOREIGN KEY (snapshot_id, doc_id) REFERENCES needs.corpus_document (snapshot_id, doc_id)
);
-- The path that picks documents by topic (the sunscreen population filter, rule 6).
CREATE INDEX ON needs.corpus_mention (snapshot_id, topic_id);

GRANT SELECT, INSERT, UPDATE, DELETE
  ON needs.corpus_snapshot, needs.corpus_document, needs.corpus_mention TO needs_runtime;
