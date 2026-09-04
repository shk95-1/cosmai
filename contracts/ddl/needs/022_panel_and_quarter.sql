-- 022: 패널 모집단과 분기 입자 (포크 이슈 #3). 추가만 (tests/test_ddl_additive_only.py).
--
-- ydc 의 모든 비율은 시드 채널 43개 패널을 분모로 쓴다. cosmai 에는 그 개념이 없어서, 같은 코드가
-- 다른 뜻의 숫자를 오류 없이 낼 수 있는 자리였다. 이 파일은 그 모집단과, 그 위에 서는 분기 입자의
-- 자리를 만든다 -- 값(43채널)은 시드가 채운다(#31).
--
-- 번호가 022 인 것은 이 파일이 장수 브랜치 feat/ydc-import 의 것이기 때문이다(020~ 이 포크 블록,
-- contracts/versioning.md). 021 까지는 운영 원장 needs.schema_migration 에 이미 있으므로 번호를
-- 바꾸거나 앞 파일을 고치면 재적용을 시도한다.

-- ---------- 모집단 ----------
-- 명부 판본 한 줄이 사는 표. 이것이 있어야 "이 비율이 어느 명부에 대한 것인가"를 FK 로 강제할 수 있다 --
-- panel_channel 의 PK 는 (version, channel_id) 라, version 만 가리키는 열은 붙을 부모가 없었다.
CREATE TABLE needs.panel_roster (
  version    int  NOT NULL,
  note       text,                          -- 이 판본이 무엇인지 (seed:channels_v1 …)
  seeded_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (version)
);

-- 역할이 needs 파생에 사는 이유: 원천(tubedepth)의 채널 표는 upstream 계약이고 tool/checks/ddl-drift
-- 가 지키는 자리라, 포크가 컬럼을 더할 자리가 아니다. 명부에 없는 채널은 패널 밖이고, 그래서 분모에
-- 들어가지 않는다 -- 이것이 이 표의 뜻이다 (contracts/formats.md §패널 명부 CSV).
CREATE TABLE needs.panel_channel (
  channel_id    text NOT NULL,
  version       int  NOT NULL REFERENCES needs.panel_roster,  -- 명부 판본. 사전과 같은 모양이다 (version + active)
  panel_role    text NOT NULL CHECK (panel_role IN ('product','expert')),
  handle        text,
  channel_title text,
  role_basis    text,                      -- 역할을 그렇게 정한 근거 (team_message | name_rule_verified …)
  source_list   text,
  active        boolean NOT NULL DEFAULT true,
  seeded_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (version, channel_id)
);
-- 분모를 세는 조회는 언제나 "활성 명부의 이 역할"이다.
CREATE INDEX ON needs.panel_channel (version, panel_role) WHERE active;

-- ---------- 분기 입자 ----------
-- 월(metrics_need · metrics_wish)의 대체가 아니라 추가다. 기존 표에 입자를 가리키는 열을 더하면
-- 이미 있는 행의 뜻이 바뀌므로 표를 나눴고, 어느 표가 어느 입자의 정본인지는 계약이 말한다
-- (contracts/formats.md §시간 의 "집계 그레인의 정본"). 뷰가 아닌 것도 뜻이다: 분기 값은 그 시점의
-- 확정값이라, 매 조회마다 다시 계산하면 "그때 확정한 값"과 "지금 다시 센 값"이 갈린다.
CREATE TABLE needs.metrics_topic_quarter (
  run_id        bigint NOT NULL REFERENCES needs.analysis_run,
  scope         text NOT NULL,             -- 카테고리명 또는 'all' (metrics_need.scope 와 같은 어휘)
  -- 주제 축의 레지스트리는 aspect_lexicon(ruleset='retrieval-topic').aspect 이고 needs.need_key 가 아니다.
  -- 두 축은 `백탁` 하나만 겹친다(tests/test_panel_quarter_contract.py) -- USING (need_key) 로 조인하면
  -- 주제 하나를 돌려주고 나머지를 조용히 떨어뜨리므로, 이름이 그 축을 말한다.
  topic_key     text NOT NULL,
  quarter       text NOT NULL CHECK (quarter ~ '^[0-9]{4}Q[1-4]$'),  -- 문법이 곧 입자다
  -- source·content_type 은 키 안의 닫힌 어휘다. 오타 하나(youtube_videos)가 조용히 별도 키 그룹을 열고,
  -- 그 그룹은 자기만의 분모를 갖는다 -- formats.md 가 불가능하다고 말하는 그 일이다.
  source        text NOT NULL CHECK (source IN ('youtube_video','youtube_comment')),
  content_type  text NOT NULL CHECK (content_type IN ('long_form','short_form')),
  -- 어떤 모집단에 대한 비율인지가 행에 남는다. 키에 있는 것도 뜻이다: 모집단이 키 밖에 있으면
  -- 같은 자리를 두 모집단이 다투고 나중 것이 앞선 것을 덮는다.
  panel_version int  NOT NULL REFERENCES needs.panel_roster,
  panel_role    text NOT NULL CHECK (panel_role IN ('product','expert')),
  mentions         int NOT NULL,           -- 분자: 이 주제가 걸린 문서 수
  documents        int NOT NULL,           -- 그 분기 그 모집단의 문서 수 (§수식 의 "분기 문서 모집단")
  quarter_mentions int NOT NULL,           -- 구성비의 분모: 그 분기 trend_use 주제들의 언급 합
  denom_channels   int NOT NULL,           -- 그 분기에 산출에 든 패널 채널 수
  -- 자리수가 곧 판정 게이트의 해상도다(ydc judge.py 의 TAU·DIFFUSION_TAU 는 반올림된 값 위에서 맞춰졌다).
  -- 맨 numeric 이면 저장이 자리수를 지키지 않아, 같은 run 이 두 벌의 값을 갖는다 (interfaces.md §수식).
  composition       numeric(9,5),          -- mentions / quarter_mentions
  velocity_yoy      numeric(10,4),         -- ln(composition[q]) - ln(composition[전년 동분기])
  persistence       numeric(4,3),
  persist_quarters  int,
  window_quarters   int,
  unique_ratio      numeric(5,4),
  channel_count     int,
  channel_diffusion numeric(4,3),
  sample_ok     boolean NOT NULL,
  -- 표본 게이트는 velocity_yoy 의 조건과 같은 수다. 이름만 있고 정의가 없으면 행이 자기 이름과 다른
  -- 것을 말한다.
  CHECK (sample_ok = (mentions >= 5)),
  -- 이 주제도 그 합의 한 항이므로 분자가 분모를 넘을 수 없다. 넘으면 다른 분모를 쓴 행이다.
  CHECK (mentions <= quarter_mentions),
  PRIMARY KEY (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role)
);
-- 시계열 조회는 주제 하나를 분기 순으로 읽는다. 기본키는 run_id 로 시작해서 그 길을 못 준다.
CREATE INDEX ON needs.metrics_topic_quarter (topic_key, quarter);

GRANT SELECT, INSERT, UPDATE, DELETE
  ON needs.panel_roster, needs.panel_channel, needs.metrics_topic_quarter TO needs_runtime;
