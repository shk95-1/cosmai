-- 025: 판정 셀에 붙는 근거 댓글 (포크 이슈 #6). 추가만 (tests/test_ddl_additive_only.py).
--
-- ydc evidence_comments.py 는 주제별 근거 댓글을 CSV 로 떨구고 cards.py 가 그것을 판정 CSV 와 손으로
-- 맞춘다. 그 맞춤이 이 표다: 여덟 칸이 needs.topic_quarter_judgement 의 기본키 그대로여서 근거 행은
-- 자기가 받치는 판정 셀 없이 설 수 없고, 셀에서 근거로 가는 길이 FK 하나다.
--
-- 024 와 같은 이유로 이름이 metrics_ 로 시작하지 않고 contracts/formats.md §시간 의 "집계 그레인의
-- 정본" 표에 줄을 갖지 않는다 -- 세는 칸이 하나도 없다. 다만 024 와 다른 점이 하나 있다: 판정은 지표
-- 행 하나에서 행 하나를 내는 파생이고, 근거는 그 셀을 만든 **문서를 도로 가리키는 포인터**다.
CREATE TABLE needs.topic_quarter_evidence (
  -- 여덟 칸은 topic_quarter_judgement 의 기본키 그대로다. metrics_topic_quarter 가 아니라 판정 표를
  -- 가리키는 것이 뜻이다 -- 근거는 지표를 읽는 사람이 아니라 판정을 읽는 사람이 묻는 것이다.
  run_id        bigint NOT NULL,
  scope         text NOT NULL,
  topic_key     text NOT NULL,
  quarter       text NOT NULL,
  source        text NOT NULL,
  content_type  text NOT NULL,
  panel_version int  NOT NULL,
  panel_role    text NOT NULL,
  -- 그 셀 안에서 좋아요 내림차순 자리. 상한(셀당 몇 건)은 여기 적지 않는다 -- 그 수는 보고서의 손잡이라
  -- 바뀌고, DDL 은 추가만이라 CHECK 을 되돌릴 수 없다 (contracts/interfaces.md §근거 가 그 자리다).
  rank          int  NOT NULL CHECK (rank >= 1),
  -- 근거의 본문을 여기 베끼지 않는다. 코퍼스가 정본이고 사본을 두면 어느 쪽이 원문인지 알 수 없게
  -- 된다(analysis/retrieval/corpus.py 가 CSV 한 벌을 더 두지 않는 것과 같은 문장). 대신 그 문서를
  -- 가리키고, 셀에서 원문까지는 뷰 needs.topic_quarter_evidence_quote 가 한 줄로 잇는다.
  snapshot_id   int  NOT NULL,
  doc_id        text NOT NULL,
  -- 근거는 그 셀의 source 가 낸 문서에서만 나온다. doc_id 는 source || ':' || source_item_id 이므로
  -- (023 의 생성 열) 그 규칙이 행 하나 안에서 선다 -- 댓글을 영상 셀의 근거로 다는 것이 여기서 막힌다.
  CHECK (split_part(doc_id, ':', 1) = source),
  -- 고른 이유를 행에 남긴다. 좋아요는 collected_at 시점의 스냅샷이라(interfaces.md §모집단의 한계)
  -- 나중에 세어 보면 다른 수가 나온다 -- 그때 이 정렬을 설명할 수 있는 것은 저장된 이 값뿐이다.
  like_count    int  NOT NULL CHECK (like_count >= 0),
  -- 이 문서가 그 주제에 걸린 표현. corpus_mention 이 이미 단 값이고 여기서 다시 매칭하지 않는다.
  matched_term  text,
  picked_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role, rank),
  -- 한 셀에 같은 댓글이 두 자리를 차지하면 근거 셋이 실은 하나다. 자리(rank)만 유일하면 그것을 못 막는다.
  UNIQUE (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role, doc_id),
  FOREIGN KEY (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role)
    REFERENCES needs.topic_quarter_judgement,
  FOREIGN KEY (snapshot_id, doc_id) REFERENCES needs.corpus_document (snapshot_id, doc_id)
);
-- 화면이 묻는 것은 "이 분기 이 주제의 소비자 발화"이지 "이 run 의"가 아니다 (024·022 와 같은 이유).
CREATE INDEX ON needs.topic_quarter_evidence (topic_key, quarter);

GRANT SELECT, INSERT, UPDATE, DELETE ON needs.topic_quarter_evidence TO needs_runtime;
