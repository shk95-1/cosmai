-- 021: aspect 사전의 룰셋별 여분 칸 (포크 이슈 #8).
--
-- `aspect_lexicon` 한 버전에는 룰셋이 여럿 산다(`suncare-v2.2` · `p1-v2.2` · `shared`, 그리고
-- 이 이슈가 들여오는 검색 유닛의 `retrieval-topic`). 주제 사전 행은 공통 일곱 칸에 자리가 없는
-- 사실 셋을 더 들고 다녀야 한다: 별칭의 표기 계열(ko|latin|mfds_inci) · 주제 유형 · 트렌드 판정
-- 사용 여부. 그것들을 `category`·`priority` 같은 공통 칸에 얹으면 한 컬럼이 룰셋마다 다른 뜻을
-- 갖게 되므로, 룰셋이 자기 어휘를 담는 칸을 따로 준다(`labeled_set.extra` 와 같은 모양).
--
-- 기존 행은 `{}` 로 남고 읽는 쪽은 아무것도 바꾸지 않는다 -- 추가만.
ALTER TABLE needs.aspect_lexicon ADD COLUMN extra jsonb NOT NULL DEFAULT '{}'::jsonb;
