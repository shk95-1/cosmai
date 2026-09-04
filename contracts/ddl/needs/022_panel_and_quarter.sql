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

-- 역할이 needs 파생에 사는 이유: 원천(tubedepth)의 채널 표는 upstream 계약이고 tool/checks/ddl-drift
-- 가 지키는 자리라, 포크가 컬럼을 더할 자리가 아니다. 명부에 없는 채널은 패널 밖이고, 그래서 분모에
-- 들어가지 않는다 -- 이것이 이 표의 뜻이다 (contracts/formats.md §패널 명부 CSV).
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
-- 월(metrics_need · metrics_wish)의 대체가 아니라 추가다. 기존 표에 입자를 가리키는 열을 더하면
-- 이미 있는 행의 뜻이 바뀌므로 표를 나눴고, 어느 표가 어느 입자의 정본인지는 계약이 말한다
-- (contracts/formats.md §시간 의 "집계 그레인의 정본"). 뷰가 아닌 것도 뜻이다: 분기 값은 그 시점의
-- 확정값이라, 매 조회마다 다시 계산하면 "그때 확정한 값"과 "지금 다시 센 값"이 갈린다.
CREATE TABLE needs.metrics_topic_quarter (
  run_id        bigint NOT NULL REFERENCES needs.analysis_run,
  scope         text NOT NULL,             -- a category name, or 'all' (the metrics_need.scope vocabulary)
  -- 주제 축의 레지스트리는 aspect_lexicon(ruleset='retrieval-topic').aspect 이고 needs.need_key 가 아니다.
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
  documents        int NOT NULL,           -- 그 분기 그 모집단의 문서 수 (§수식 의 "분기 문서 모집단")
  quarter_mentions int NOT NULL,           -- composition denominator: that quarter's trend_use topic mentions summed
  denom_channels   int NOT NULL,           -- panel channels that entered the computation that quarter
  -- 자리수가 곧 판정 게이트의 해상도다(ydc judge.py 의 TAU·DIFFUSION_TAU 는 반올림된 값 위에서 맞춰졌다).
  -- 맨 numeric 이면 저장이 자리수를 지키지 않아, 같은 run 이 두 벌의 값을 갖는다 (interfaces.md §수식).
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
