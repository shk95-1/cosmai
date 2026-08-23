-- naver 수집기 원천 (이슈 #9, 조정 판정 (a)). 추가만 -- 001은 손대지 않는다 (test_ddl_additive_only.py).
--
-- 실용적 선택: needs 는 원래 분석 산출 스키마이지 원천 저장소가 아니다. 그런데 collectors/commerce·
-- collectors/youtube 는 각자 trend_radar·tubedepth 스키마를 갖는 반면, naver 는 5단계에서 처음 생기는
-- 수집기라 자기 스키마가 없다(원본 cosmai-old의 잡 큐·source 행 모델은 이 레포가 계승하지 않기로 했다 --
-- REBUILD 전제, #18). 새 스키마를 하나 더 만드는 대신 needs 에 naver_ 접두로 얹는다. 두 번째 수집기가
-- 원천 저장을 필요로 하면(예: #10 라이브 컷오버가 다른 소스를 추가하면) 그때 분리를 다시 본다.
--
-- naver_blog_post 가 formats.md 의 ref 문법(`naver_blog` → `post_id`)이 가리키는 원천 테이블이고
-- (#17 T15가 지적한 공백), analysis.types.TextUnit(src='naver_blog') 은 이 테이블을 읽어 채워진다.

CREATE TABLE needs.naver_run (
  id                 uuid PRIMARY KEY,
  dataset            text NOT NULL CHECK (dataset IN ('datalab','blog')),
  captured_at        timestamptz NOT NULL,
  started_at         timestamptz NOT NULL,
  finished_at        timestamptz,
  status             text NOT NULL,          -- running | ok | partial | blocked | failed
  note               text,
  collector_version  text
);

CREATE TABLE needs.naver_fetch_log (
  id          bigserial PRIMARY KEY,
  run_id      uuid NOT NULL REFERENCES needs.naver_run(id) ON DELETE CASCADE,
  at          timestamptz NOT NULL,
  dataset     text NOT NULL,
  query       text NOT NULL,                 -- 요청한 것: datalab 그룹명 또는 blog 검색어
  status      int,
  attempt     int NOT NULL,
  elapsed_ms  int,
  bytes       int,
  error       text
);
CREATE INDEX ix_naver_fetch_log_run_at ON needs.naver_fetch_log (run_id, at);

CREATE TABLE needs.naver_datalab_point (
  category     text NOT NULL,                -- lexicon_category 어휘 (예: 선블록)
  group_key    text NOT NULL,                 -- keywords.json 의 그룹 이름 (예: 백탁)
  month        text NOT NULL,                 -- 'YYYY-MM'
  ratio        numeric,
  terms        jsonb NOT NULL,                -- 요청에 실제로 넣은 검색어 목록 (감사용)
  captured_at  timestamptz NOT NULL,
  PRIMARY KEY (category, group_key, month)
);

CREATE TABLE needs.naver_blog_post (
  post_id                 text PRIMARY KEY,   -- ref = post_id (formats.md). 원본의 안정적인 link.
  url                     text NOT NULL,
  category                text,
  group_key               text,
  query                   text,               -- 이 글을 찾은 검색어
  title                   text NOT NULL,
  excerpt                 text NOT NULL,
  author                  text,
  published_at            date,               -- naver 가 postdate 를 못 주면 NULL
  observed_at_resolution  text NOT NULL CHECK (observed_at_resolution IN ('day','month','year')),
  captured_at             timestamptz NOT NULL
);
