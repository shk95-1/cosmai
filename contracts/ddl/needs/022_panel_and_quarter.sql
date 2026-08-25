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
-- 역할이 needs 파생에 사는 이유: 원천(tubedepth)의 채널 표는 upstream 계약이고 tool/checks/ddl-drift
-- 가 지키는 자리라, 포크가 컬럼을 더할 자리가 아니다. 명부에 없는 채널은 패널 밖이고, 그래서 분모에
-- 들어가지 않는다 -- 이것이 이 표의 뜻이다 (contracts/formats.md §패널 명부 CSV).
CREATE TABLE needs.panel_channel (
  channel_id    text NOT NULL,
  version       int  NOT NULL,             -- 명부 판본. 사전과 같은 모양이다 (version + active)
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
  need_key      text NOT NULL,             -- 주제 id. 주제 사전이 aspect_lexicon(retrieval-topic) 이다
  quarter       text NOT NULL CHECK (quarter ~ '^[0-9]{4}Q[1-4]$'),  -- 문법이 곧 입자다
  source        text NOT NULL,             -- youtube_video | youtube_comment -- 합치지 않고 나란히
  content_type  text NOT NULL,             -- long_form | short_form -- 분모는 장문만 (§수식)
  -- 어떤 모집단에 대한 비율인지가 행에 남는다. 키에 있는 것도 뜻이다: 모집단이 키 밖에 있으면
  -- 같은 자리를 두 모집단이 다투고 나중 것이 앞선 것을 덮는다.
  panel_version int  NOT NULL,             -- needs.panel_channel.version
  panel_role    text NOT NULL CHECK (panel_role IN ('product','expert')),
  mentions         int NOT NULL,           -- 분자: 이 주제가 걸린 문서 수
  documents        int NOT NULL,           -- 그 분기 그 모집단의 문서 수
  quarter_mentions int NOT NULL,           -- 구성비의 분모: 그 분기 전 주제의 언급 합
  denom_channels   int NOT NULL,           -- 그 분기에 산출에 든 패널 채널 수
  composition       numeric,               -- mentions / quarter_mentions
  velocity_yoy      numeric,               -- ln(composition[q]) - ln(composition[전년 동분기])
  persistence       numeric,
  persist_quarters  int,
  window_quarters   int,
  unique_ratio      numeric,
  channel_count     int,
  channel_diffusion numeric,
  sample_ok     boolean NOT NULL,
  PRIMARY KEY (run_id, scope, need_key, quarter, source, content_type, panel_version, panel_role)
);
-- 시계열 조회는 주제 하나를 분기 순으로 읽는다. 기본키는 run_id 로 시작해서 그 길을 못 준다.
CREATE INDEX ON needs.metrics_topic_quarter (need_key, quarter);

GRANT SELECT, INSERT, UPDATE, DELETE ON needs.panel_channel, needs.metrics_topic_quarter TO needs_runtime;
