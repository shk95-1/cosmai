-- 025: the evidence comments attached to a verdict cell (fork issue #6). Additive only
-- (tests/test_ddl_additive_only.py).
--
-- ydc evidence_comments.py drops per-topic evidence comments into a CSV and cards.py matches that
-- against the verdict CSV by hand. This table is that match: the eight columns are
-- needs.topic_quarter_judgement's primary key as they stand, so an evidence row cannot stand
-- without the verdict cell it supports, and the path from cell to evidence is one FK.
--
-- 024 와 같은 이유로 이름이 metrics_ 로 시작하지 않고 contracts/formats.md §시간 의 "집계 그레인의
-- 정본" 표에 줄을 갖지 않는다 -- 세는 칸이 하나도 없다. 다만 024 와 다른 점이 하나 있다: 판정은 지표
-- 행 하나에서 행 하나를 내는 파생이고, 근거는 그 셀을 만든 **문서를 도로 가리키는 포인터**다.
CREATE TABLE needs.topic_quarter_evidence (
  -- The eight columns are topic_quarter_judgement's primary key as it stands. Pointing at the
  -- verdict table rather than metrics_topic_quarter is meaning -- evidence is asked for by whoever
  -- reads the verdict, not by whoever reads the metrics.
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
  -- The evidence body is not copied here. The corpus is canonical, and a copy makes it unknowable
  -- which side is the original (the same sentence as analysis/retrieval/corpus.py keeping no second
  -- set of CSVs). It points at the document instead, and the view
  -- needs.topic_quarter_evidence_quote joins cell to original text in one line.
  snapshot_id   int  NOT NULL,
  doc_id        text NOT NULL,
  -- Evidence comes only from documents that cell's source produced. Since doc_id is
  -- source || ':' || source_item_id (023's generated column), the rule stands inside a single row --
  -- attaching a comment as evidence for a video cell is blocked here.
  CHECK (split_part(doc_id, ':', 1) = source),
  -- 고른 이유를 행에 남긴다. 좋아요는 collected_at 시점의 스냅샷이라(interfaces.md §모집단의 한계)
  -- 나중에 세어 보면 다른 수가 나온다 -- 그때 이 정렬을 설명할 수 있는 것은 저장된 이 값뿐이다.
  like_count    int  NOT NULL CHECK (like_count >= 0),
  -- The expression by which this document matched that topic. corpus_mention already recorded it
  -- and nothing is matched again here.
  matched_term  text,
  picked_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role, rank),
  -- If one comment takes two slots in a cell, the evidence set is really one. Uniqueness
  -- on the slot (rank) alone cannot stop that.
  UNIQUE (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role, doc_id),
  FOREIGN KEY (run_id, scope, topic_key, quarter, source, content_type, panel_version, panel_role)
    REFERENCES needs.topic_quarter_judgement,
  FOREIGN KEY (snapshot_id, doc_id) REFERENCES needs.corpus_document (snapshot_id, doc_id)
);
-- The screen asks for "this quarter's consumer speech on this topic", not "this run's" (the same
-- reason as 024 and 022).
CREATE INDEX ON needs.topic_quarter_evidence (topic_key, quarter);

GRANT SELECT, INSERT, UPDATE, DELETE ON needs.topic_quarter_evidence TO needs_runtime;
