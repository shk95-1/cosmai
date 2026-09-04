-- #28 검색 유닛: 청크 저장소. 추가만 (tests/test_ddl_additive_only.py).
--
-- 번호가 020 인 것은 이 파일이 장수 브랜치 feat/ydc-import 의 것이기 때문이다. main 이 00N 을
-- 계속 쓰는 동안 파일명이 겹치지 않아야 하고, db/migrate.sh 는 파일명 순으로 자기 체크아웃에
-- 있는 파일만 훑으므로 원장에 020 행이 남아도 main 의 배포는 그냥 지나간다. 추가만이라 순서가
-- 결과를 바꾸지 않는다.
--
-- 청크는 원천의 파생물이지 원천이 아니다. 그래서 원천 행이 사라지면 다시 만들면 그만이고,
-- 외래키를 걸지 않는다 -- 원천은 다른 스키마(tubedepth · trend_radar)에 있고 needs 는 그쪽에
-- SELECT 권한만 갖는다(db/grants/needs_runtime_reader.sql).
CREATE TABLE needs.retrieval_chunk (
  chunk_id    text PRIMARY KEY,          -- `{doc_id}#{ordinal}` (analysis/retrieval/chunks.py FIELDS)
  doc_id      text NOT NULL,             -- `{source}:{원천 키}` -- 소스를 합치는 일이 join 이 아니라 이어 붙이기가 되는 지점
  source      text NOT NULL,             -- youtube_comment | youtube_video | youtube_transcript | commerce_review
  ordinal     int  NOT NULL,             -- 문서 안에서 0 부터 연속
  text        text NOT NULL,             -- normalize_text 를 거친 본문
  text_md5    text NOT NULL,             -- 재청킹이 같은 결과인지 보는 값. 본문 비교는 2704B btree 상한에 걸린다
  chunked_at  timestamptz NOT NULL DEFAULT now()
);

-- 한 문서의 조각을 순서대로 되짚는 조회(근거 표시)와, 소스별 집계·평가가 각각 쓴다.
CREATE INDEX ON needs.retrieval_chunk (doc_id, ordinal);
CREATE INDEX ON needs.retrieval_chunk (source);

GRANT SELECT, INSERT, UPDATE, DELETE ON needs.retrieval_chunk TO needs_runtime;
