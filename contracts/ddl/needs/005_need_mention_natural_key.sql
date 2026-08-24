-- need_mention 자연키 교체 — 001 의 UNIQUE (src, ref, need_key, sentence) 를 갈아엎는다.
-- 사용자 승인 2026-08-24 (#12 안 A + #5 운영 실패). 001 은 수정하지 않는다: 이 파일이 정정이다.
--
-- 왜 1 — btree 상한. 길이 제한 없는 `sentence` 가 btree 키에 그대로 들어가 있었다. #5 운영 첫 실행:
--   run 4 | failed | analyze:all product_ref=179 brand_mention=97161
--          failed:polarity index row size 3336 exceeds btree version 4 maximum 2704
--                         for index "need_mention_src_ref_need_key_sentence_key"
-- trend_radar.review 19,811건 중 2600B 초과는 6건뿐인데(최대 3218B, 문장 분할점이 없어 리뷰 하나가
-- 통째로 한 "문장"이 된 경우) 그 6건이 실행 전체를 멈춘다. md5(sentence) 는 32자라 키가 유계가 된다.
--
-- 왜 2 — 버전이 키에 없어 시드와 분석이 같은 자리를 다퉜다. #3 실측: 시드 slice-suncare 리뷰 언급
-- 400행 중 400/400 이 분석이 만드는 것과 같은 키다. #4 실측: 시드 적재 때 slice-p1 548행이
-- slice-suncare 행에 DO NOTHING 으로 흡수된다. extractor_version 이 키에 들어가면 둘은 공존한다.
--
-- UNIQUE 제약이 아니라 UNIQUE INDEX 인 이유: PostgreSQL 의 ADD CONSTRAINT ... UNIQUE 는 표현식을
-- 받지 않는다. ON CONFLICT 는 이 인덱스와 같은 표현식 형태로 써야 매칭된다.
-- 운영 60,910행에 새 키 중복 0건(조정 세션 사전 확인) — 인덱스가 그대로 만들어진다.
ALTER TABLE needs.need_mention DROP CONSTRAINT need_mention_src_ref_need_key_sentence_key;
CREATE UNIQUE INDEX need_mention_natural_key
  ON needs.need_mention (src, ref, need_key, extractor_version, md5(sentence));
