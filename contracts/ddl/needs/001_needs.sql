-- app.needs — 정규화·분석 산출물 스키마 (계약 v0.1, 2026-08-23)
-- 근거: architect/REBUILD.md §2 (슬라이스 7개 요구사항 매트릭스). 컬럼은 slice-*/ 산출 CSV의 합집합.
-- 규칙: 자연키 upsert, 모든 파생 행에 *_version, 시간 컬럼은 observed_at + observed_at_resolution.

-- 스키마·롤 생성은 db/bootstrap.sql 의 책임 (psql -v schema=needs). 이 파일은 owner 롤로 테이블만 만든다.

-- 롤 4종 (기존 패턴과 동일). 비밀번호는 init 스크립트가 env에서 주입.
-- needs_owner: DDL 소유 / needs_migrator: 마이그레이션 (SET ROLE needs_owner) / needs_runtime: DML / needs_reader: SELECT
-- PostgREST anon 에게는 metrics_* · *_lexicon · product_ref 만 SELECT (집계 완료본).

-- ---------- 식별 ----------
CREATE TABLE needs.product_ref (
  product_ref     text PRIMARY KEY,          -- 'oy:A000000184352' 처럼 primary 멤버 키
  brand           text,
  name_norm       text NOT NULL,             -- 정규화 이름 (괄호·용량·기획 제거)
  name            text NOT NULL,
  n_sites         int  NOT NULL DEFAULT 1,
  first_seen      date,
  linker_version  text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE needs.product_member (
  source          text NOT NULL,             -- oliveyoung | glowpick | daisomall | hwahae
  product_key     text NOT NULL,
  product_ref     text NOT NULL REFERENCES needs.product_ref,
  role            text NOT NULL CHECK (role IN ('primary','member')),
  match_score     numeric,
  PRIMARY KEY (source, product_key)
);
CREATE TABLE needs.product_variant (          -- 리필·용량·향·옵션 — 같은 ref 아래 변형
  source          text NOT NULL,
  product_key     text NOT NULL,
  variant_of      text NOT NULL REFERENCES needs.product_ref,
  variant_kind    text NOT NULL CHECK (variant_kind IN ('refill','size','scent','shade','option','set')),
  variant_label   text,
  PRIMARY KEY (source, product_key)
);

-- ---------- 사전 (버전 있는 테이블; 파일 아님) ----------
CREATE TABLE needs.entity_lexicon (
  id              bigserial PRIMARY KEY,
  kind            text NOT NULL CHECK (kind IN ('brand','format','attribute','ingredient','stopword','alias')),
  canonical       text NOT NULL,
  surface         text NOT NULL,             -- 실제 매칭 문자열 (한글 변형 포함)
  tier            text,                      -- brand: normal | cooc_required | stop ; 기타 null
  source          text,                      -- rank_snapshot.brand | category_name | paper_lexicon | manual
  version         int  NOT NULL,
  active          boolean NOT NULL DEFAULT true,
  note            text,
  UNIQUE (kind, surface, version)
);
CREATE TABLE needs.aspect_lexicon (
  id              bigserial PRIMARY KEY,
  aspect          text NOT NULL,             -- need_key
  scope           text NOT NULL CHECK (scope IN ('generic','category')),
  category        text NOT NULL DEFAULT '', -- scope=category 일 때; generic 은 ''
  pattern         text NOT NULL,             -- 정규식
  is_neutral_noun boolean NOT NULL DEFAULT false,
  version         int  NOT NULL,
  active          boolean NOT NULL DEFAULT true,
  UNIQUE (aspect, scope, category, pattern, version)
);
CREATE TABLE needs.site_axis_map (            -- 사이트 제공 토픽축 ↔ need_key (P1: 25 항목)
  site            text NOT NULL,
  category        text NOT NULL DEFAULT '',
  site_axis       text NOT NULL,
  need_key        text,                      -- null = 대응 없음
  note            text,
  PRIMARY KEY (site, category, site_axis)
);

-- ---------- 평가셋 (1급 자산) ----------
CREATE TABLE needs.labeled_set (
  task            text NOT NULL,             -- polarity | wish_class | brand_link | product_match | aspect
  ref             text NOT NULL,             -- 문장/댓글/쌍을 가리키는 안정 키
  split           text NOT NULL CHECK (split IN ('tune','holdout')),
  gold            text NOT NULL,
  text            text,
  labeler         text NOT NULL,
  labeled_at      date NOT NULL,
  extra           jsonb,
  PRIMARY KEY (task, ref)
);

-- ---------- 언급 (정규화 산출물) ----------
CREATE TABLE needs.need_mention (
  mention_id      bigserial PRIMARY KEY,
  src             text NOT NULL CHECK (src IN ('review','yt_comment','yt_transcript','naver_blog')),
  site            text NOT NULL,             -- oliveyoung | glowpick | daisomall | youtube | naver
  ref             text NOT NULL,             -- review: product_key/review_key ; comment: video_id/comment_id
  product_ref     text REFERENCES needs.product_ref,
  source_product_key text,
  category        text,
  need_key        text NOT NULL,
  aspect_scope    text,
  polarity        text NOT NULL CHECK (polarity IN ('불만','만족','중립')),
  strength        numeric,                   -- review: 1 - rating/5 ; comment: like_count
  rating          numeric,
  observed_at     date NOT NULL,
  observed_at_resolution text NOT NULL CHECK (observed_at_resolution IN ('day','month','year')),
  month           text NOT NULL,             -- 'YYYY-MM' (공통 집계 그레인)
  sentence        text NOT NULL,
  extractor_version text NOT NULL,
  polarity_version  text NOT NULL,           -- 'rule-v2.2' | 'llm-<model>-<date>'
  UNIQUE (src, ref, need_key, sentence)
);
CREATE TABLE needs.wish_mention (
  mention_id      bigserial PRIMARY KEY,
  src             text NOT NULL CHECK (src IN ('yt_comment','review')),
  ref             text NOT NULL,
  video_id        text,
  channel_id      text,
  channel_is_brand_owner boolean,
  product_ref     text REFERENCES needs.product_ref,
  observed_at     date NOT NULL,
  observed_at_resolution text NOT NULL,
  month           text NOT NULL,
  wish_class      text NOT NULL CHECK (wish_class IN ('a','b','c')),  -- a 제품/출시 요청, b 크리에이터 요청, c 일반 희망
  brand           text,
  format          text,
  attribute       text,
  marker          text,
  sentence        text NOT NULL,
  like_count      int,
  extractor_version text NOT NULL,
  UNIQUE (src, ref)
);
CREATE TABLE needs.brand_mention (
  src             text NOT NULL CHECK (src IN ('title','transcript','comment')),
  ref_id          text NOT NULL,
  video_id        text,
  brand           text NOT NULL,             -- canonical
  count           int  NOT NULL,
  cooc_count      int,
  observed_at     date,
  observed_at_resolution text,
  linker_version  text NOT NULL,
  PRIMARY KEY (src, ref_id, brand, linker_version)
);
CREATE TABLE needs.product_line_mention (
  src             text NOT NULL,
  ref_id          text NOT NULL,
  line_key        text NOT NULL,             -- 브랜드+라인 (SKU 아님)
  count           int  NOT NULL,
  linker_version  text NOT NULL,
  PRIMARY KEY (src, ref_id, line_key, linker_version)
);

-- ---------- 분모·랭킹 파생 ----------
CREATE TABLE needs.product_denominator (
  source          text NOT NULL,
  product_key     text NOT NULL,
  captured_at     date NOT NULL,
  site_review_count int,
  low_collected   int,
  low_complete    boolean,                   -- RATING_ASC 표본에 3★이 섞임 = 1·2★ 전수
  site_low_est    numeric,
  PRIMARY KEY (source, product_key, captured_at)
);
CREATE TABLE needs.rank_daily (
  source          text NOT NULL,
  board           text NOT NULL,
  category_key    text NOT NULL,
  product_key     text NOT NULL,
  day_kst         date NOT NULL,
  n_snapshots     int  NOT NULL,
  present_share   numeric,
  rank_mean       numeric,
  rank_min        int,
  rank_max        int,
  price_mode      int,
  PRIMARY KEY (source, board, category_key, product_key, day_kst)
);
CREATE TABLE needs.price_event (
  source          text NOT NULL,
  product_key     text NOT NULL,
  board           text NOT NULL,
  t_change        timestamptz NOT NULL,
  price_before    int, price_after int, pct numeric,
  direction       text CHECK (direction IN ('drop','rise')),
  rank_pre6 numeric, rank_post6 numeric, rank_post12 numeric, rank_post24 numeric,
  PRIMARY KEY (source, product_key, board, t_change)
);

-- ---------- 집계 (읽기 출구에 노출되는 것) ----------
CREATE TABLE needs.analysis_run (
  run_id          bigserial PRIMARY KEY,
  started_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz,
  status          text NOT NULL DEFAULT 'running',
  versions        jsonb NOT NULL,            -- {linker, extractor, polarity, aggregate, lexicon}
  note            text
);
CREATE TABLE needs.metrics_need (
  run_id          bigint NOT NULL REFERENCES needs.analysis_run,
  scope           text NOT NULL,             -- category 이름 또는 'all'
  need_key        text NOT NULL,
  month           text NOT NULL DEFAULT '', -- '' = 전체 기간
  product_ref     text NOT NULL DEFAULT '', -- '' = 카테고리 합
  neg int NOT NULL, pos int NOT NULL,
  unresolved      numeric,                   -- neg/(neg+pos)
  low_share       numeric,                   -- 저평점 표본 내 비율
  population_share_pct numeric,              -- × 사이트 저평점 비율
  strength_low_rating_ratio numeric,
  persist_months  int,
  persist_products int,
  PRIMARY KEY (run_id, scope, need_key, month, product_ref)
);
CREATE TABLE needs.metrics_wish (
  run_id bigint NOT NULL REFERENCES needs.analysis_run,
  scope text NOT NULL, format text NOT NULL DEFAULT '', attribute text NOT NULL DEFAULT '', brand text NOT NULL DEFAULT '',
  mentions int NOT NULL, channels int, months_present int, like_sum int, like_cap_sum numeric,
  PRIMARY KEY (run_id, scope, format, attribute, brand)
);
