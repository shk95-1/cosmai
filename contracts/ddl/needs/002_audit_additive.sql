-- app.needs 계약 보강 — 2026-08-23 계약 감사 51건 (이슈 #17 조정자 판정). 감사 id 를 각 줄에 남긴다.
-- 추가만: CREATE TABLE / ADD COLUMN 뿐이고 001 은 손대지 않는다 (tests/test_ddl_additive_only.py 가 강제).
-- 주석에도 백분율 기호를 쓰지 않는다: 테스트 하네스가 이 파일을 psycopg 로 실행하고 그 문자를 파라미터 자리로 읽는다.

-- ---------- 사전 ----------
-- B4: 두 규칙 사전(suncare-v2.2 / p1-v2.2)이 한 테이블에 섞여 어느 쪽도 재현되지 않았다.
-- 값: suncare-v2.2 | p1-v2.2 | shared(두 사전에 동일한 행). 로더는 ruleset IN (<요청>, 'shared') 로 읽는다.
ALTER TABLE needs.aspect_lexicon ADD COLUMN ruleset text NOT NULL DEFAULT '';
-- B5: 규칙 출력이 매칭 순서에 의존한다. 오름차순, 동률은 id (formats.md).
ALTER TABLE needs.aspect_lexicon ADD COLUMN priority int NOT NULL DEFAULT 0;

-- A17: 같은 개념이 두 슬라이스에서 다른 이름으로 들어와 scope='all' 롤업이 둘로 나뉜다.
CREATE TABLE needs.need_key (
  need_key        text PRIMARY KEY,
  canonical       text NOT NULL,             -- 동의어 묶음의 대표 이름; 대표가 없으면 자기 자신
  note            text
);

-- A18: 리뷰의 카테고리 유도 규칙이 슬라이스 코드 상수로만 존재했다.
CREATE TABLE needs.category_map (
  site            text NOT NULL,             -- 사이트 이름, 또는 '*' = 모든 사이트
  source_category text NOT NULL,             -- method=rank_snapshot: 사이트 카테고리 leaf / method=name_keyword: 제품명 정규식
  lexicon_category text NOT NULL,            -- aspect_lexicon.category 어휘
  method          text NOT NULL CHECK (method IN ('rank_snapshot','name_keyword')),
  PRIMARY KEY (site, source_category)
);

-- ---------- 제품 식별 ----------
-- A13: 230개 후보쌍(재검수 자산)이 갈 곳이 없었고 product_member.match_score 의 출처가 여기다. B3: variant 검수 큐도 이것.
CREATE TABLE needs.product_ref_candidate (
  src_a           text NOT NULL,
  key_a           text NOT NULL,
  src_b           text NOT NULL,
  key_b           text NOT NULL,
  brand           text,
  shared_tok      int,
  shared_sig      int,
  dice            numeric,
  mutual          boolean,
  linker_version  text NOT NULL,
  PRIMARY KEY (src_a, key_a, src_b, key_b, linker_version)
);

-- A14: 슬라이스는 SKU 히트만 낸다. line_key = brand || ' ' || line_tokens (formats.md).
ALTER TABLE needs.product_line_mention ADD COLUMN brand text;
ALTER TABLE needs.product_line_mention ADD COLUMN line_tokens text;

-- ---------- 언급 ----------
-- B9: 극성 판단 근거. 규칙에서는 오류 분석의 단서, LLM(#6)에서는 비용을 들여 받은 유일한 감사 흔적.
ALTER TABLE needs.need_mention ADD COLUMN polarity_reason text;
-- B10: category 는 사이트 원문이라 '선블록'으로 조회되지 않는다 — 어느 사전 카테고리로 판정했는지.
ALTER TABLE needs.need_mention ADD COLUMN lexicon_category text;
-- A9: wish_mention 에는 있고 need_mention 에만 없던 표지. complaint | wish | low_rating.
ALTER TABLE needs.need_mention ADD COLUMN kind text;
ALTER TABLE needs.need_mention ADD COLUMN marker text;

-- ---------- 분모·랭킹 파생 ----------
-- B6: 언급 0건 제품을 포함한 카테고리 분모는 need_mention 에서 복원할 수 없다.
ALTER TABLE needs.product_denominator ADD COLUMN category text;
-- A19: 이 세 테이블만 run_id 도 version 도 없어 어느 실행이 만든 행인지 알 수 없었다 (versioning.md).
ALTER TABLE needs.product_denominator ADD COLUMN aggregate_version text NOT NULL DEFAULT '';
ALTER TABLE needs.rank_daily ADD COLUMN aggregate_version text NOT NULL DEFAULT '';
ALTER TABLE needs.price_event ADD COLUMN aggregate_version text NOT NULL DEFAULT '';
-- A16: present_share 가 2자리 반올림이라 n_snapshots 에서 역산되지 않는다.
ALTER TABLE needs.rank_daily ADD COLUMN n_present int;
-- A15: rank_post24 가 스냅샷 2개 평균인지 24개 평균인지 가르는 표본 크기 가드.
ALTER TABLE needs.price_event ADD COLUMN n_pre int;
ALTER TABLE needs.price_event ADD COLUMN n_post24 int;

-- ---------- 집계 ----------
-- A1: 유튜브 댓글 측 불만/만족 집계 전량.
ALTER TABLE needs.metrics_need ADD COLUMN yt_neg int;
ALTER TABLE needs.metrics_need ADD COLUMN yt_pos int;
-- A2: persist_* 의 분모. 절대수만으로는 카테고리 간 비교가 안 된다.
ALTER TABLE needs.metrics_need ADD COLUMN persist_months_total int;
ALTER TABLE needs.metrics_need ADD COLUMN persist_products_total int;
-- A3: 불만 강도 평균 (yt_like_mean 은 슬라이스가 스스로 폐기 권고했으므로 추가하지 않는다).
ALTER TABLE needs.metrics_need ADD COLUMN strength_mean numeric;
-- A4: "2026 신제품이 이 불만을 해결했는가" — 목표 가설의 핵심 검증 지표.
ALTER TABLE needs.metrics_need ADD COLUMN unresolved_new numeric;
-- A5: scope 는 카테고리명이므로 generic/category 축을 따로 적는다 (need_mention.aspect_scope 와 같은 어휘).
ALTER TABLE needs.metrics_need ADD COLUMN aspect_scope text;
-- A6: 비율의 분자·분모 원값. 표본 3건짜리 비율과 300건짜리 비율을 구분하려면 필요하다.
ALTER TABLE needs.metrics_need ADD COLUMN low_mentioning int;
ALTER TABLE needs.metrics_need ADD COLUMN denom_low int;
ALTER TABLE needs.metrics_need ADD COLUMN denom_site int;
-- A7: 단일 영상 집중·1건 지배 판정과 창 안 존재 구간, 그리고 사람이 읽을 예시 문장.
ALTER TABLE needs.metrics_wish ADD COLUMN videos int;
ALTER TABLE needs.metrics_wish ADD COLUMN max_like int;
ALTER TABLE needs.metrics_wish ADD COLUMN first_month text;
ALTER TABLE needs.metrics_wish ADD COLUMN last_month text;
ALTER TABLE needs.metrics_wish ADD COLUMN example text;
