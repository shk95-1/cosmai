# 포맷

## 사전 CSV (→ `needs.entity_lexicon` / `needs.aspect_lexicon` 적재)
- entity: `kind,canonical,surface,tier,source,note` — 한 줄 = 한 surface. 영문 별칭은 실제 코퍼스에 나타난 것만 (P3: 영문은 거의 무용).
- aspect: `aspect,scope,category,pattern,is_neutral_noun,ruleset,priority` — pattern 은 Python `re`. 중립 명사 쌍둥이(펌프/거품/탈모 등)는 `is_neutral_noun=true`.
- aspect CSV 의 **알려진 일곱 칸 밖의 열은 `extra`**(jsonb, 021)로 간다 — 룰셋마다 필요한 사실이 다르고, 그것을 공통 칸에 얹으면 한 컬럼이 룰셋마다 다른 뜻을 갖는다. 빈 칸은 값이 아니라 무기입이라 `extra` 에 들어가지 않는다.
- 버전: 적재 시 `--version n` 으로 부여, `activate` 로 교체. 같은 버전 재적재는 no-op — 예외는 `ruleset`·`priority` 가 아직 비어 있는(`ruleset=''`) 행의 **1회 백필**뿐이다(002 이전에 적재된 v1 행). 값이 한 번 들어간 뒤로는 재적재가 아무것도 바꾸지 않는다: 사전 내용은 버전으로만 바뀐다.

### aspect 사전의 ruleset 과 순서 (B4·B5)
- `ruleset ∈ {suncare-v2.2, p1-v2.2, shared, retrieval-topic}` — DDL 의 CHECK 로 묶지 않는다. 값이 사전 버전마다 늘어나므로(`suncare-v2.3` …) 어휘를 DDL 에 박으면 사전 개정마다 마이그레이션이 필요해진다. `shared` = 두 사전에 **같은 패턴으로** 들어 있는 행(현재 `선블록`의 `백탁`·`색상어두움` 2행)이고, UNIQUE 가 중복 적재를 막으므로 한 행으로만 존재한다.
- 로더는 항상 `WHERE version = <v> AND ruleset IN (<요청 ruleset>, 'shared')` 로 읽는다. `scope='generic' OR category='선블록'` 같은 조건은 어느 슬라이스도 재현하지 못하는 혼합물을 준다.
- 매칭 순서 = `priority` 오름차순, 동률은 `id` 오름차순. `priority` 는 `scope='category'` 0, `scope='generic'` 1 — 카테고리 전용 패턴이 같은 이름의 generic 을 가린다.
- `retrieval-topic` 은 검색 유닛의 **주제 사전**이다(포크 #8, 적재 원본 `analysis/retrieval/dict/topics_v1.csv`). 한 행 = 한 주제의 한 별칭이고 `pattern` 은 정규식이 아니라 **표기 그대로**다 — 한글은 부분문자열, 라틴은 경계 매칭(`(?<![A-Za-z])…`)이라 매칭 방식이 계열마다 다르고, 그 별칭이 Kiwi 사용자 단어이자 확장 목록이라 정규식으로는 쓸 수 없다. `extra` 가 나머지를 나른다: `term_kind ∈ {ko, latin, mfds_inci}`(`|` 로 겹칠 수 있다 — 아보벤존은 ko 이자 식약처 표기다) · `topic_type` · `trend_use` · `note`. 뒤 셋은 주제 단위 사실이라 그 주제의 아무 행에 한 번만 적고(관례: 첫 행), 두 행이 다른 값을 말하면 적재가 아니라 `analysis/retrieval/topics.py` 가 거절한다.
- 중립 명사 쌍둥이는 같은 `aspect` 이름을 갖는다(원본 사전의 `~` 접미는 CSV 에 남기지 않는다). 쌍둥이는 원본 순서 그대로 뒤에 온다 = 같은 priority, 큰 id.

## need_key 레지스트리 CSV (→ `needs.need_key`, A17)
`need_key,canonical,note` — 두 슬라이스 어휘의 합집합. `canonical` 은 동의어 묶음의 대표이고, 대표가 없으면 자기 자신이다.
v1 의 동의어 5쌍(suncare 이름 → p1 이름): `밀림→밀림들뜸` · `향→향냄새` · `발림텍스처→제형발림` · `지속력워터→지속력` · `톤업색상→색상발색`. `site_axis_map.need_key` 가 p1 어휘라서 대표를 그쪽으로 맞춘다. `scope='all'` 롤업은 `canonical` 기준으로 합산한다.

## 카테고리 매핑 CSV (→ `needs.category_map`, A18)
`site,source_category,lexicon_category,method,priority`
- `method='rank_snapshot'`: `source_category` 는 사이트 카테고리의 leaf(` > ` 로 나눈 마지막 조각). `site='*'` 는 모든 사이트에 적용한다.
- `method='name_keyword'`: `source_category` 는 **제품명 정규식**이다 — 랭킹 스냅샷이 없는 제품(글로우픽)의 폴백. 정규식은 서로 겹치므로(`선크림|…` 과 `크림` 이 "선크림"에 둘 다 맞는다) **`priority` 오름차순으로 먼저 맞는 것**을 쓴다. 동률의 순서는 정의하지 않는다 — 동률을 만들지 마라. v1 은 CSV 행 번호(1부터)를 그대로 쓴다.
- 유도 순서: 사이트 카테고리 leaf → 없으면 `name_keyword` → 그래도 없으면 카테고리 없음. 표에 없는 leaf 는 그대로 `lexicon_category` 가 된다(항등).

## 패널 명부 CSV (→ `needs.panel_channel`, 포크 #3)
한 줄 = 한 채널. ydc 의 모든 비율이 이 명부를 분모로 쓴다(시드 원본 `analysis/slices/ydc/seeds/channels_v1.csv`, **43채널**, UTF-8 BOM). 파일은 11열이고 여섯 열만 표로 간다 — 나머지 다섯은 적재하지 않는다. 무엇을 버렸는지가 여기 적혀 있지 않으면 #31 의 적재기와 이 스펙이 조용히 갈라진다.

| CSV 열 | → `needs.panel_channel` |
|---|---|
| `channel_id` | `channel_id` |
| `handle` | `handle` |
| `channel_title` | `channel_title` |
| `panel_role` | `panel_role` (아래 두 값) |
| `role_basis` | `role_basis` |
| `source_list` | `source_list` |
| `team_rank` | — 팀 내부 순위. 역할 판정의 근거는 `role_basis` 한 칸이 진다 |
| `team_role` | — 위와 같다 |
| `channel_published_at` | — 원천(`tubedepth`)이 이미 가진 채널 사실이라 명부가 두 번 들지 않는다 |
| `video_count_at_seed` | — 위와 같다 (시드 시점 스냅샷) |
| `subscriber_count_at_seed` | — 위와 같다 |

| `panel_role` | 뜻 | v1 패널 |
|---|---|---|
| `product` | 제품을 다루는 채널. ydc 의 분기 지표는 이 모집단 위에 선다 | 34 |
| `expert` | 피부과·약사 등 전문가 채널 | 9 |

- **역할은 원천이 아니라 `needs` 파생에 산다**(사용자 결정 2026-08-26). 원천(`tubedepth`)의 채널 표는 upstream 계약이고 `tool/checks/ddl-drift` 가 지키는 자리라 포크가 컬럼을 더할 자리가 아니다. 43채널은 고정 목록이라 시드 한 벌로 충분하고, 패널 구성이 바뀌면 시드를 다시 적재한다.
- **대가는 알고 받는다: 새 채널이 수집에 들어와도 역할이 자동으로 붙지 않는다.** 명부에 없는 채널은 패널 밖이므로 분모에 안 들어가는 것이 맞고, 그 사실이 행에서 읽히기만 하면 된다 — `metrics_topic_quarter` 의 `panel_version`·`panel_role`·`denom_channels` 가 그 자리다. '역할 비슷한 값'이 들어올 자리는 없다(DDL 의 CHECK, 022).
- 버전은 사전과 같은 모양이다: 적재 시 `version` 을 부여하고 `active` 로 교체한다. 집계 행은 자기가 쓴 명부를 `panel_version` 으로 가리키므로, 패널이 바뀐 뒤에도 옛 행이 무엇을 분모로 삼았는지 남는다. 그 판본은 한 줄짜리 부모 `needs.panel_roster(version)` 에 살고 명부 행과 집계 행이 **둘 다 FK 로** 그 줄을 가리킨다 — `needs_runtime` 이 두 표에 DELETE 를 갖고 있어, 부모가 없으면 명부 판본이 지워진 뒤 그 문장이 거짓이 된다.
- 값 적재(43채널)는 이 계약의 범위가 아니라 포크 #31 이다. 여기는 값이 들어갈 **자리와 뜻**만 정한다.

## 언급 행의 ref 문법 (A20)
| src | `ref` | 비고 |
|---|---|---|
| review | `product_key/review_key` | |
| yt_comment | `video_id/comment_id` | `need_mention` 과 `wish_mention` **둘 다** 이 문법. 댓글 하나가 두 테이블에서 같은 키를 갖는다 |
| yt_transcript | `video_id` | |
| yt_title | `video_id` | `TextUnit` 전용 — `need_mention` 에는 들어가지 않는다 |
| naver_blog | `post_id` | 원천 테이블 `needs.naver_blog_post` (004_naver.sql, #9, T15) |
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
- 언급 행의 그레인 = 월 (`'YYYY-MM'`). 모든 언급 행은 `observed_at_resolution` 을 가진다.
- 집계의 그레인은 **둘**이다 — 월과 분기(`'YYYYQn'`; `2026-07` → `2026Q3`). 분기는 월의 대체가 아니라 추가다: 월은 `need_mention` 과 `metrics_*` 전체가 딛고 선 입자라, 기존 표에 입자를 가리키는 열을 더하면 이미 있는 행의 뜻이 바뀐다(포크 #3 결정 2). 그래서 분기는 자기 표에 산다.
- **분기 행은 월 행을 셋 더한 것이 아니다.** 모집단이 다르다 — 월 행의 분모는 제품·카테고리(`product_denominator`)고 분기 행의 분모는 패널(§패널 명부 CSV)이다. 두 표를 합산하지 않고, 한 표를 다른 표에서 유도하지도 않는다. 분기 값은 그 시점의 확정값이라 뷰로 매번 다시 계산하지 않는다.
- 분기의 비교 상대는 인접 분기가 아니라 **전년 동분기**다(YoY; `2026Q3` ↔ `2025Q3`). 선케어는 계절 상품이라 — 장문 영상 중 선크림 언급 비중이 3년 연속 Q2 최고·Q1/Q4 최저였다 — 인접 분기를 비교하면 계절성이 매년 같은 트렌드로 읽힌다.
- YouTube 댓글 `published_at` 은 상대시간 복원 → 2025-09 이후만 `month`, 그 이전은 `year`.
- 랭킹은 `rank_daily` (KST 일). 글로우픽·화해는 갱신이 드물어 `valid_from/valid_to` 로 중복 제거.
- 원천 관측 시각이 NULL 이면(`trend_radar.review.written_at` 은 nullable 인데 `need_mention.observed_at`·`month` 는 NOT NULL) `observed_at = captured_at`(수집 시각의 날짜), `observed_at_resolution = 'day'` 로 폴백한다. 폴백은 리뷰를 수집한 달에 몰아넣으므로, `analyze` 는 폴백이 적용된 행 수를 세어 `analysis_run.note` 에 기록한다 — 이 값이 0 이 아니게 되는 순간이 규칙을 다시 볼 때다. (2026-08-23 실측: `trend_radar.review` 19,786행 중 0행.)

### 집계 그레인의 정본
| 그레인 | 정본 표 | 행의 시간 칸 |
|---|---|---|
| 월 | `needs.metrics_need` | `month` (`'YYYY-MM'`, `''` = 전체 기간) |
| 월 | `needs.metrics_wish` | `first_month`·`last_month`·`months_present` (키에는 시간이 없다) |
| 분기 | `needs.metrics_topic_quarter` | `quarter` (`'YYYYQn'`) |

같은 개념의 지표(언급 수·채널 수·지속성)가 두 표에 살게 되므로, **한 (그레인 × 개념)의 값을 묻는 자리는 이 표가 가리키는 표 하나뿐이다** — 월에 정본 표가 둘인 것은 개념이 둘(need·wish)이기 때문이고, 한 표가 두 그레인을 지는 일은 없다. 새 집계 표는 이 표에 줄을 더하면서 선다 — `tests/test_panel_quarter_contract.py` 가 그레인 없는 `metrics_*` 표를 잡는다.
