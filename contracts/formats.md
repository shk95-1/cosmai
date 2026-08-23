# 포맷

## 사전 CSV (→ `needs.entity_lexicon` / `needs.aspect_lexicon` 적재)
- entity: `kind,canonical,surface,tier,source,note` — 한 줄 = 한 surface. 영문 별칭은 실제 코퍼스에 나타난 것만 (P3: 영문은 거의 무용).
- aspect: `aspect,scope,category,pattern,is_neutral_noun` — pattern 은 Python `re`. 중립 명사 쌍둥이(펌프/거품/탈모 등)는 `is_neutral_noun=true`.
- 버전: 적재 시 `--version n` 으로 부여, `activate` 로 교체. 같은 버전 재적재는 no-op.

## 평가셋 CSV (→ `needs.labeled_set`)
`task,ref,split,gold,text,labeler,labeled_at,extra(json)`
- 현재 보유 (eval/ 에 원본): polarity sun 200 tune + 100 holdout, polarity P1 60 + 40, wish_class 100 + 60, brand_link 120, product_match 80쌍.
- 라벨 기준(polarity): 작성자가 이 제품에서 겪은 부정 경험이 있으면 불만(약해도), "X 없음/적음"류 만족 표현은 만족, 타제품·취향·피부타입 서술·잘린 문장·배송은 중립.
- 라벨 기준(wish): a = 브랜드에 대한 제품/출시/복각 요청, b = 크리에이터에 대한 콘텐츠 요청, c = 일반 희망.

## 시간
- 공통 집계 그레인 = 월 (`'YYYY-MM'`). 모든 언급 행은 `observed_at_resolution` 을 가진다.
- YouTube 댓글 `published_at` 은 상대시간 복원 → 2025-09 이후만 `month`, 그 이전은 `year`.
- 랭킹은 `rank_daily` (KST 일). 글로우픽·화해는 갱신이 드물어 `valid_from/valid_to` 로 중복 제거.
