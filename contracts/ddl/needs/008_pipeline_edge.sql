-- #141: 무엇이 무엇을 먹이는가. 추가만 (tests/test_ddl_additive_only.py).
--
-- 007 의 pipeline_stage 는 단계 *목록* 일 뿐 관계가 없다. 그림(#142)도 상태 전파(#143)도 계보
-- 추적(#144)도 그 관계 없이는 못 선다.
--
-- 노드 표를 따로 두지 않는다. 단계는 pipeline_stage.stage_key 가 이미 선언하고, 저장소는 DB
-- 자신이 레지스트리다 -- 키가 정규화된 표 이름이고 tests/test_pipeline_edge.py 가 to_regclass 로
-- 실재를 묻는다. 노드 표를 두면 같은 사실을 두 자리에 적게 된다.
--
-- 방향은 둘 다 담는다: stage -> store 는 쓴다, store -> stage 는 읽는다. 한 방향만 담으면 계보가
-- 한쪽으로만 흘러 #144 가 지표에서 수집분으로 거꾸로 못 탄다.
CREATE TABLE needs.pipeline_edge (
  from_key   text NOT NULL,
  from_kind  text NOT NULL,
  to_key     text NOT NULL,
  to_kind    text NOT NULL,
  note       text NOT NULL DEFAULT '',
  PRIMARY KEY (from_key, to_key),
  CONSTRAINT pipeline_edge_from_kind_check CHECK (from_kind IN ('stage', 'store')),
  CONSTRAINT pipeline_edge_to_kind_check   CHECK (to_kind IN ('stage', 'store')),
  -- 단계끼리 직접 잇지 않는다. 단계 사이에는 언제나 그것이 남긴 표가 있고, 그 표를 건너뛰면
  -- 계보가 "무엇을 통해" 를 잃는다.
  CONSTRAINT pipeline_edge_not_stage_to_stage CHECK (NOT (from_kind = 'stage' AND to_kind = 'stage'))
);
