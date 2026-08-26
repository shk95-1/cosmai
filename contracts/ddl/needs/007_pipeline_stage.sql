-- #138: 파이프라인 단계의 기대 주기를 계약으로. 추가만 (tests/test_ddl_additive_only.py).
--
-- 왜 표인가 -- 기대 주기는 stack/crontab.d/ 에만 있었고 포털은 PostgREST 로 DB 만 읽는다. 크론탭을
-- 파싱해 넣는 대신 여기서 선언하는 이유는 enabled 다: youtube watch 는 크론 줄이 *있는데* compose
-- profile 뒤라 안 돈다(STATE.md §2, 재가동은 #39). 크론탭도 DB 도 그 사실을 모르므로 누군가 선언해야
-- 하고, 자동화는 문제의 절반만 없앤다. 크론탭과의 어긋남은 tests/test_pipeline_stage.py 가 막는다.
--
-- 값은 db/seed/pipeline.py 가 넣는다. 판정은 이 표를 읽는 뷰 needs.pipeline_health 가 진다 --
-- 화면·tool/status·나중의 알림이 같은 답을 하려면 판정이 한 자리에 있어야 한다.
CREATE TABLE needs.pipeline_stage (
  stage_key         text PRIMARY KEY,          -- '<arm>:<dataset>'. analyze 증분 패스만 _missing 접미
  arm               text NOT NULL,
  dataset           text NOT NULL,
  expected_interval interval NOT NULL,         -- 크론 줄이 뜻하는 주기. late/stalled 의 눈금이 여기서 나온다
  enabled           boolean NOT NULL DEFAULT true,
  note              text NOT NULL DEFAULT '',
  -- collector_health 의 세 팔 + 분석. 새 팔이 생기면 뷰의 UNION 도 함께 늘어야 하므로 여기서 막는다.
  CONSTRAINT pipeline_stage_arm_check CHECK (arm IN ('commerce', 'naver', 'youtube', 'analyze'))
);
