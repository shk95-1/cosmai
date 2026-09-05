-- 022: the panel population and the quarterly grain (fork issue #3). Additive only
-- (tests/test_ddl_additive_only.py).
--
-- Every ydc ratio uses the 43-channel seed panel as its denominator. cosmai had no such concept,
-- so the same code could emit numbers of a different meaning without any error. This file makes
-- room for that population and for the quarterly grain that stands on it -- the values (43
-- channels) are filled by the seed (#31).
--
-- The number is 022 because this file belongs to the long-lived branch feat/ydc-import (020~ is
-- the fork block, contracts/versioning.md). Everything up to 021 is already in the production
-- ledger needs.schema_migration, so changing a number or editing an earlier file makes it try to
-- apply again.

-- ---------- population ----------
-- The table one roster version lives in. Only with it can "which roster is this ratio against" be
-- forced by an FK -- panel_channel's PK is (version, channel_id), so a column naming version alone
-- had no parent to attach to.
CREATE TABLE needs.panel_roster (
  version    int  NOT NULL,
  note       text,                          -- what this version is (seed:channels_v1 ...)
  seeded_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (version)
);

-- Why the role lives in the needs derivation: the source (tubedepth) channel table is an upstream
-- contract and the place tool/checks/ddl-drift guards, so it is not a place for the fork to add a
-- column. A channel not in the roster is outside the panel and so does not enter the denominator --
-- that is what this table means (contracts/formats.md §Panel roster CSV).
CREATE TABLE needs.panel_channel (
  channel_id    text NOT NULL,
  version       int  NOT NULL REFERENCES needs.panel_roster,  -- roster version; the shape of a dictionary (version + active)
  panel_role    text NOT NULL CHECK (panel_role IN ('product','expert')),
  handle        text,
  channel_title text,
  role_basis    text,                      -- why the role was set that way (team_message | name_rule_verified ...)
  source_list   text,
  active        boolean NOT NULL DEFAULT true,
  seeded_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (version, channel_id)
);
-- The query that counts a denominator is always "this role in the active roster".
CREATE INDEX ON needs.panel_channel (version, panel_role) WHERE active;

-- ---------- quarterly grain ----------
-- Not a replacement for the month (metrics_need · metrics_wish) but an addition. Adding a column
-- that points at the grain to an existing table changes what its existing rows mean, so the tables
-- were split, and which table is canonical for which grain is said by the contract
-- (contracts/formats.md §Time's "the canonical table per aggregate grain"). Not being a view is
-- meaning too: a quarterly value is the value settled at that moment, so recomputing it on every
-- query parts "the value settled then" from "the value recounted now".
CREATE TABLE needs.metrics_topic_quarter (
  run_id        bigint NOT NULL REFERENCES needs.analysis_run,
  scope         text NOT NULL,             -- a category name, or 'all' (the metrics_need.scope vocabulary)
  -- The registry of the topic axis is aspect_lexicon(ruleset='retrieval-topic').aspect, not needs.need_key.
  -- 두 축은 `백탁` 하나만 겹친다(tests/test_panel_quarter_contract.py) -- USING (need_key) 로 조인하면
  -- 주제 하나를 돌려주고 나머지를 조용히 떨어뜨리므로, 이름이 그 축을 말한다.
  topic_key     text NOT NULL,
  quarter       text NOT NULL CHECK (quarter ~ '^[0-9]{4}Q[1-4]$'),  -- the grammar is the grain
  -- source and content_type are closed vocabularies inside the key. One typo (youtube_videos)
  -- silently opens a separate key group, and that group carries its own denominator -- the very
  -- thing formats.md says is impossible.
  source        text NOT NULL CHECK (source IN ('youtube_video','youtube_comment')),
  content_type  text NOT NULL CHECK (content_type IN ('long_form','short_form')),
  -- Which population the ratio is against stays on the row. Being in the key is meaning too: with
  -- the population outside the key, two populations fight over one slot and the later overwrites
  -- the earlier.
  panel_version int  NOT NULL REFERENCES needs.panel_roster,
  panel_role    text NOT NULL CHECK (panel_role IN ('product','expert')),
  mentions         int NOT NULL,           -- numerator: documents this topic matched
  documents        int NOT NULL,           -- documents of that population in that quarter (the quarterly document population, §Formulas)
  quarter_mentions int NOT NULL,           -- composition denominator: that quarter's trend_use topic mentions summed
  denom_channels   int NOT NULL,           -- panel channels that entered the computation that quarter
  -- The decimal places are the resolution of the verdict gate (ydc judge.py's TAU·DIFFUSION_TAU were
  -- fitted on rounded values). A bare numeric does not keep the decimal places in storage, and then one
  -- run holds two sets of values (interfaces.md §Formulas).
  composition       numeric(9,5),          -- mentions / quarter_mentions
  velocity_yoy      numeric(10,4),         -- ln(composition[q]) - ln(composition[same quarter last year])
  persistence       numeric(4,3),
  persist_quarters  int,
  window_quarters   int,
  unique_ratio      numeric(5,4),
  channel_count     int,
  channel_diffusion numeric(4,3),
  sample_ok     boolean NOT NULL,
  -- The sample gate is the same number as velocity_yoy's condition. A name with no definition
  -- makes a row say something other than its own name.
  CHECK (sample_ok = (mentions >= 5)),
  -- This topic is one term of that sum, so the numerator cannot exceed the denominator. If it
  -- does, the row used a different denominator.
  CHECK (mentions <= quarter_mentions),
  PRIMARY KEY (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role)
);
-- A time-series query reads one topic in quarter order. The primary key starts at run_id and
-- cannot give that path.
CREATE INDEX ON needs.metrics_topic_quarter (topic_key, quarter);

GRANT SELECT, INSERT, UPDATE, DELETE
  ON needs.panel_roster, needs.panel_channel, needs.metrics_topic_quarter TO needs_runtime;
