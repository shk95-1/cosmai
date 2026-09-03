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
### 주제 사전 v3 — ydc 별칭과 사전 후보의 판정 원장 (포크 #56)
`lexicon.json` 의 별칭 9 중 자리가 있던 셋(`썬크림`·`자외선차단제`·`코스알엑스`)은 이미 사전에 있었고, `선크림추천` 은 `bm25.expand` 의 부분문자열 확장이 잡아 행이 필요 없다(포크 #37 1c). 남은 5종과 `protected` 32 가 남긴 후보 7종을 여기서 판정한다. **원장은 `tool/measure-lexicon-candidates` 의 `LEDGER` 이고**(판정·자리·근거·df·new 가 한 자리에 있다), 그 도구가 261,317 문서(`archive/yt-handoff/document.csv`, 읽기 전용) 위에서 그 수를 다시 재 원장과 **정확히 맞대는** 길이다(어긋나면 종료 코드 1). 주제 단위 수(아래 12,197 → 12,418 · 959 → 2,021)는 같은 도구의 `--topics` 가 낸다. `tests/retrieval/test_lexicon_v3.py` 가 원장과 적재 원본을 맞댄다.
- **세는 규칙은 사전의 매칭 규칙이다** — `ko` 는 부분문자열(대소문자 무시), `latin` 은 경계 매칭. 섞으면 수가 갈린다: `sunscreen` 은 부분문자열로 **81**, 경계로 **76** 이고 차이 다섯은 복수형 `sunscreens` 다. 사전이 경계로 매칭하므로 원장의 값은 76 이다(#56 이 이슈 본문의 81 을 고친 자리).
- **df 하나로는 등재를 못 정한다.** 이미 있는 별칭이 그 문서를 전부 보고 있으면 새 행은 아무것도 관측하지 않는다. 그래서 원장은 df 옆에 `new` 를 함께 든다 — `톤업크림` 628 편은 `톤업` 이 전부 보므로 `new` 가 0 이다. **`new` 의 기준 사전은 원장의 표기를 *전부* 뺀 사전이다**: 한 행씩 빼며 잰 값이 아니므로 같은 주제에 여럿이 붙으면 `new` 의 합이 그 주제의 실제 델타보다 크다(선크림 다섯의 합 224 vs 실제 +221 — 겹쳐 나오는 문서를 각각 세기 때문이다).
- **등재는 넷을 다 넘어야 한다.**
  1. **df ≥ `analysis/retrieval/terms.MIN_DOCS`(5)** — 이 이슈가 지어낸 바닥이 아니라 미포착 표현 표가 이미 쓰는 바닥이다.
  2. **그 행이 없으면 관측되지 않는 것이 있되, 있던 것을 잃지 않는다** — 매칭이 넓어지거나(`new` > 0) 토큰이 달라지고, **기존 토큰을 잃지 않는다.** 별칭은 Kiwi 사용자 단어가 되어 복합어를 한 덩어리로 묶으므로 조각 토큰이 사라질 수 있다: `속건조` 는 `속건조` 를 얻고 확장이 `건조` 를 지켜 통과하지만, `톤업크림` 은 `크림` 을, `비비크림` 은 `비비`·`크림` **둘 다** 잃어 걸린다.
  3. **그 주제의 뜻을 바꾸지 않는다** — 별칭은 같은 축의 다른 표기여야 한다. 축이 같은지는 뜻으로 가르고 실측이 그 판단을 되묻는다: **그 주제의 등장 문서를 50% 이상 늘리는 말은 별칭이 아니라 그 주제가 지금까지 안 세던 것**이다. 지금 이 문턱은 데이터가 한 점뿐이지만 판정이 그 값에 둔감하다 — 통과한 최대가 `파데프리` +14.6% 이고 걸린 최소가 `화잘먹` +110.7% 라 사이가 비어 있다.
  4. **그 주제 유형의 축이어야 한다** — 사전에서 제품 범주는 `topic_type='product_category'`(`선크림`, `trend_use=false`) 자리이지 `attribute` 주제가 아니다. `비비크림` 은 1~3 을 다 넘고 여기서 걸린다.
- v3 가 더한 것은 일곱이다: `선크림` 에 `썬쿠션`·`썬스틱`·`선에센스`·`선스프레이`(ko)와 `sunscreen`(latin) · `촉촉함_건조함` 에 `속건조` · `톤업_메이크업베이스` 에 `파데프리`. **`속건조` 는 매칭이 아니라 토큰으로 자리를 얻었다**(`new` 0): `건조` 가 2,217 문서를 이미 보지만 Kiwi 가 `속`+`건조` 로 쪼개 그 말을 정확히 찾을 수 없었다.
- **행이 되지 않은 여덟.** `올영`(5,583) — 자리는 `entity_lexicon`(kind=brand)이지만 정본 `올리브영` 이 유통 채널이라 `tier='stop'` 이고, `analysis/lexicon.compile_lexicon` 이 stop 정본의 표면을 `surface_re` 에서 통째로 빼므로 행을 더해도 링커·추출기의 산출이 한 비트도 안 바뀐다. 게다가 brand 는 `db/seed/lexicon.py` 가 `LEXICON_VERSION`(1) 으로 적재하는 유일한 길이고 `activate` 는 그 kind 를 통째로 갈아끼우므로, 행 하나를 더하려면 950표기짜리 v2 를 통째로 세워야 한다 — 자리를 만드는 것이 먼저다. `sunstick`(3) — 기준 1 아래이고 그 3편을 이미 `선크림` 이 본다. `톤업크림`(628 · new 0) — 기준 2. `화잘먹`(1,154 · new 1,062) — 기준 3: `밀림_들뜸` 의 별칭 넷이 전부 결함어인데 반대 방향의 결과어라 959 → 2,021(+110.7%)이 된다. `비비크림`(698 · new 581) — 기준 4. `모공막힘`(5 · new 5) — **보류**: 네 기준을 다 넘지만 표본이 바닥과 같아 축(`자극_눈시림` 의 `트러블` 계열인지)을 가를 수 없다. `케미컬`(3)·`olive영`(0) — 기준 1, #37 판정 유지.
- **선크림 주제가 넓어진다.** v3 의 다섯 표기가 그 주제에 붙어 활성 사전으로 세는 문서가 12,197 → 12,418(**+221**)이 된다. 이 수는 `match_topics` 를 직접 부르는 자리(`retrieval eval` 의 정답 · `terms` · `crosscheck`)에만 걸린다 — 분기 지표·근거·민감도는 `corpus_mention`(2026-08-19 관측, ydc 의 매칭)을 읽으므로 사전 버전이 그 표들을 움직이지 않는다.


### entity 사전의 `kind='stopword'` — 질의 불용어 (포크 #46)
- 한 줄 = 질의에서 지울 표기 하나. `canonical` 은 정본 표기가 아니라 **그 표기가 걸리는 축**이다 — 지금 값은 `query` 하나뿐이고, 색인·추출 축에는 불용어를 두지 않는다는 판단(`entrypoints.md` §검색, 포크 #8·#37)이 그대로 서 있다. 축을 `kind` 로 가르지 않은 이유는 `activate` 가 kind 단위라 축을 늘릴 때마다 새 버전 축이 생기기 때문이고, `tier` 로 가르지 않은 이유는 그 칸이 brand 전용 어휘를 이미 갖고 있어서다.
- **두 번째 축은 지금 자리가 없다.** 유일키가 `UNIQUE (kind, surface, version)`(`001_needs.sql:50`)라 `canonical` 을 안 담으므로, 다른 축이 같은 `surface` 를 가지면 `db/lexicon.py` 의 `ON CONFLICT … DO NOTHING` 이 오류 없이 **조용히 버린다**. 그러니 위 문단은 축을 늘리는 여지를 약속하지 않는다 — 늘리려면 유일키를 넓히는 추가 DDL 이 먼저다(등급 B 리뷰 M3, 2026-08-26).
- `surface` 는 정규식도 원형도 아니라 **`bm25.tokenize` 가 실제로 내놓는 토큰**이다 — 필터가 토큰 목록 위에서 돌기 때문이다(`관해서` → `관하`, `어떻게` → `어떻`). 그래서 목록을 고치는 사람은 표기가 아니라 토큰을 적어야 하고, `tests/retrieval/test_query_stopwords.py` 가 그 파일의 프로브 질의 다섯 개에서 각 행의 토큰이 나오는지를 되묻는다(코퍼스 전수가 아니라 손으로 고른 시험 벡터다) — 나오지 않는 행은 지우지 않고 `note` 가 그 사실을 적는다(판단은 같고 도달만 못 하므로, 형태소 분석기가 바뀌면 살아난다).
- 적재 원본은 `analysis/retrieval/dict/query_stopwords_v1.csv`(13행)이고 길은 `cosmai lexicon load/diff/activate --kind stopword` 하나다. **활성 버전**은 aspect 와 따로 돈다(`entity_lexicon` 의 `activate` 는 `WHERE kind = %s`).
- 그러나 버전 **번호표**는 `entity_lexicon` 전역이다 — `analysis/lexicon.py` 의 `_label` 과 `analysis/aggregate/pipeline.py:149` 가 kind 를 안 가리고 `max(version)` 을 읽으므로, 이 목록을 v2 로 올리면 `brand` 가 v1 그대로여도 run 의 `versions.lexicon` 이 2 가 된다. `:149` 는 `active` 조차 안 보므로 **`activate` 전 `load` 만으로도** 그렇게 된다. 지금은 두 kind 가 다 v1 이라 무해하고, 고치는 것은 **포크 #58** 이 진다 — 이 목록이 그 선재 성질을 밟는 첫 사용자다. **주제 사전 v3(포크 #56)은 그 자리를 밟지 않는다**: `aspect_lexicon` 은 다른 표라 `max(version)` 을 나눠 갖지 않고, `versions.lexicon` 은 `entity_lexicon` 만 읽는다(`aggregate/pipeline.py:149`). aspect 를 v3 로 올려도 그 칸은 1 그대로다 — 움직이는 것은 `versions.lexicon.aspect`(`analysis/pipeline.py:139`) 뿐이고 그쪽은 ruleset 마다 따로 싣는다.

## need_key 레지스트리 CSV (→ `needs.need_key`, A17)
`need_key,canonical,note` — 두 슬라이스 어휘의 합집합. `canonical` 은 동의어 묶음의 대표이고, 대표가 없으면 자기 자신이다.
v1 의 동의어 5쌍(suncare 이름 → p1 이름): `밀림→밀림들뜸` · `향→향냄새` · `발림텍스처→제형발림` · `지속력워터→지속력` · `톤업색상→색상발색`. `site_axis_map.need_key` 가 p1 어휘라서 대표를 그쪽으로 맞춘다. `scope='all'` 롤업은 `canonical` 기준으로 합산한다.

## 카테고리 매핑 CSV (→ `needs.category_map`, A18)
`site,source_category,lexicon_category,method,priority`
- `method='rank_snapshot'`: `source_category` 는 사이트 카테고리의 leaf(` > ` 로 나눈 마지막 조각). `site='*'` 는 모든 사이트에 적용한다.
- `method='name_keyword'`: `source_category` 는 **제품명 정규식**이다 — 랭킹 스냅샷이 없는 제품(글로우픽)의 폴백. 정규식은 서로 겹치므로(`선크림|…` 과 `크림` 이 "선크림"에 둘 다 맞는다) **`priority` 오름차순으로 먼저 맞는 것**을 쓴다. 동률의 순서는 정의하지 않는다 — 동률을 만들지 마라. v1 은 CSV 행 번호(1부터)를 그대로 쓴다.
- 유도 순서: 사이트 카테고리 leaf → 없으면 `name_keyword` → 그래도 없으면 카테고리 없음. 표에 없는 leaf 는 그대로 `lexicon_category` 가 된다(항등).

## 카테고리 표기 (A21, #123)
`category` 의 정본은 하나뿐이다: **사이트가 발행한 카테고리 경로를 자르지 않은 문자열**. 아래 세 자리가
그 같은 문자열을 쓴다 — 한 자리가 leaf 로 자르면(`'01 > 선케어 > 선블록'` → `'선블록'`) 두 값은 절대
같아지지 않고 카테고리 scope 는 분모를 하나도 받지 못한다(운영 실측 run 24: 카테고리 scope 22개에서
`population_share_pct`·`low_share`·`denom_low`·`denom_site` 전부 NULL).

| 자리 | 값 |
|---|---|
| `needs.need_mention.category` | `trend_radar.rank_snapshot.category_name` 원문 (`analysis/units.py:review_unit`) |
| `needs.product_denominator.category` | 같은 문자열 (`analysis/aggregate/ranking.py:denominators`) |
| `needs.metrics_need.scope` | 같은 문자열 — `scope='all'` 롤업만 예외 (`analysis/aggregate/pipeline.py:scopes_for`) |

- 사이트마다 깊이가 다르다: oliveyoung 은 `'01 > 선케어 > 선블록'`, glowpick 은 `'크림'`, daisomall 은
  `'뷰티/위생'` 하나뿐이다. 얕은 값도 그 사이트가 발행한 **경로 전체**이므로 이미 정본이다 — 정본은
  "계층형으로 만들어라"가 아니라 "원문을 자르지 마라"다.
- 사이트가 카테고리를 말하지 않으면 NULL 이다. 사전 라벨로 메우지 않는다 — 그것은 `lexicon_category`
  (B10)의 자리이고, 두 열은 뜻이 다르다. 제품명 정규식 폴백(`category_map.method='name_keyword'`)은
  `lexicon_category` 만 낸다.
- leaf 로 자른 짧은 형이 필요하면 `analysis/units.py:leaf()` 로 그때 자른다. 저장하지 않는다 —
  경로→leaf 는 함수지만 leaf→경로는 아니다(`'블러셔'` 는 glowpick 의 `'블러셔'` 이기도 하고
  oliveyoung 의 `'02 > 베이스 메이크업 > 블러셔'` 이기도 하다).

```python
CATEGORY_CANONICAL_SOURCE = "trend_radar.rank_snapshot.category_name"
CATEGORY_CANONICAL_COLUMNS = (
    "needs.need_mention.category",
    "needs.product_denominator.category",
    "needs.metrics_need.scope",
)
```

## 패널 명부 CSV (→ `needs.panel_channel`, 포크 #3)
한 줄 = 한 채널. ydc 의 모든 비율이 이 명부를 분모로 쓴다(시드 원본 `eval/panel/channels_v1.csv`, **43채널**). 파일은 11열이고 여섯 열만 표로 간다 — 나머지 다섯은 적재하지 않는다. 무엇을 버렸는지가 여기 적혀 있지 않으면 #31 의 적재기와 이 스펙이 조용히 갈라진다.

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
- 적재는 `db/seed/panel.py`(`python -m db.seed --only panel`) 하나다 — 새 CLI 가 아니라 다른 시드와 같은 자리다(포크 #31). 원본은 슬라이스에 있었고 #9 가 그 디렉터리를 지웠으므로 `eval/` 로 옮겼다. 옮기며 **UTF-8 BOM 을 뗐다**: `db/seed/_common.read_csv` 는 utf-8 로 열어서 BOM 이 남으면 첫 열 이름이 `channel_id` 가 아니게 되고, 열 이름 11개는 그대로다.
- **활성 판본은 언제나 하나다.** `active` 가 행 단위라 부분 인덱스는 두 판본이 동시에 켜진 상태를 막지 못하고, 그러면 `WHERE active` 를 타는 분모가 43 대신 86 이 된다. 부분 유니크 인덱스로는 "활성 행의 distinct version 이 하나"를 쓸 수 없으므로 이 불변식은 적재기가 진다 — 한 문장짜리 `SET active = (version = n)`(`db/seed/panel.activate`)과, 둘이면 답 대신 멈추는 `panel.active_version` (포크 #3 리뷰 L6 · #31).

## 코퍼스 스냅샷 (→ `needs.corpus_snapshot` / `corpus_document` / `corpus_mention`, 포크 #4)
한 줄 = 한 문서다. 영상과 댓글이 같은 표에 산다(`content_type` 이 가른다). 원본은 ydc 인계 CSV 세 장
(`archive/yt-handoff/`, document 261,317 · mention 105,358 · channel 43행)이고, **그 자리는 읽기 전용**
(`STATE.md` §3)이라 적재기가 경로를 인자로 받는다(`python -m db.corpus load <dir>`). 슬라이스에도
매니페스트 사본이 있었지만(`analysis/slices/ydc/common/manifest.json`) 줄바꿈만 다른 같은 JSON 이라
폐기했다 — 적재기가 읽는 것은 언제나 인자로 받은 디렉터리의 것이다(포크 #37).

**이 행들은 2026-08-19 의 관측이지 "지금의 유튜브"가 아니다.** 재수집으로 다시 만들 수 없다 — 댓글은
계속 쌓이고 조회수·좋아요는 `collected_at` 시점의 값이다. 그래서 관측 판본(`snapshot_id`)이 유일키의
**맨 앞**에 서고(`corpus_document` PK = `(snapshot_id, source, source_item_id)`), 재수집(#38)은 다른
판본으로 들어와 옛 행 옆에 선다. 덮이지 않는 것은 적재기 규율이 아니라 키의 성질이다. 어느 판본을
분석이 읽는지는 `corpus_snapshot.active` 한 칸이고, 판본당 한 행이라 그 불변식은 부분 유니크
인덱스가 진다(023) — `panel_channel` 이 같은 문장을 적재기에 지운 것과 갈리는 자리다.

`channel.csv` 는 **표가 되지 않는다.** 채널의 역할은 분모를 정하는 값이라 한 표(§패널 명부 CSV 의
`panel_channel`)에만 살아야 하고, 두 표에 살면 두 분모가 생겨 나중 것이 앞선 것과 조용히 갈린다.
반입은 대신 **대조한다**: 코퍼스가 언급하는 채널이 전부 활성 명부에 같은 역할로 있어야 하고, 아니면
거절한다(`db/corpus.check_channels`). 그 파일의 `uploads_playlist_id` 도 표로 가지 않는다 — 43행 전부
`'UU' || substr(channel_id, 3)` 이라 값이 아니라 유도식이다.

### 매니페스트가 못박은 규칙 (`manifest.rules`, 그대로)
1. 유일키는 source + source_item_id 다. doc_id 는 그 둘을 콜론으로 이은 값이다.
2. 분기는 저장하지 않는다. published_at 의 연·월로 달력 분기를 만든다(수집 13,979편 전부 analysis_month 와 일치함을 확인).
3. 댓글은 published_at 이 자기 시각이므로 분기 판정에 쓰지 않는다. parent_item_id 로 부모 영상에 조인해 부모의 분기에 배정한다.
4. 트렌드 판정 분모는 content_type = video_long 만 쓴다. video_short 는 별도 계열, video_unknown 은 양쪽에서 제외한다.
5. 판정·보고 모집단은 channel.panel_role = product 로 한정한다.
6. 선크림 모집단 필터는 topic_id = 선크림(trend_use = false)으로 만든다.
7. mention 은 주제 15개 전부를 담는다. 판정용 13개는 trend_use = true 로 필터한다.
8. 행을 지우지 않는다. 품질 문제는 quality_flags 로 표시한다(empty_text, duplicate_in_parent).
9. 언급량 집계에서는 quality_flags 가 빈 문서만 센다. duplicate_in_parent 는 같은 영상 안 복붙이라 반응 1건으로 보지 않는다.
10. 댓글은 주제 사전에 걸린 영상만 수집했다. 전체 영상에 대한 댓글 분모는 존재하지 않는다.
11. 태그를 판정 텍스트에 포함할지는 미결이다. 포함하면 선크림 장문이 962 → 1,019편이 되고 모든 composition 이 움직인다.

규칙 3·4 는 이 파일이 처음 말하는 문장이 아니다: 댓글의 분기 귀속과 장문만인 분모는
`interfaces.md` §수식 의 "분기 문서 모집단" 이 이미 지고 있고, 여기 있는 것은 그 문장이 **이 코퍼스의
원 규칙과 같다**는 대조다. 이 11줄은 `db/corpus/contract.py` 에 상수로 서 있고, 적재기는 읽어 들인
매니페스트가 그것과 다르면 반입을 거절한다 — 다른 규칙으로 만들어진 코퍼스가 같은 표에 섞이면 그
표의 모든 비율이 오류 없이 달라진다.

### 매니페스트가 선언한 행수 (`manifest.table_counts` · `documents_by_content_type`)
적재기는 **선언한 행수와 반입분을 대조한다**(`db/corpus.load` → `contract.check_counts`) — 다르면 켜기
전에 거절한다. 규칙·한계와 달리 이 값은 판본마다 달라서 계약이 수를 지지 않고 대조한다는 규칙만
진다. 잘려 들어온 CSV 는 행이 **적을 뿐** 오류가 없고, 그러면 이 스냅샷의 모든 비율이 조용히 달라진다
— `reproduces` 는 선크림 한 주제만 다시 세므로 그 바깥이 잘린 것을 잡지 못한다. 2026-08-19 판본의 값은
document 261,317 · mention 105,358 · channel 43 이고 문서 구성은 `video_long` 7,085 · `video_short`
6,888 · `video_unknown` 6 · `comment` 247,338 이다.

대조가 어긋나면 **행은 남는다.** 문서 26만 행을 다 읽은 뒤에야 구성이 드러나므로 거절이 반입 뒤에
선다 — 다만 그 행들은 자기 `snapshot_id` 아래에 있고 **켜지지 않으므로** 분석은 그것을 읽지 않는다.
출구는 지우는 것이 아니라 원본을 고쳐 **같은 `snapshot_id` 로 다시 부르는 것**이다(모든 INSERT 가
`ON CONFLICT DO NOTHING` 이라 빠진 자리만 메운다). `DROP` 은 매번 승인이라 출구로 쓰지 않는다.

`input_counts`(입력 영상 13,979 · 댓글 247,338 · 중복 문서 0 · 고아 댓글 0 · 중복 댓글 본문 237)는
대부분 **보류**다(포크 #37): ydc `to_common_schema.py` 가 변환 **이전**에 센 값이라 반입분 위에서 다시
셀 수 없다. 두 칸만 여기서 선다 — 입력 행수의 합(13,979 + 247,338)이 위 document 행수이고,
`duplicate_docs = 0` 은 새 판본에서 **읽은 행수와 들어간 행수가 같은지**로 증명한다
(`db/corpus.load` → `contract.check_unique`; `ON CONFLICT DO NOTHING` 이 중복을 조용히 버리므로 세지
않으면 잘려 들어온 파일과 구분되지 않는다). 나머지는 그대로 보류이고, 그중
**`orphan_comments` 는 DB 가 지지 않는다** — 댓글의 부모는 `corpus_document.parent_item_id`(023) 이고
거기엔 FK 가 없다(부분 인덱스뿐이다). `corpus_mention` 의 FK 가 지는 것은 고아 **언급**이지 고아
**댓글**이 아니다.

### text 의 뜻 (`manifest.text_rule`, 그대로)
> 영상 text = 정규화(제목 + 공백 + 설명). 댓글 text = 정규화(본문). 정규화는 HTML 엔티티 해제 → NFKC → 제어문자 제거 → 공백 축약이며 trend.py 의 normalize_text 를 그대로 쓴다. 태그는 text 에 넣지 않고 source_metadata.tags 로 보낸다. 자막·음성은 PoC 제외.

- **cosmai 의 정규화와 대조**: `analysis/retrieval/normalize.py` 의 `normalize_text` 는 같은 네 단계를
  **고정점까지** 돌리고, ydc `trend.py` 의 것은 한 번만 돌린다(이중 이스케이프 `&amp;lt;` 에서 갈린다).
  두 구현이 다르므로 `text` 가 다른 뜻이 될 수 있는 자리인데, 이 코퍼스에서는 갈리지 않는다 — 실측
  2026-08-26 기준 261,317행 전부가 한 번으로 이미 고정점이고(달라지는 행 0), 수집기가
  `textFormat=plainText` 로 받아 HTML 이 애초에 들어오지 않기 때문이다. 재수집분에는 이 성질이
  보장되지 않는다.
- 한계 문장 여덟은 `interfaces.md` §모집단의 한계 가 진다 — 그것들은 포맷이 아니라 **숫자를 읽는
  법**이라 수식 옆에 있어야 한다.

## 언급 행의 ref 문법 (A20)
| src | `ref` | 비고 |
|---|---|---|
| review | `product_key/review_key` | |
| yt_comment | `video_id/comment_id` | `need_mention` 과 `wish_mention` **둘 다** 이 문법. 댓글 하나가 두 테이블에서 같은 키를 갖는다 |
| yt_transcript | `video_id` | |
| yt_title | `video_id` | `TextUnit` 전용 — `need_mention` 에는 들어가지 않는다 |
| naver_blog | `post_id` | **예약됨(미구현, #96)** — 원천 테이블 `needs.naver_blog_post` (004_naver.sql, #9, T15)까지는 있고, 라이브 전송(`collectors/naver/cli.py:_RaisingFetcher`, #95)과 분석 갈래(`analysis/polarity/pipeline.py`)가 없어 이 src 의 `need_mention` 행은 아직 생기지 않는다 |
- `needs.need_mention.src` 의 CHECK(001_needs.sql)는 이미 `naver_blog` 를 값으로 받아둔다 — DDL 은 추가만이라 예약을 미리 걸어도 해가 없다(#96).
- `brand_mention` 은 `ref_id` 를 쓰고 src 어휘가 다르다: `yt_title→title` · `yt_transcript→transcript` · `yt_comment→comment` (B12).
- `labeled_set.ref` 는 별도 이름공간이다(`sun:<split>:<i>:<review_ref>` · `p1:<split>:<i>` · wish 는 `comment_id` 단독 · `<sample>:<src>/<ref_id>/<brand>` · `<v1|v2>:<i>`). 언급 행과 조인하려면 변환이 필요하다.

## NAVER DataLab: ratio 는 요청 안에서만 비교 가능 (#44)
NAVER DataLab 은 **요청 하나 안에서** 시리즈들의 최댓값을 100 으로 맞춰 나머지를 그 비율로 되돌린다(벤더
문서). 그래서 `needs.naver_datalab_point.ratio` 는 **같은 요청으로 나온 행끼리만** 크기를 비교할 수 있다
— 다른 요청(다른 `category`, 또는 같은 category 라도 다른 실행)에서 나온 행과 비교하면, 각자 다른 100 을
기준으로 정규화된 숫자를 같은 잣대인 양 취급하는 것이라 **오류 없이 그럴듯한 틀린 숫자**가 나온다.
`category`·`group_key` 로 GROUP BY 해서 순위를 매기는 정도는 안전하지만, 서로 다른 `request_key` 를 가진
행을 비교·합산하기 전에는 **앵커 재척도**(전역 앵커 키워드를 여러 요청에 공통으로 넣어 그 비율로 되돌리는
것 — 이슈 #90 이 앵커 선정과 재척도 시점을 결정한다)가 선행해야 한다. 이 계약은 재척도 없이 요청을 가로지른
비교를 하지 말라는 제약이고, #90 은 어떻게 재척도하는지를 정한다.

**요청 경계는 `naver_datalab_point.request_key` 로 행에서 읽는다**(`contracts/ddl/needs/006_naver_request.sql`,
결정 (가): `terms` 는 그룹 하나의 검색어 감사용일 뿐 `startDate`/`endDate`/`timeUnit` 을 담지 않아 같은
그룹의 다른 실행을 구별하지 못한다). `request_key` = 실제로 보낸 요청 바디
(`keywordGroups`·`startDate`·`endDate`·`timeUnit`) 의 canonical JSON(`json.dumps(sort_keys=True)`) 을
sha256 한 hex digest(`collectors/naver/parsing.py:datalab_request_key`) — 같은 파라미터는 같은 키를,
`endDate` 가 하루라도 움직이면(그 창이 다시 스케일되므로) 다른 키를 낸다. 한 응답이 만든 모든 행은 같은
`request_key` 를 공유한다.

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

**판정 표(`needs.topic_quarter_judgement`, 포크 #40)는 이 표에 줄을 갖지 않는다.** 그 표는 문서를 세지 않고
`metrics_topic_quarter` 의 행을 받아 같은 키로 한 행씩 낸다 — 언급 수·채널 수·지속성 중 어느 것도 들지
않으므로 정본을 다툴 상대가 없다. 집계가 아니라 파생이라는 그 사실이 이름(`metrics_` 로 시작하지 않는다)과
FK(지표 행의 기본키 여덟 칸 전부)에 동시에 서 있다. 근거 문장은 `interfaces.md` §판정.

**근거 표(`needs.topic_quarter_evidence`, 포크 #6)도 같은 이유로 여기 줄이 없다.** 그 표는 세지 않고
**가리킨다** — 판정 셀 하나에 그 셀을 만든 코퍼스 문서 몇 개를 좋아요 순으로 붙일 뿐이라 시간 칸(`quarter`)도
자기 것이 아니라 가리키는 셀의 것이다. 근거 문장은 `interfaces.md` §근거.
