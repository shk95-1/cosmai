# Formats

## Lexicon CSV (→ loaded into `needs.entity_lexicon` / `needs.aspect_lexicon`)
- entity: `kind,canonical,surface,tier,source,note` — one row = one surface. Latin aliases only where they actually appeared in the corpus (P3: Latin is nearly useless).
- aspect: `aspect,scope,category,pattern,is_neutral_noun,ruleset,priority` — pattern 은 Python `re`. 중립 명사 쌍둥이(펌프/거품/탈모 등)는 `is_neutral_noun=true`.
- **Columns of an aspect CSV outside the seven known ones go to `extra`** (jsonb, 021) — each ruleset needs different facts, and putting them on a shared column gives one column a different meaning per ruleset. An empty cell is not a value but an absence, so it does not enter `extra`.
- Versions: assigned at load with `--version n`, swapped with `activate`. Re-loading the same version is a no-op — the one exception is a **single backfill** of rows whose `ruleset`·`priority` are still empty (`ruleset=''`), i.e. v1 rows loaded before 002. Once a value is in, re-loading changes nothing: dictionary content changes by version alone.

### The aspect lexicon's ruleset and order (B4·B5)
- `ruleset ∈ {suncare-v2.2, p1-v2.2, shared, retrieval-topic}` — DDL 의 CHECK 로 묶지 않는다. 값이 사전 버전마다 늘어나므로(`suncare-v2.3` …) 어휘를 DDL 에 박으면 사전 개정마다 마이그레이션이 필요해진다. `shared` = 두 사전에 **같은 패턴으로** 들어 있는 행(현재 `선블록`의 `백탁`·`색상어두움` 2행)이고, UNIQUE 가 중복 적재를 막으므로 한 행으로만 존재한다.
- 로더는 항상 `WHERE version = <v> AND ruleset IN (<요청 ruleset>, 'shared')` 로 읽는다. `scope='generic' OR category='선블록'` 같은 조건은 어느 슬라이스도 재현하지 못하는 혼합물을 준다.
- Matching order = `priority` ascending, ties by `id` ascending. `priority` is 0 for `scope='category'` and 1 for `scope='generic'` — a category-only pattern hides a generic of the same name.
- `retrieval-topic` 은 검색 유닛의 **주제 사전**이다(포크 #8, 적재 원본 `analysis/retrieval/dict/topics_v1.csv`). 한 행 = 한 주제의 한 별칭이고 `pattern` 은 정규식이 아니라 **표기 그대로**다 — 한글은 부분문자열, 라틴은 경계 매칭(`(?<![A-Za-z])…`)이라 매칭 방식이 계열마다 다르고, 그 별칭이 Kiwi 사용자 단어이자 확장 목록이라 정규식으로는 쓸 수 없다. `extra` 가 나머지를 나른다: `term_kind ∈ {ko, latin, mfds_inci}`(`|` 로 겹칠 수 있다 — 아보벤존은 ko 이자 식약처 표기다) · `topic_type` · `trend_use` · `note`. 뒤 셋은 주제 단위 사실이라 그 주제의 아무 행에 한 번만 적고(관례: 첫 행), 두 행이 다른 값을 말하면 적재가 아니라 `analysis/retrieval/topics.py` 가 거절한다.
- Neutral-noun twins carry the same `aspect` name (the source dictionary's `~` suffix is not kept in the CSV). A twin follows in the source order = the same priority, a larger id.
### Topic lexicon v3 — the judgment ledger for ydc's aliases and the dictionary candidates (fork #56)
`lexicon.json` 의 별칭 9 중 자리가 있던 셋(`썬크림`·`자외선차단제`·`코스알엑스`)은 이미 사전에 있었고, `선크림추천` 은 `bm25.expand` 의 부분문자열 확장이 잡아 행이 필요 없다(포크 #37 1c). 남은 5종과 `protected` 32 가 남긴 후보 7종을 여기서 판정한다. **원장은 `tool/measure-lexicon-candidates` 의 `LEDGER` 이고**(판정·자리·근거·df·new 가 한 자리에 있다), 그 도구가 261,317 문서(`archive/yt-handoff/document.csv`, 읽기 전용) 위에서 그 수를 다시 재 원장과 **정확히 맞대는** 길이다(어긋나면 종료 코드 1). 주제 단위 수(아래 12,197 → 12,418 · 959 → 2,021)는 같은 도구의 `--topics` 가 낸다. `tests/retrieval/test_lexicon_v3.py` 가 원장과 적재 원본을 맞댄다.
- **The counting rule is the dictionary's matching rule** — `ko` is a substring (case ignored), `latin` is a boundary match. Mix them and the numbers part: `sunscreen` is **81** as a substring and **76** on a boundary, and the five of difference are the plural `sunscreens`. The dictionary matches on a boundary, so the ledger's value is 76 (the place where #56 corrected the 81 in the issue body).
- **df 하나로는 등재를 못 정한다.** 이미 있는 별칭이 그 문서를 전부 보고 있으면 새 행은 아무것도 관측하지 않는다. 그래서 원장은 df 옆에 `new` 를 함께 든다 — `톤업크림` 628 편은 `톤업` 이 전부 보므로 `new` 가 0 이다. **`new` 의 기준 사전은 원장의 표기를 *전부* 뺀 사전이다**: 한 행씩 빼며 잰 값이 아니므로 같은 주제에 여럿이 붙으면 `new` 의 합이 그 주제의 실제 델타보다 크다(선크림 다섯의 합 224 vs 실제 +221 — 겹쳐 나오는 문서를 각각 세기 때문이다).
- **Listing has to clear all four.**
  1. **df ≥ `analysis/retrieval/terms.MIN_DOCS`(5)** — not a floor this issue invented but the floor the uncaptured-expression table already uses.
  2. **그 행이 없으면 관측되지 않는 것이 있되, 있던 것을 잃지 않는다** — 매칭이 넓어지거나(`new` > 0) 토큰이 달라지고, **기존 토큰을 잃지 않는다.** 별칭은 Kiwi 사용자 단어가 되어 복합어를 한 덩어리로 묶으므로 조각 토큰이 사라질 수 있다: `속건조` 는 `속건조` 를 얻고 확장이 `건조` 를 지켜 통과하지만, `톤업크림` 은 `크림` 을, `비비크림` 은 `비비`·`크림` **둘 다** 잃어 걸린다.
  3. **그 주제의 뜻을 바꾸지 않는다** — 별칭은 같은 축의 다른 표기여야 한다. 축이 같은지는 뜻으로 가르고 실측이 그 판단을 되묻는다: **그 주제의 등장 문서를 50% 이상 늘리는 말은 별칭이 아니라 그 주제가 지금까지 안 세던 것**이다. 지금 이 문턱은 데이터가 한 점뿐이지만 판정이 그 값에 둔감하다 — 통과한 최대가 `파데프리` +14.6% 이고 걸린 최소가 `화잘먹` +110.7% 라 사이가 비어 있다.
  4. **그 주제 유형의 축이어야 한다** — 사전에서 제품 범주는 `topic_type='product_category'`(`선크림`, `trend_use=false`) 자리이지 `attribute` 주제가 아니다. `비비크림` 은 1~3 을 다 넘고 여기서 걸린다.
- v3 가 더한 것은 일곱이다: `선크림` 에 `썬쿠션`·`썬스틱`·`선에센스`·`선스프레이`(ko)와 `sunscreen`(latin) · `촉촉함_건조함` 에 `속건조` · `톤업_메이크업베이스` 에 `파데프리`. **`속건조` 는 매칭이 아니라 토큰으로 자리를 얻었다**(`new` 0): `건조` 가 2,217 문서를 이미 보지만 Kiwi 가 `속`+`건조` 로 쪼개 그 말을 정확히 찾을 수 없었다.
- **행이 되지 않은 여덟.** `올영`(5,583) — 자리는 `entity_lexicon`(kind=brand)이지만 정본 `올리브영` 이 유통 채널이라 `tier='stop'` 이고, `analysis/lexicon.compile_lexicon` 이 stop 정본의 표면을 `surface_re` 에서 통째로 빼므로 행을 더해도 링커·추출기의 산출이 한 비트도 안 바뀐다. 게다가 brand 는 `db/seed/lexicon.py` 가 `LEXICON_VERSION`(1) 으로 적재하는 유일한 길이고 `activate` 는 그 kind 를 통째로 갈아끼우므로, 행 하나를 더하려면 950표기짜리 v2 를 통째로 세워야 한다 — 자리를 만드는 것이 먼저다. `sunstick`(3) — 기준 1 아래이고 그 3편을 이미 `선크림` 이 본다. `톤업크림`(628 · new 0) — 기준 2. `화잘먹`(1,154 · new 1,062) — 기준 3: `밀림_들뜸` 의 별칭 넷이 전부 결함어인데 반대 방향의 결과어라 959 → 2,021(+110.7%)이 된다. `비비크림`(698 · new 581) — 기준 4. `모공막힘`(5 · new 5) — **보류**: 네 기준을 다 넘지만 표본이 바닥과 같아 축(`자극_눈시림` 의 `트러블` 계열인지)을 가를 수 없다. `케미컬`(3)·`olive영`(0) — 기준 1, #37 판정 유지.
- **선크림 주제가 넓어진다.** v3 의 다섯 표기가 그 주제에 붙어 활성 사전으로 세는 문서가 12,197 → 12,418(**+221**)이 된다. 이 수는 `match_topics` 를 직접 부르는 자리(`retrieval eval` 의 정답 · `terms` · `crosscheck`)에만 걸린다 — 분기 지표·근거·민감도는 `corpus_mention`(2026-08-19 관측, ydc 의 매칭)을 읽으므로 사전 버전이 그 표들을 움직이지 않는다.


### Query stopwords — the entity lexicon's `kind='stopword'` (fork #46)
- One row = one surface form to erase from a query. `canonical` is not the canonical surface but **the axis that surface is caught on** — the only value today is `query`, and the judgment that no stopword goes on the index and extraction axis (`entrypoints.md` §Search, fork #8·#37) still stands as it was. The axis is not split by `kind` because `activate` works per kind and every added axis would make a new version axis, and it is not split by `tier` because that slot already holds a brand-only vocabulary.
- **The second axis has no place today.** The unique key is `UNIQUE (kind, surface, version)` (`001_needs.sql:50`) and does not carry `canonical`, so if another axis holds the same `surface`, `db/lexicon.py`'s `ON CONFLICT … DO NOTHING` **drops it quietly** without an error. So the paragraph above promises no room to add an axis — adding one starts with additive DDL that widens the unique key (grade B review M3, 2026-08-26).
- `surface` 는 정규식도 원형도 아니라 **`bm25.tokenize` 가 실제로 내놓는 토큰**이다 — 필터가 토큰 목록 위에서 돌기 때문이다(`관해서` → `관하`, `어떻게` → `어떻`). 그래서 목록을 고치는 사람은 표기가 아니라 토큰을 적어야 하고, `tests/retrieval/test_query_stopwords.py` 가 그 파일의 프로브 질의 다섯 개에서 각 행의 토큰이 나오는지를 되묻는다(코퍼스 전수가 아니라 손으로 고른 시험 벡터다) — 나오지 않는 행은 지우지 않고 `note` 가 그 사실을 적는다(판단은 같고 도달만 못 하므로, 형태소 분석기가 바뀌면 살아난다).
- The loaded source is `analysis/retrieval/dict/query_stopwords_v1.csv` (13 rows) and the only path is `cosmai lexicon load/diff/activate --kind stopword`. The **active version** turns separately from aspect (`entity_lexicon`'s `activate` is `WHERE kind = %s`).
- The version **number**, though, is global to `entity_lexicon` — `_label` in `analysis/lexicon.py` and `analysis/aggregate/pipeline.py:149` read `max(version)` without looking at the kind, so raising this list to v2 makes a run's `versions.lexicon` 2 even while `brand` stays at v1. `:149` does not even look at `active`, so **a `load` without an `activate`** is enough. Both kinds are at v1 today, so it is harmless, and fixing it is **fork #58**'s job — this list is the first user to step on that pre-existing property. **The v3 topic dictionary (fork #56) does not step on it**: `aspect_lexicon` is a different table and does not share `max(version)`, and `versions.lexicon` reads `entity_lexicon` alone (`aggregate/pipeline.py:149`). Raising aspect to v3 leaves that column at 1 — what moves is `versions.lexicon.aspect` (`analysis/pipeline.py:139`) alone, and that side carries one entry per ruleset.

## need_key registry CSV (→ `needs.need_key`, A17)
`need_key,canonical,note` — the union of the two slices' vocabularies. `canonical` is the representative of a synonym group, and where there is none it is itself.
v1 의 동의어 5쌍(suncare 이름 → p1 이름): `밀림→밀림들뜸` · `향→향냄새` · `발림텍스처→제형발림` · `지속력워터→지속력` · `톤업색상→색상발색`. `site_axis_map.need_key` 가 p1 어휘라서 대표를 그쪽으로 맞춘다. `scope='all'` 롤업은 `canonical` 기준으로 합산한다.

## Category map CSV (→ `needs.category_map`, A18)
`site,source_category,lexicon_category,method,priority`
- `method='rank_snapshot'`: `source_category` is the leaf of the site's category (the last piece split on ` > `). `site='*'` applies to every site.
- `method='name_keyword'`: `source_category` 는 **제품명 정규식**이다 — 랭킹 스냅샷이 없는 제품(글로우픽)의 폴백. 정규식은 서로 겹치므로(`선크림|…` 과 `크림` 이 "선크림"에 둘 다 맞는다) **`priority` 오름차순으로 먼저 맞는 것**을 쓴다. 동률의 순서는 정의하지 않는다 — 동률을 만들지 마라. v1 은 CSV 행 번호(1부터)를 그대로 쓴다.
- Derivation order: the site category leaf → failing that `name_keyword` → failing that, no category. A leaf absent from the table becomes the `lexicon_category` as it stands (identity).

## Category notation (A21, #123)
`category` 의 정본은 하나뿐이다: **사이트가 발행한 카테고리 경로를 자르지 않은 문자열**. 아래 세 자리가
그 같은 문자열을 쓴다 — 한 자리가 leaf 로 자르면(`'01 > 선케어 > 선블록'` → `'선블록'`) 두 값은 절대
같아지지 않고 카테고리 scope 는 분모를 하나도 받지 못한다(운영 실측 run 24: 카테고리 scope 22개에서
`population_share_pct`·`low_share`·`denom_low`·`denom_site` 전부 NULL).

| place | value |
|---|---|
| `needs.need_mention.category` | `trend_radar.rank_snapshot.category_name` verbatim (`analysis/units.py:review_unit`) |
| `needs.product_denominator.category` | the same string (`analysis/aggregate/ranking.py:denominators`) |
| `needs.metrics_need.scope` | the same string — the `scope='all'` rollup is the only exception (`analysis/aggregate/pipeline.py:scopes_for`) |

- 사이트마다 깊이가 다르다: oliveyoung 은 `'01 > 선케어 > 선블록'`, glowpick 은 `'크림'`, daisomall 은
  `'뷰티/위생'` 하나뿐이다. 얕은 값도 그 사이트가 발행한 **경로 전체**이므로 이미 정본이다 — 정본은
  "계층형으로 만들어라"가 아니라 "원문을 자르지 마라"다.
- When the site says no category it is NULL. It is not filled in with a dictionary label — that is
  `lexicon_category`'s place (B10) and the two columns mean different things. The product-name regex
  fallback (`category_map.method='name_keyword'`) produces `lexicon_category` alone.
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

## Panel roster CSV (→ `needs.panel_channel`, fork #3)
One row = one channel. Every ydc ratio uses this roster as its denominator (seed original `eval/panel/channels_v1.csv`, **43 channels**). The file has 11 columns and only six go into the table — the other five are not loaded. Without what was dropped written down here, #31's loader and this spec part company quietly.

| CSV column | → `needs.panel_channel` |
|---|---|
| `channel_id` | `channel_id` |
| `handle` | `handle` |
| `channel_title` | `channel_title` |
| `panel_role` | `panel_role` (the two values below) |
| `role_basis` | `role_basis` |
| `source_list` | `source_list` |
| `team_rank` | — the team's internal ranking. The ground for the role verdict is carried by `role_basis` alone |
| `team_role` | — the same as above |
| `channel_published_at` | — a channel fact the source (`tubedepth`) already holds, so the roster does not carry it twice |
| `video_count_at_seed` | — the same as above (a snapshot as of the seed) |
| `subscriber_count_at_seed` | — the same as above |

| `panel_role` | meaning | v1 panel |
|---|---|---|
| `product` | a channel that covers products. ydc's quarterly metrics stand on this population | 34 |
| `expert` | expert channels: dermatologists, pharmacists and so on | 9 |

- **The role lives in the `needs` derivation, not in the source** (user decision 2026-08-26). The source (`tubedepth`) channel table is an upstream contract that `tool/checks/ddl-drift` guards, so it is not a place for the fork to add a column. The 43 channels are a fixed list, so one seed is enough, and when the panel composition changes the seed is loaded again.
- **The price is taken knowingly: a new channel entering collection gets no role automatically.** A channel not in the roster is outside the panel, so keeping it out of the denominator is right; all that is needed is for the fact to be readable off the row — `metrics_topic_quarter`'s `panel_version`·`panel_role`·`denom_channels` are that place. There is no slot for a 'role-like value' to arrive in (the DDL's CHECK, 022).
- The version has the same shape as a dictionary's: a `version` is assigned at load and swapped by `active`. An aggregate row points at the roster it used through `panel_version`, so after the panel changes it is still recorded what an old row took as its denominator. That version lives in the one-row parent `needs.panel_roster(version)`, and roster rows and aggregate rows point at that row **both by FK** — `needs_runtime` holds DELETE on both tables, so without the parent that sentence would go false once a roster version was deleted.
- Loading is `db/seed/panel.py` (`python -m db.seed --only panel`) and nothing else — not a new CLI but the same place as the other seeds (fork #31). The original lived in a slice and #9 deleted that directory, so it moved to `eval/`. Moving it **removed the UTF-8 BOM**: `db/seed/_common.read_csv` opens as utf-8, and a surviving BOM makes the first column name something other than `channel_id`; the 11 column names are otherwise unchanged.
- **There is always exactly one active version.** `active` is per row, so a partial index cannot stop two versions being on at once, and then a denominator that goes through `WHERE active` becomes 86 instead of 43. A partial unique index cannot express "the active rows have one distinct version", so the loader carries this invariant — a one-statement `SET active = (version = n)` (`db/seed/panel.activate`) and `panel.active_version`, which stops instead of answering when there are two (fork #3 review L6 · #31).

## Corpus snapshot (→ `needs.corpus_snapshot` / `corpus_document` / `corpus_mention`, fork #4)
One row = one document. Videos and comments live in the same table (`content_type` tells them apart).
The originals are the three ydc handover CSVs (`archive/yt-handoff/`, document 261,317 · mention
105,358 · channel 43 rows), and **that place is read-only** (`STATE.md` §3), so the loader takes the
path as an argument (`python -m db.corpus load <dir>`). A slice held a copy of the manifest too
(`analysis/slices/ydc/common/manifest.json`) but it was the same JSON up to line breaks and was
discarded — what the loader reads is always the one in the directory it was given (fork #37).

**These rows are an observation of 2026-08-19, not "YouTube now".** They cannot be remade by
re-collecting — comments keep piling up and view and like counts are values as of `collected_at`. So
the observation version (`snapshot_id`) stands **at the front** of the unique key (`corpus_document`
PK = `(snapshot_id, source, source_item_id)`) and a re-collection (#38) arrives under another version
and stands beside the old rows. What keeps them from being overwritten is a property of the key, not
loader discipline. Which version the analysis reads is the one `corpus_snapshot.active` column, and
with one row per version that invariant is carried by a partial unique index (023) — where this parts
from `panel_channel`, which left the same sentence to the loader.

`channel.csv` **does not become a table.** A channel's role is the value that fixes a denominator, so it has
to live in one table alone (`panel_channel` of §Panel roster CSV); living in two tables makes two denominators, and the later one quietly parts from the earlier.
The import **compares** instead: every channel the corpus mentions has to be in the active roster
under the same role, or it is refused (`db/corpus.check_channels`). That file's
`uploads_playlist_id` does not become a table column either — all 43 rows are
`'UU' || substr(channel_id, 3)`, a derivation rather than a value.

### The rules the manifest nailed down (`manifest.rules`, verbatim)
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

Rules 3 and 4 are not sentences this file says first: a comment's quarter attribution and the long-form-only
denominator are already carried by the quarterly document population bullet of `interfaces.md` §Formulas, and
what is here is the comparison saying that sentence **is the same as this corpus's original rule**. These 11 lines
stand as constants in `db/corpus/contract.py`, and the loader refuses the import when the manifest it read in
differs from them — a corpus made under different rules mixed into the same table changes every ratio of that
table with no error.

### The row counts the manifest declared (`manifest.table_counts` · `documents_by_content_type`)
적재기는 **선언한 행수와 반입분을 대조한다**(`db/corpus.load` → `contract.check_counts`) — 다르면 켜기
전에 거절한다. 규칙·한계와 달리 이 값은 판본마다 달라서 계약이 수를 지지 않고 대조한다는 규칙만
진다. 잘려 들어온 CSV 는 행이 **적을 뿐** 오류가 없고, 그러면 이 스냅샷의 모든 비율이 조용히 달라진다
— `reproduces` 는 선크림 한 주제만 다시 세므로 그 바깥이 잘린 것을 잡지 못한다. 2026-08-19 판본의 값은
document 261,317 · mention 105,358 · channel 43 이고 문서 구성은 `video_long` 7,085 · `video_short`
6,888 · `video_unknown` 6 · `comment` 247,338 이다.

When the comparison disagrees, **the rows stay.** The composition only shows after all 260k document
rows have been read, so the refusal comes after the import -- but those rows sit under their own
`snapshot_id` and are **never switched on**, so the analysis does not read them. The way out is not to
delete them but to fix the source and call it again **under the same `snapshot_id`** (every INSERT is
`ON CONFLICT DO NOTHING`, so only the gaps are filled). `DROP` needs approval every time and is not
used as an exit.

`input_counts` (input videos 13,979 · comments 247,338 · duplicate documents 0 · orphan comments 0 ·
duplicate comment bodies 237) is mostly **held over** (fork #37): ydc `to_common_schema.py` counted those
values **before** the conversion, so they cannot be counted again over the imported rows. Two slots do stand
here — the sum of the input row counts (13,979 + 247,338) is the document row count above, and
`duplicate_docs = 0` is proved on a new version by **whether the rows read and the rows entered are equal**
(`db/corpus.load` → `contract.check_unique`; `ON CONFLICT DO NOTHING` drops duplicates quietly, so without
counting them a truncated file cannot be told apart). The rest stays held over, and among it
**`orphan_comments` is not carried by the DB** — a comment's parent is `corpus_document.parent_item_id` (023)
and there is no FK there (a partial index only). What `corpus_mention`'s FK carries is an orphan **mention**,
not an orphan **comment**.

### What text means (`manifest.text_rule`, verbatim)
> 영상 text = 정규화(제목 + 공백 + 설명). 댓글 text = 정규화(본문). 정규화는 HTML 엔티티 해제 → NFKC → 제어문자 제거 → 공백 축약이며 trend.py 의 normalize_text 를 그대로 쓴다. 태그는 text 에 넣지 않고 source_metadata.tags 로 보낸다. 자막·음성은 PoC 제외.

- **Against cosmai's normalisation**: `normalize_text` in `analysis/retrieval/normalize.py` runs the
  same four steps **to a fixed point**, while ydc `trend.py`'s runs them once (they part on a double
  escape, `&amp;lt;`). The two implementations differ, so `text` could mean two things here, but on
  this corpus they do not part — measured 2026-08-26, all 261,317 rows are already at a fixed point
  after one pass (0 rows differ), because the collector receives `textFormat=plainText` and no HTML
  comes in to begin with. A re-collection is not guaranteed this property.
- The eight limitation sentences are carried by `interfaces.md` §Limitations of the population — they are not
  a format but **how to read the numbers**, so they belong beside the formulas.

## MFDS registration ledger CSV (→ `needs.mfds_registration`, fork #55)
- Source `eval/mfds/mfds_items_v1.csv`, copied verbatim from ydc `rag/mfds_items.csv` at tag v0.4.0 (`76db718`).
  Four columns map one to one: `COSMETIC_REPORT_SEQ` → `report_seq`, `ITEM_NAME` → `item_name`,
  `ENTP_NAME` → `entp_name`, `report_date` → `report_date` (the source carries a `00:00:00` time and no filing
  has an hour, so it is stored as a date). The file is 4,736 lines and **4,735 records** — the header is the
  other line. Nothing is dropped and nothing is re-derived: this is the official filing record, which is why
  `cosmai#73`'s "the external ydc CSV is not imported" does not reach it.
- **The key that joins is the company, not the product name.** `entp_key` = `db/seed/mfds.py`
  `normalize_company(entp_name)`: NFKC → the corporate form removed (the Korean company-form word and its
  two parenthesised abbreviations, `Co.,Ltd` · `Corp` · `Inc` — the vocabulary lives in that function; the
  latin forms are word-bounded, the Korean ones are written glued to the name) → lower-cased → everything
  that is not a Hangul syllable, a latin letter or a digit dropped. It is compared against
  `needs.entity_lexicon.surface WHERE kind='brand'` put through the same function — **the lexicon side
  folds too** (118 of 950 active surfaces move under the fold; no SQL in the repository folds a surface, the
  fold is the Python function on both sides, as `tool/measure-mfds-join` does). Measured on production
  2026-09-04 (read-only): **233 of the 4,735 filings join, on 40 brands; those brands covered 411
  `trend_radar.product` rows and 29 of 205 `needs.product_ref` rows when measured.** The commerce side grows
  with every collection — `uv run tool/measure-mfds-join` is the live count, not this sentence. Rejected in
  the same measurement:
  folded `item_name` = `trend_radar.product.name` → **0** products (a registered name is a legal name, a
  listing name is marketing copy); `item_name` through the linker's `normalize_name` → 14 filings;
  `item_name` contained in a product name → 92 filings, but the pairs are coincidences; `entp_name` with
  the corporate form left in → 1 filing (4,332 companies carry one, no brand surface does). Re-measure with
  `tool/measure-mfds-join`; the numbers move as the lexicon grows.
- **Update path: not updated.** Snapshot of ydc v0.4.0, newest `report_date` **2026-08-20** (oldest
  2008-10-30). `needs.mfds_snapshot` carries `source_tag`, `source_file`, `source_rows`, `max_report_date`,
  `update_policy = 'not_updated'` and `loaded_at`, so staleness is readable off the database rather than off
  a document. The alternative is a collector against the MFDS open API — a new collector, a new key in
  `secrets.md` and a new pipeline stage; out of #55's scope and not worth it while the ledger cross-checks
  brands rather than reporting counts. A refresh is a second `mfds_snapshot` row: `report_seq` is the primary
  key and the loader is `ON CONFLICT DO NOTHING`, so a filing already present keeps the values of the load
  that first carried it (`snapshot_id` says which) — a filing that changed under the same sequence is
  neither re-entered nor updated; it stays as first loaded. That rests on a written assumption: MFDS does
  not re-file under a used report number. The loader refuses a silent merge: after the snapshot row's
  `DO NOTHING` it reads the row back and raises when the file's `source_rows`, `max_report_date` or
  `source_file` differ from what is stored, so a refresh is a reviewed bump of the snapshot id in code,
  never a rerun over a grown file. `entp_key` is loader-filled (`CHECK (entp_key <> '')`, a company that
  folds to nothing is refused before any DB contact); changing `normalize_company` therefore needs
  `db/seed/mfds.py` `rekey()` over the stored rows — a rerun of the load cannot repair a key, which is why
  028 grants `needs_runtime` UPDATE as well. `update_policy` has no CHECK — this vocabulary is this
  section's to grow and the DDL is additive only.

## The ref grammar of a mention row (A20)
| src | `ref` | note |
|---|---|---|
| review | `product_key/review_key` | |
| yt_comment | `video_id/comment_id` | **both** `need_mention` and `wish_mention` use this grammar. One comment carries the same key in two tables |
| yt_transcript | `video_id` | |
| yt_title | `video_id` | `TextUnit` only — it never enters `need_mention` |
| naver_blog | `post_id` | **reserved (unimplemented, #96)** — the source table `needs.naver_blog_post` (004_naver.sql, #9, T15) exists, but the live transport (`collectors/naver/cli.py:_RaisingFetcher`, #95) and the analysis branch (`analysis/polarity/pipeline.py`) do not, so no `need_mention` row of this src exists yet |
- The CHECK on `needs.need_mention.src` (001_needs.sql) already accepts `naver_blog` as a value — the DDL is additive only, so booking it ahead does no harm (#96).
- `brand_mention` uses `ref_id` and a different src vocabulary: `yt_title→title` · `yt_transcript→transcript` · `yt_comment→comment` (B12).
- `labeled_set.ref` is a separate namespace (`sun:<split>:<i>:<review_ref>` · `p1:<split>:<i>` · wish uses `comment_id` alone · `<sample>:<src>/<ref_id>/<brand>` · `<v1|v2>:<i>`). Joining it to a mention row needs a conversion.

## NAVER DataLab: a ratio is comparable only inside one request (#44)
NAVER DataLab scales the maximum of its series to 100 **within one request** and returns the rest as
a ratio of it (vendor documentation). So `needs.naver_datalab_point.ratio` can be compared in size
**only among rows from the same request** — comparing it with a row from another request (another
`category`, or the same category on another run) treats numbers normalised against two different
100s as if they were one scale, and gives **a plausible wrong number with no error**. Ranking by
GROUP BY on `category`·`group_key` is safe, but before comparing or summing rows with different
`request_key` values an **anchor rescale** must come first (put a global anchor keyword into several
requests and return everything to that ratio — issue #90 decides the anchor and when to rescale).
This contract is the constraint "do not compare across requests without a rescale"; #90 sets out how
to rescale.

**The request boundary is read off the row as `naver_datalab_point.request_key`**
(`contracts/ddl/needs/006_naver_request.sql`, decision (a): `terms` only audits one group's search
terms and carries no `startDate`/`endDate`/`timeUnit`, so it cannot tell one run of the same group
from another). `request_key` = the sha256 hex digest of the canonical JSON
(`json.dumps(sort_keys=True)`) of the request body actually sent
(`keywordGroups`·`startDate`·`endDate`·`timeUnit`) (`collectors/naver/parsing.py:datalab_request_key`)
— the same parameters give the same key, and moving `endDate` by even a day (that window is rescaled)
gives a different one. Every row one response made shares one `request_key`.

## Lists that go into a scalar column (A12)
`wish_mention.format` · `wish_mention.attribute` are `;`-separated, **at most 3**, and **the first is the main value**. Aggregation uses the first alone.
`product_line_mention.line_key` = `brand || ' ' || line_tokens` (A14).

## No aspect (B8)
`need_mention.need_key` is NOT NULL and part of the UNIQUE. A row whose aspect could not be decided (the rules' `neg-only`/`pos-only`/`no-aspect`, the LLM's `aspect=null`) is stored under the **`need_key=''` sentinel**. `metrics_need` aggregation **excludes** `need_key=''`.

## Sample constants (T5)
`low_complete = (low_collected < 150) or has_3star`. 150 is the RATING_ASC collection sample ceiling (`REVIEW_PAGES 3 x 50`) and `collectors/commerce/scope.json` (#7) holds the same value.

## Evaluation set CSV (→ `needs.labeled_set`)
`task,ref,split,gold,text,labeler,labeled_at,extra(json)`
- Held today (originals under eval/): polarity sun 200 tune + 100 holdout, polarity P1 60 tune + 40 holdout, wish_class 100 tune + 60 holdout + blind60_v2 60 holdout, brand_link 120, product_match 80 pairs.
- 라벨 기준(polarity): 작성자가 이 제품에서 겪은 부정 경험이 있으면 불만(약해도), "X 없음/적음"류 만족 표현은 만족, 타제품·취향·피부타입 서술·잘린 문장·배송은 중립.
- Labelling criteria (wish): a = a product, launch or reissue request aimed at a brand, b = a content request aimed at a creator, c = a general wish.

## Time
- The grain of a mention row = the month (`'YYYY-MM'`). Every mention row carries an `observed_at_resolution`.
- Aggregation has **two** grains -- the month and the quarter (`'YYYYQn'`; `2026-07` -> `2026Q3`). The quarter is an addition, not a replacement: the month is the grain `need_mention` and every `metrics_*` stand on, so adding a grain column to an existing table would change what its existing rows mean (fork #3, decision 2). The quarter therefore lives in its own table.
- **A quarterly row is not three month rows added together.** The population differs — a month row's denominator is the product and category (`product_denominator`) while a quarterly row's is the panel (§Panel roster CSV). The two tables are not summed, and neither is derived from the other. A quarterly value is the value settled at that moment, so it is not recomputed by a view on every query.
- A quarter's comparison partner is not the adjacent quarter but **the same quarter of the previous year** (YoY; `2026Q3` <-> `2025Q3`). Suncare is a seasonal product -- the share of long-form videos mentioning sunscreen peaked in Q2 and bottomed in Q1/Q4 three years running -- so comparing adjacent quarters reads seasonality as the same trend every year.
- YouTube comment `published_at` comes from restoring a relative time -> `month` only from 2025-09 on, `year` before that.
- Ranking is `rank_daily` (KST day). Glowpick and Hwahae update rarely, so duplicates are removed with `valid_from/valid_to`.
- When the source observation time is NULL (`trend_radar.review.written_at` is nullable while `need_mention.observed_at`/`month` are NOT NULL) it falls back to `observed_at = captured_at` (the date of the collection time) with `observed_at_resolution = 'day'`. The fallback piles reviews into the month they were collected in, so `analyze` counts the rows the fallback applied to and records it in `analysis_run.note` -- the moment this value stops being 0 is the moment to look at the rule again. (Measured 2026-08-23: 0 of `trend_radar.review`'s 19,786 rows.)

### The canonical table per aggregate grain
| grain | canonical table | the row's time slot |
|---|---|---|
| month | `needs.metrics_need` | `month` (`'YYYY-MM'`, `''` = the whole period) |
| month | `needs.metrics_wish` | `first_month`·`last_month`·`months_present` (the key carries no time) |
| quarter | `needs.metrics_topic_quarter` | `quarter` (`'YYYYQn'`) |

Metrics of the same concept (mention counts · channel counts · persistence) end up living in two tables, so **the one place that answers a (grain × concept) value is the single table this table points at** — the month has two canonical tables because there are two concepts (need · wish), and no one table ever carries two grains. A new aggregate table stands by adding a row here — `tests/test_panel_quarter_contract.py` catches a `metrics_*` table with no grain.

**The verdict table (`needs.topic_quarter_judgement`, fork #40) has no line in this table.** That table counts
no document; it takes a row of `metrics_topic_quarter` and emits one row under the same key — it carries none
of mention count, channel count or persistence, so it has no rival to be canonical over. That it is a
derivation rather than an aggregate stands at once in the name (it does not start with `metrics_`) and in the
FK (all eight columns of the metric row's primary key). The grounds are `interfaces.md` §Verdict.

**The evidence table (`needs.topic_quarter_evidence`, fork #6) has no line here either, for the same reason.**
That table does not count but **points** — it only attaches to one verdict cell a few of the corpus documents
that made that cell, in like-count order, so even its time slot (`quarter`) is not its own but that of the
cell it points at. The grounds are `interfaces.md` §Evidence.
