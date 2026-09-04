-- need_mention natural key replacement -- it tears up 001's UNIQUE (src, ref, need_key, sentence).
-- User approval 2026-08-24 (#12 option A + the #5 production failure). 001 is not edited: this
-- file is the correction.
--
-- Why 1 -- the btree ceiling. An unbounded `sentence` sat in the btree key as it was. The first
-- production run of #5:
--   run 4 | failed | analyze:all product_ref=179 brand_mention=97161
--          failed:polarity index row size 3336 exceeds btree version 4 maximum 2704
--                         for index "need_mention_src_ref_need_key_sentence_key"
-- Only 6 of trend_radar.review's 19,811 rows exceed 2600B (3218B at most, where a review with no sentence
-- boundary became one whole "sentence"), and those 6 stop the entire run. md5(sentence) is 32
-- characters, so the key is bounded.
--
-- Why 2 -- with no version in the key, the seed and the analysis fought over the same slot.
-- Measured in #3: 400 of the
-- 400 seeded slice-suncare review mentions carry the same key the analysis makes. Measured in #4:
-- on seed load, 548 slice-p1 rows are
-- absorbed into slice-suncare rows by DO NOTHING. With extractor_version in the key the two coexist.
--
-- Why a UNIQUE INDEX and not a UNIQUE constraint: PostgreSQL's ADD CONSTRAINT ... UNIQUE takes no
-- expression. ON CONFLICT has to be written in the same expression form as this index to match it.
-- Zero duplicates under the new key across 60,910 production rows (checked ahead in the
-- coordinating session) -- the index builds as is.
ALTER TABLE needs.need_mention DROP CONSTRAINT need_mention_src_ref_need_key_sentence_key;
CREATE UNIQUE INDEX need_mention_natural_key
  ON needs.need_mention (src, ref, need_key, extractor_version, md5(sentence));
