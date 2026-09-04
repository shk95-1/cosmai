-- 023: 2026-08-19 유튜브 코퍼스 스냅샷 (포크 이슈 #4). 추가만 (tests/test_ddl_additive_only.py).
--
-- ydc 가 넘긴 261,317 문서는 재수집으로 다시 만들 수 없다: 댓글은 계속 쌓이고 조회수·좋아요는
-- collected_at 시점의 값이다. 그래서 이 행들은 "지금의 유튜브"가 아니라 **2026-08-19 의 관측**이고,
-- 그 사실이 행에서 읽혀야 한다. 재수집(#38)이 같은 유일키로 얹혀도 이 행들을 덮지 않는 것이
-- 이 파일이 지는 유일한 불변식이다.
--
-- 번호 023 은 장수 브랜치 feat/ydc-import 의 블록(020~, contracts/versioning.md)이다. 022 까지
-- 운영 원장 needs.schema_migration 에 있으므로 번호를 바꾸거나 앞 파일을 고치면 재적용을 시도한다.

-- ---------- 스냅샷 판본 ----------
-- 관측 판본 한 줄. panel_roster 와 같은 모양이다(판본 표 + 그것을 FK 로 가리키는 내용 표): 판본이
-- 부모 한 줄로 서 있어야 "이 행들이 어느 관측의 것인가"를 FK 로 강제할 수 있다.
CREATE TABLE needs.corpus_snapshot (
  snapshot_id  int  NOT NULL,
  label        text NOT NULL UNIQUE,        -- yt-handoff-20260819
  produced_by  text,                        -- 이 판본을 만든 것 (to_common_schema.py)
  -- 어느 수집 런에서 온 판본인지. 매니페스트의 source_runs 그대로다.
  source_runs  text[] NOT NULL CHECK (cardinality(source_runs) > 0),
  collected_at timestamptz NOT NULL,        -- 그 런들의 가장 이른 수집 시각 = 이 스냅샷의 시점
  note         text,
  -- 분석이 기본으로 읽는 판본. 스냅샷과 재수집분이 **함께 살아 있는 것**이 이 표의 뜻이므로
  -- (재수집은 스냅샷을 대체하지 않는다) '현행'은 지움이 아니라 이 한 칸으로 말한다.
  active       boolean NOT NULL DEFAULT false,
  imported_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_id)
);
-- 활성 판본은 언제나 하나. panel_channel 은 이것을 인덱스로 쓸 수 없었지만(판본당 43행이라 "활성
-- 행의 distinct version 이 하나"는 유니크 키가 아니다) 이 표는 판본당 한 행이라 쓸 수 있다.
CREATE UNIQUE INDEX corpus_snapshot_one_active ON needs.corpus_snapshot (active) WHERE active;

-- ---------- 문서 ----------
-- 유일키가 (source, source_item_id) 인 것은 매니페스트의 규칙 1 이다. **그 앞에 snapshot_id 가 서
-- 있는 것이 스냅샷이 덮이지 않는 이유다**: 재수집분은 다른 snapshot_id 로 들어오므로 같은 영상의 새
-- 관측이 옛 관측과 다른 행이 된다. 플래그나 적재기 규율이 아니라 키가 그것을 불가능하게 만든다.
CREATE TABLE needs.corpus_document (
  snapshot_id    int  NOT NULL REFERENCES needs.corpus_snapshot,
  source         text NOT NULL CHECK (source IN ('youtube_video','youtube_comment')),
  source_item_id text NOT NULL,
  -- 규칙 1 의 뒷문장("doc_id 는 그 둘을 콜론으로 이은 값")을 산문이 아니라 생성 열로 둔다 --
  -- mention 이 이 값으로 조인하므로, 적재기가 만들면 두 벌의 doc_id 가 갈릴 수 있는 자리가 생긴다.
  doc_id         text NOT NULL GENERATED ALWAYS AS (source || ':' || source_item_id) STORED,
  content_type   text NOT NULL
                 CHECK (content_type IN ('video_long','video_short','video_unknown','comment')),
  parent_item_id text,                      -- 댓글이 달린 영상. 규칙 3 의 분기 귀속이 이 칸을 탄다
  channel_id     text NOT NULL,             -- 댓글도 부모 영상의 채널을 싣는다 (패널 조인의 자리)
  published_at   timestamptz NOT NULL,
  url            text,
  -- 정규화된 표면형이다. 규칙은 매니페스트의 text_rule (contracts/formats.md §코퍼스 스냅샷).
  -- 빈 문자열이 있다(quality_flags = 'empty_text'): 행을 지우지 않는 것이 규칙 8 이다.
  text            text NOT NULL,
  quality_flags   text NOT NULL DEFAULT '',
  source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- source_metadata 안에 있던 값을 열로 올린다. 조회수·좋아요가 "이 시점의 값"이라는 한계를 읽으려면
  -- (interfaces.md §모집단의 한계) 시점이 JSON 안이 아니라 행의 칸이어야 한다.
  collected_at   timestamptz NOT NULL,
  source_run     text NOT NULL,             -- corpus_snapshot.source_runs 의 한 원소
  PRIMARY KEY (snapshot_id, source, source_item_id),
  -- mention 이 FK 로 가리킬 자리. 생성 열이라 이 유니크는 유일키를 하나 더 만들지 않는다.
  UNIQUE (snapshot_id, doc_id)
);
-- 댓글을 부모 영상으로 되찾는 길 (규칙 3). 영상 행은 parent_item_id 가 없으므로 부분 인덱스다.
CREATE INDEX ON needs.corpus_document (snapshot_id, parent_item_id) WHERE content_type = 'comment';
-- 패널 × 장문 분모를 세는 길 (규칙 4·5).
CREATE INDEX ON needs.corpus_document (snapshot_id, content_type, channel_id);

-- ---------- 언급 ----------
-- (문서, 주제) 하나가 한 행이다. 15개 주제 전부가 들어오고 판정용 13개는 trend_use 로 거른다(규칙 7).
CREATE TABLE needs.corpus_mention (
  snapshot_id  int  NOT NULL,
  doc_id       text NOT NULL,
  topic_id     text NOT NULL,
  topic_type   text NOT NULL,               -- product_category | attribute | spec | event | genre
  trend_use    boolean NOT NULL,
  matched_term text,
  span_start   int,
  PRIMARY KEY (snapshot_id, doc_id, topic_id),
  -- 고아 언급이 없다는 것을 적재기 점검이 아니라 DB 가 진다. snapshot_id 가 키에 함께 있으므로
  -- 한 판본의 언급이 다른 판본의 문서에 붙는 일도 막힌다.
  FOREIGN KEY (snapshot_id, doc_id) REFERENCES needs.corpus_document (snapshot_id, doc_id)
);
-- 주제로 문서를 고르는 길 (선크림 모집단 필터, 규칙 6).
CREATE INDEX ON needs.corpus_mention (snapshot_id, topic_id);

GRANT SELECT, INSERT, UPDATE, DELETE
  ON needs.corpus_snapshot, needs.corpus_document, needs.corpus_mention TO needs_runtime;
