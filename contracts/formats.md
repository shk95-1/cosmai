# 포맷

## 사전 CSV (→ `needs.entity_lexicon` / `needs.aspect_lexicon` 적재)
- entity: `kind,canonical,surface,tier,source,note` — 한 줄 = 한 surface. 영문 별칭은 실제 코퍼스에 나타난 것만 (P3: 영문은 거의 무용).
- aspect: `aspect,scope,category,pattern,is_neutral_noun,ruleset,priority` — pattern 은 Python `re`. 중립 명사 쌍둥이(펌프/거품/탈모 등)는 `is_neutral_noun=true`.
- 버전: 적재 시 `--version n` 으로 부여, `activate` 로 교체. 같은 버전 재적재는 no-op — 단 `ruleset`·`priority` 는 v1 행에 뒤늦게 생긴 컬럼이라 재적재가 그 두 값만 갱신한다.

### aspect 사전의 ruleset 과 순서 (B4·B5)
- `ruleset ∈ {suncare-v2.2, p1-v2.2, shared}`. `shared` = 두 사전에 **같은 패턴으로** 들어 있는 행(현재 `선블록`의 `백탁`·`색상어두움` 2행)이고, UNIQUE 가 중복 적재를 막으므로 한 행으로만 존재한다.
- 로더는 항상 `WHERE version = <v> AND ruleset IN (<요청 ruleset>, 'shared')` 로 읽는다. `scope='generic' OR category='선블록'` 같은 조건은 어느 슬라이스도 재현하지 못하는 혼합물을 준다.
- 매칭 순서 = `priority` 오름차순, 동률은 `id` 오름차순. `priority` 는 `scope='category'` 0, `scope='generic'` 1 — 카테고리 전용 패턴이 같은 이름의 generic 을 가린다.
- 중립 명사 쌍둥이는 같은 `aspect` 이름을 갖는다(원본 사전의 `~` 접미는 CSV 에 남기지 않는다). 쌍둥이는 원본 순서 그대로 뒤에 온다 = 같은 priority, 큰 id.

## need_key 레지스트리 CSV (→ `needs.need_key`, A17)
`need_key,canonical,note` — 두 슬라이스 어휘의 합집합. `canonical` 은 동의어 묶음의 대표이고, 대표가 없으면 자기 자신이다.
v1 의 동의어 5쌍(suncare 이름 → p1 이름): `밀림→밀림들뜸` · `향→향냄새` · `발림텍스처→제형발림` · `지속력워터→지속력` · `톤업색상→색상발색`. `site_axis_map.need_key` 가 p1 어휘라서 대표를 그쪽으로 맞춘다. `scope='all'` 롤업은 `canonical` 기준으로 합산한다.

## 카테고리 매핑 CSV (→ `needs.category_map`, A18)
`site,source_category,lexicon_category,method`
- `method='rank_snapshot'`: `source_category` 는 사이트 카테고리의 leaf(` > ` 로 나눈 마지막 조각). `site='*'` 는 모든 사이트에 적용한다.
- `method='name_keyword'`: `source_category` 는 **제품명 정규식**이다 — 랭킹 스냅샷이 없는 제품(글로우픽)의 폴백. 파일 순서대로 먼저 맞는 것을 쓴다.
- 유도 순서: 사이트 카테고리 leaf → 없으면 `name_keyword` → 그래도 없으면 카테고리 없음. 표에 없는 leaf 는 그대로 `lexicon_category` 가 된다(항등).

## 언급 행의 ref 문법 (A20)
| src | `ref` | 비고 |
|---|---|---|
| review | `product_key/review_key` | |
| yt_comment | `video_id/comment_id` | `need_mention` 과 `wish_mention` **둘 다** 이 문법. 댓글 하나가 두 테이블에서 같은 키를 갖는다 |
| yt_transcript | `video_id` | |
| yt_title | `video_id` | `TextUnit` 전용 — `need_mention` 에는 들어가지 않는다 |
| naver_blog | `post_id` | 원천 테이블은 5단계에 생긴다 (T15) |
- `brand_mention` 은 `ref_id` 를 쓰고 src 어휘가 다르다: `yt_title→title` · `yt_transcript→transcript` · `yt_comment→comment` (B12).
- `labeled_set.ref` 는 별도 이름공간이다(`sun:<split>:<i>:<review_ref>` · `p1:<split>:<i>` · wish 는 `comment_id` 단독 · `<sample>:<src>/<ref_id>/<brand>` · `<v1|v2>:<i>`). 언급 행과 조인하려면 변환이 필요하다.

## 스칼라 컬럼에 들어가는 목록 (A12)
`wish_mention.format` · `wish_mention.attribute` 는 `;` 로 구분하고 **최대 3개**, **첫 번째가 주 값**이다. 집계는 첫 값만 쓴다.
`product_line_mention.line_key` = `brand || ' ' || line_tokens` (A14).

## aspect 없음 (B8)
`need_mention.need_key` 는 NOT NULL 이고 UNIQUE 의 일부다. aspect 를 못 정한 행(규칙의 `neg-only`/`pos-only`/`no-aspect`, LLM 의 `aspect=null`)은 **`need_key=''` 센티널**로 저장한다. `metrics_need` 집계는 `need_key=''` 를 **제외**한다.

## 표본 상수 (T5)
`low_complete = (low_collected < 150) or has_3star`. 150 은 RATING_ASC 수집 표본 상한(`REVIEW_PAGES 3 x 50`)이고 `collectors/commerce/scope.json`(#7)이 같은 값을 갖는다.

## 평가셋 CSV (→ `needs.labeled_set`)
`task,ref,split,gold,text,labeler,labeled_at,extra(json)`
- 현재 보유 (eval/ 에 원본): polarity sun 200 tune + 100 holdout, polarity P1 60 tune + 40 holdout, wish_class 100 tune + 60 holdout + blind60_v2 60 holdout, brand_link 120, product_match 80쌍.
- 라벨 기준(polarity): 작성자가 이 제품에서 겪은 부정 경험이 있으면 불만(약해도), "X 없음/적음"류 만족 표현은 만족, 타제품·취향·피부타입 서술·잘린 문장·배송은 중립.
- 라벨 기준(wish): a = 브랜드에 대한 제품/출시/복각 요청, b = 크리에이터에 대한 콘텐츠 요청, c = 일반 희망.

## 시간
- 공통 집계 그레인 = 월 (`'YYYY-MM'`). 모든 언급 행은 `observed_at_resolution` 을 가진다.
- YouTube 댓글 `published_at` 은 상대시간 복원 → 2025-09 이후만 `month`, 그 이전은 `year`.
- 랭킹은 `rank_daily` (KST 일). 글로우픽·화해는 갱신이 드물어 `valid_from/valid_to` 로 중복 제거.
