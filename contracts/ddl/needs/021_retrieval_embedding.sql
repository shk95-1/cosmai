-- #28 검색 유닛 단계 4: 청크 임베딩. 추가만 (tests/test_ddl_additive_only.py).
-- 020 과 같은 이유로 이 브랜치의 번호 블록(020번대)을 쓴다.
--
-- 타입을 `public.vector` 로 한정한 이유. 확장은 public 에 설치되는데 needs_runtime 의
-- search_path 는 `needs, pg_catalog` 라 public 이 없다(db/bootstrap.sql). 한정하지 않으면
-- 이 DDL 은 슈퍼유저 세션에서는 서고 runtime 세션에서는 타입을 못 찾는다.
--
-- 벡터 자체가 아니라 **무엇으로 만든 벡터인지**를 같이 적는다. 모델 리비전·프리픽스·L2 정규화가
-- 하나만 어긋나도 코사인 유사도는 오류 없이 숫자를 내고 순위만 조용히 틀린다 -- ydc 가
-- encode_chunks 의 매니페스트로 막던 것을 여기서는 행마다 적어 막는다.
CREATE TABLE needs.retrieval_embedding (
  chunk_id      text PRIMARY KEY REFERENCES needs.retrieval_chunk (chunk_id) ON DELETE CASCADE,
  model         text NOT NULL,            -- intfloat/multilingual-e5-base
  revision      text NOT NULL,            -- HF 커밋 sha. 못 읽으면 'unknown' -- 그것도 사실이다
  doc_prefix    text NOT NULL,            -- e5 계열은 문서에 `passage: ` 가 붙어야 한다
  l2_normalized boolean NOT NULL,         -- 코사인을 내적으로 계산해도 되는지를 가른다
  embedding     public.vector(768) NOT NULL,
  embedded_at   timestamptz NOT NULL DEFAULT now()
);

-- HNSW + 코사인. 768차원은 ivfflat 의 목록 수를 고르는 일이 데이터 크기에 매여 있어, 청크가
-- 계속 늘어나는 이 표에는 다시 만들 필요가 없는 HNSW 가 맞다.
CREATE INDEX ON needs.retrieval_embedding USING hnsw (embedding public.vector_cosine_ops);
-- 한 모델로 다시 태울 때 어느 행이 낡았는지 고르는 조회.
CREATE INDEX ON needs.retrieval_embedding (model, revision);

GRANT SELECT, INSERT, UPDATE, DELETE ON needs.retrieval_embedding TO needs_runtime;
