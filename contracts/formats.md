# Formats

## Lexicon CSV (→ loaded into `needs.entity_lexicon` / `needs.aspect_lexicon`)
- entity: `kind,canonical,surface,tier,source,note` — one row = one surface. Latin aliases only where they actually appeared in the corpus (P3: Latin is nearly useless).
- aspect: `aspect,scope,category,pattern,is_neutral_noun,ruleset,priority` — pattern is a Python `re`. A neutral-noun twin (펌프/거품/탈모 and the like) is `is_neutral_noun=true`.
- **Columns of an aspect CSV outside the seven known ones go to `extra`** (jsonb, 021) — each ruleset needs different facts, and putting them on a shared column gives one column a different meaning per ruleset. An empty cell is not a value but an absence, so it does not enter `extra`.
- Versions: assigned at load with `--version n`, swapped with `activate`. Re-loading the same version is a no-op — the one exception is a **single backfill** of rows whose `ruleset`·`priority` are still empty (`ruleset=''`), i.e. v1 rows loaded before 002. Once a value is in, re-loading changes nothing: dictionary content changes by version alone.

### The aspect lexicon's ruleset and order (B4·B5)
- `ruleset ∈ {suncare-v2.2, p1-v2.2, shared, retrieval-topic}` — not tied down by a CHECK in the DDL. The values grow with every dictionary version (`suncare-v2.3` …), so nailing the vocabulary into the DDL would need a migration at every dictionary revision. `shared` = the rows that sit in both dictionaries **with the same pattern** (today the 2 rows `백탁`·`색상어두움` of `선블록`), and UNIQUE stops a duplicate load, so it exists as one row only.
- The loader always reads `WHERE version = <v> AND ruleset IN (<요청 ruleset>, 'shared')`. A condition like `scope='generic' OR category='선블록'` gives a mixture that reproduces no slice at all.
- **What need extraction loads is `p1-v2.2` · `suncare-v2.2` · `shared`, and nothing else** (#126). The table houses one ruleset per consumer, and the need path reaches only the two a category can name (`ruleset_for`, `analysis/polarity/__init__.py`) plus the `shared` the loader adds — which is the whole reason `retrieval-topic` has 0 rows in `need_mention` and `metrics_need` and never made `선크림` stand as a need. Widen the loader and a topic alias becomes a `need_key` with nothing on the row to say so, so `tests/test_lexicon_loader.py` holds the boundary against a topic row planted in the same table.
- Matching order = `priority` ascending, ties by `id` ascending. `priority` is 0 for `scope='category'` and 1 for `scope='generic'` — a category-only pattern hides a generic of the same name.
- `retrieval-topic` is the retrieval unit's **topic dictionary** (fork #8, the loaded source `analysis/retrieval/dict/topics_v1.csv`). One row = one alias of one topic, and `pattern` is not a regular expression but **the surface form as written** — Hangul is a substring and Latin is a boundary match (`(?<![A-Za-z])…`), so the way of matching differs per family, and since that alias is a Kiwi user word and an expansion list at once it cannot be written as a regular expression. `extra` carries the rest: `term_kind ∈ {ko, latin, mfds_inci}` (they can overlap with `|` — 아보벤존 is ko and an MFDS notation at once) · `topic_type` · `trend_use` · `note`. The last three are facts about the topic, so they are written once on any one row of that topic (by convention the first), and when two rows say different values it is `analysis/retrieval/topics.py`, not the load, that refuses.
- Neutral-noun twins carry the same `aspect` name (the source dictionary's `~` suffix is not kept in the CSV). A twin follows in the source order = the same priority, a larger id.
### Topic lexicon v3 — the judgment ledger for ydc's aliases and the dictionary candidates (fork #56)
Of `lexicon.json`'s 9 aliases the three that had a slot (`썬크림`·`자외선차단제`·`코스알엑스`) were already in the dictionary, and `선크림추천` is caught by `bm25.expand`'s substring expansion, so it needs no row (fork #37 1c). The remaining 5, and the 7 candidates `protected` 32 left behind, are judged here. **The ledger is `LEDGER` in `tool/measure-lexicon-candidates`** (the verdict, the slot, the grounds, df and new in one place), and that tool is the way to measure those numbers again over 261,317 documents (`archive/yt-handoff/document.csv`, read-only) and hold them **exactly** against the ledger (exit code 1 when they diverge). The per-topic counts (12,197 → 12,418 · 959 → 2,021 below) come from the same tool's `--topics`. `tests/retrieval/test_lexicon_v3.py` holds the ledger against the loaded source.
- **The counting rule is the dictionary's matching rule** — `ko` is a substring (case ignored), `latin` is a boundary match. Mix them and the numbers part: `sunscreen` is **81** as a substring and **76** on a boundary, and the five of difference are the plural `sunscreens`. The dictionary matches on a boundary, so the ledger's value is 76 (the place where #56 corrected the 81 in the issue body).
- **df alone cannot decide a listing.** If an alias that is already there sees all of those documents, the new row observes nothing. So the ledger carries `new` beside df — `톤업크림`'s 628 items are all seen by `톤업`, so its `new` is 0. **`new`'s reference dictionary is the dictionary with *every* surface form of the ledger taken out**: it is not a value measured by taking out one row at a time, so when several attach to the same topic the sum of `new` is larger than that topic's real delta (선크림's five sum to 224 vs the real +221 — because a document that comes up in more than one is counted in each).
- **Listing has to clear all four.**
  1. **df ≥ `analysis/retrieval/terms.MIN_DOCS`(5)** — not a floor this issue invented but the floor the uncaptured-expression table already uses.
  2. **Without that row something goes unobserved, and nothing that was there is lost** — the matching widens (`new` > 0) or the tokens change, and **no existing token is lost.** An alias becomes a Kiwi user word and binds a compound into one lump, so a fragment token can disappear: `속건조` gains `속건조` and passes because the expansion keeps `건조`, but `톤업크림` loses `크림` and `비비크림` loses **both** `비비` and `크림`, and they are caught.
  3. **It does not change what that topic means** — an alias has to be another surface form on the same axis. Whether the axis is the same is decided by meaning, and the measurement asks that judgment back: **a word that raises that topic's appearing documents by 50% or more is not an alias but something that topic has not been counting until now**. This threshold stands on a single data point today, but the verdict is insensitive to its value — the largest that passed is `파데프리` at +14.6% and the smallest that was caught is `화잘먹` at +110.7%, so the space between them is empty.
  4. **It has to be an axis of that topic type** — in the dictionary a product category is the `topic_type='product_category'` slot (`선크림`, `trend_use=false`), not an `attribute` topic. `비비크림` clears 1 to 3 and is caught here.
- What v3 added is seven: `썬쿠션`·`썬스틱`·`선에센스`·`선스프레이` (ko) and `sunscreen` (latin) on `선크림` · `속건조` on `촉촉함_건조함` · `파데프리` on `톤업_메이크업베이스`. **`속건조` earned its slot as a token, not as a match** (`new` 0): `건조` already sees 2,217 documents, but Kiwi split it into `속`+`건조` and that word could not be found exactly.
- **The eight that did not become rows.** `올영` (5,583) — its slot is `entity_lexicon` (kind=brand), but the canonical `올리브영` is a retail channel and therefore `tier='stop'`, and `analysis/lexicon.compile_lexicon` takes the surfaces of a stop canonical out of `surface_re` altogether, so adding the row would not move the linker's or the extractor's output by one bit. On top of that, brand is loaded only by `db/seed/lexicon.py` at `LEXICON_VERSION` (1) and `activate` swaps that kind out whole, so adding one row means standing up a whole v2 of 950 surface forms — making the slot comes first. `sunstick` (3) — below criterion 1, and those 3 items are already seen by `선크림`. `톤업크림` (628 · new 0) — criterion 2. `화잘먹` (1,154 · new 1,062) — criterion 3: `밀림_들뜸`'s four aliases are all defect words, while this is a result word in the opposite direction, so it becomes 959 → 2,021 (+110.7%). `비비크림` (698 · new 581) — criterion 4. `모공막힘` (5 · new 5) — **held**: it clears all four criteria, but the sample is level with the floor, so the axis (whether it belongs to `자극_눈시림`'s `트러블` family) cannot be decided. `케미컬` (3)·`olive영` (0) — criterion 1, the #37 verdict stands.
- **The 선크림 topic widens.** v3's five surface forms attach to that topic, so the documents counted with the active dictionary become 12,197 → 12,418 (**+221**). This number reaches only the places that call `match_topics` directly (`retrieval eval`'s answer key · `terms` · `crosscheck`) — the quarterly metrics, the evidence and the sensitivity read `corpus_mention` (observed 2026-08-19, ydc's matching), so a dictionary version does not move those tables.


### The wish axes — the entity lexicon's `kind='format'`·`'attribute'` (#124)
- `wish_mention.format`·`attribute` are filled by `_listed()` from `Lexicon.format_patterns`·`attribute_patterns`, one pattern per **canonical**, at most three per sentence (`LIST_MAX`, A12). Both kinds were 0 rows until #124, so both columns were empty for every `rule-v2.3` row while the same extractor filled 5,905 / 3,253 of the 18,489 `slice-p9` rows. The dictionary is what changed, so the rules carry a new version: the run that fills the two columns is **`rule-v2.4`**, and `extractor_version` is what tells the two populations apart (`versioning.md`).
- The canonical is **the slice-p9 label as it stands** — 36 format, 23 attribute. The issue's 389 / 169 were the `;`-joined combinations, not the vocabulary; unnested there is no tail to cut, so the seed carries all of both.
- **Neither label is a surface form, and the parenthetical is not a surface list.** `제형(스틱/쿠션/젤/밤/스프레이)` never appears in a sentence and `기능(지속/커버/보습/미백 등)` ends in `등`, so the parenthetical is an open list of examples. The surfaces were **mined from the labelled sentences** — every character n-gram (n=2..8) of a label's own rows with df ≥ 3 whose label precision is ≥ 0.90, particle tails stripped, deduped by substring — and then read by hand, because the mining also scores a prior run's collisions. The loaded source is `eval/lexicon/format_lexicon_v1.csv` (271 rows) and `eval/lexicon/attribute_lexicon_v1.csv` (165), one file per kind: `cosmai lexicon load --kind` refuses a mixed file and `activate` turns one kind at a time.
- **Order is load-bearing twice.** `_listed` keeps the first three canonicals that answer, in row order, and `ON CONFLICT (kind, surface, version) DO NOTHING` gives a repeated surface to whichever canonical claims it first (the unique key carries no canonical — the paragraph above this one). So both files are written in slice-p9 volume order, and where slice-p9 gave one surface to two canonicals (`선세럼`, `비비크림`, `바디미스트`, `스킨로션`) the higher-volume one keeps it.
- **The two kinds match by different boundary rules** (`WORD_BOUNDED_KINDS`, `analysis/lexicon.py`). A **format** surface names the product the sentence is about, so it carries the same left boundary a brand surface does and a compound is a row of its own; unbounded, `크림` answers all 500 `선크림` sentences and that one canonical falls to precision 0.51. An **attribute** surface is a property riding on the tail of whatever the request names (`톤업선크림도`), so it matches wherever it sits; bounding it costs recall 0.89 → 0.73 and buys no precision.
- **Measured against the slice-p9 rows, 2026-09-13** (`tool/measure-wish-axis-lexicon`, read-only): format **P 0.956 · R 0.843** (tp 6,236 · fp 287 · fn 1,158), attribute **P 0.926 · R 0.891** (tp 3,331 · fp 267 · fn 409); 5,360 and 3,154 rows filled against slice-p9's 5,905 and 3,253. Per canonical, precision is ≥ 0.95 on **31 of 36** format and **16 of 23** attribute; the other twelve are the per-canonical table the same tool prints, and `--extra`·`--misses` print the sentences behind either side of one. Why a canonical sits where it does differs from one to the next — a substring the boundary rule cannot separate is not the same fact as a value slice-p9 dropped at its own three-value cap — so no sentence here stands in for the table; read it.
- **The panel shows the first value, not the set.** The numbers above are set-level: they score the `;`-joined column `wish_mention` stores, up to three values against slice-p9's list. `metrics_wish` is built with `_first(wish.format)` (`analysis/aggregate/__init__.py`), so a cell carries the first value alone and `LIST_MAX`'s other two never leave `wish_mention` — set-level describes the stored column, first-value describes the panel. On the rows where both sides produced a value (5,235 format · 2,951 attribute), the first value **is** slice-p9's first value **0.892** (format) · **0.929** (attribute) of the time, and lies somewhere in slice-p9's list **0.990** · **0.989** of the time. `tool/measure-wish-axis-lexicon` prints both lines per axis.
- **slice-p9 is a prior rule run, not a gold standard** — it matched surfaces as plain substrings, so a disagreement is an answer to read. Its `용기/패키지` counts 유튜브 as a tube (55 of 61), its `네일` counts 썸네일 (15 of 32), its `향` counts 영향·방향·취향, its `크림` counts 아이스크림, and its `토너/스킨` counts the brands 에이프릴스킨·스킨수티컬즈. None of those are seeded, which is most of the recall gap on those five; the rest is arbitrary compounds (`물크림`, `흉터크림`, `싱아팩`) no literal surface list can enumerate. `--extra`/`--misses` print the sentences behind either side.

### Query stopwords — the entity lexicon's `kind='stopword'` (fork #46)
- One row = one surface form to erase from a query. `canonical` is not the canonical surface but **the axis that surface is caught on** — the only value today is `query`, and the judgment that no stopword goes on the index and extraction axis (`entrypoints.md` §Search, fork #8·#37) still stands as it was. The axis is not split by `kind` because `activate` works per kind and every added axis would make a new version axis, and it is not split by `tier` because that slot already holds a brand-only vocabulary.
- **The second axis has no place today.** The unique key is `UNIQUE (kind, surface, version)` (`001_needs.sql:50`) and does not carry `canonical`, so if another axis holds the same `surface`, `db/lexicon.py`'s `ON CONFLICT … DO NOTHING` **drops it quietly** without an error. So the paragraph above promises no room to add an axis — adding one starts with additive DDL that widens the unique key (grade B review M3, 2026-08-26).
- `surface` is neither a regular expression nor a base form but **the token `bm25.tokenize` actually produces** — because the filter runs over a token list (`관해서` → `관하`, `어떻게` → `어떻`). So whoever edits the list has to write the token rather than the surface form, and `tests/retrieval/test_query_stopwords.py` asks back whether each row's token comes out of the five probe queries in that file (hand-picked test vectors, not the whole corpus) — a row that does not come out is not deleted, `note` writes that fact down instead (the judgment is the same and only the reach is missing, so it comes alive again when the morphological analyser changes).
- The loaded source is `analysis/retrieval/dict/query_stopwords_v1.csv` (13 rows) and the only path is `cosmai lexicon load/diff/activate --kind stopword`. The **active version** turns separately from aspect (`entity_lexicon`'s `activate` is `WHERE kind = %s`).
- The version **number**, though, is global to `entity_lexicon` — `_label` in `analysis/lexicon.py` and `analysis/aggregate/pipeline.py:149` read `max(version)` without looking at the kind, so raising this list to v2 makes a run's `versions.lexicon` 2 even while `brand` stays at v1. `:149` does not even look at `active`, so **a `load` without an `activate`** is enough. Both kinds are at v1 today, so it is harmless, and fixing it is **fork #58**'s job — this list is the first user to step on that pre-existing property. **The v3 topic dictionary (fork #56) does not step on it**: `aspect_lexicon` is a different table and does not share `max(version)`, and `versions.lexicon` reads `entity_lexicon` alone (`aggregate/pipeline.py:149`). Raising aspect to v3 leaves that column at 1 — what moves is `versions.lexicon.aspect` (`analysis/pipeline.py:139`) alone, and that side carries one entry per ruleset.

## need_key registry CSV (→ `needs.need_key`, A17)
`need_key,canonical,note` — the union of the two slices' vocabularies. `canonical` is the representative of a synonym group, and where there is none it is itself.
v1's 5 synonym pairs (the suncare name → the p1 name): `밀림→밀림들뜸` · `향→향냄새` · `발림텍스처→제형발림` · `지속력워터→지속력` · `톤업색상→색상발색`. `site_axis_map.need_key` is p1 vocabulary, so the representative is aligned to that side. The `scope='all'` rollup sums on `canonical`.

## Category map CSV (→ `needs.category_map`, A18)
`site,source_category,lexicon_category,method,priority`
- `method='rank_snapshot'`: `source_category` is the leaf of the site's category (the last piece split on ` > `). `site='*'` applies to every site.
- `method='name_keyword'`: `source_category` is a **product-name regular expression** — the fallback for a product with no rank snapshot (glowpick). The expressions overlap each other (`선크림|…` and `크림` both match "선크림"), so **the first one to match in ascending `priority`** is used. The order of a tie is not defined — do not make a tie. v1 uses the CSV row number (from 1) as it stands.
- Derivation order: the site category leaf → failing that `name_keyword` → failing that, no category. A leaf absent from the table becomes the `lexicon_category` as it stands (identity).

## Category notation (A21, #123)
`category` has one canonical form only: **the category path the site published, as a string that is not
cut**. The three places below use that same string — if one place cuts to the leaf (`'01 > 선케어 > 선블록'`
→ `'선블록'`) the two values can never become equal and the category scope receives not one denominator
(measured in operations, run 24: on 22 category scopes, `population_share_pct`·`low_share`·`denom_low`·`denom_site` all NULL).

| place | value |
|---|---|
| `needs.need_mention.category` | `trend_radar.rank_snapshot.category_name` verbatim (`analysis/units.py:review_unit`) |
| `needs.product_denominator.category` | the same string (`analysis/aggregate/ranking.py:denominators`) |
| `needs.metrics_need.scope` | the same string — the `scope='all'` rollup is the only exception (`analysis/aggregate/pipeline.py:scopes_for`) |

- The depth differs per site: oliveyoung has `'01 > 선케어 > 선블록'`, glowpick has `'크림'`, and daisomall
  has `'뷰티/위생'` and nothing else. A shallow value is the **whole path** that site published too, so it is
  already canonical — canonical does not say "make it hierarchical" but "do not cut the original".
- When the site says no category it is NULL. It is not filled in with a dictionary label — that is
  `lexicon_category`'s place (B10) and the two columns mean different things. The product-name regex
  fallback (`category_map.method='name_keyword'`) produces `lexicon_category` alone.
- When the short leaf-cut form is needed, cut it at that moment with `analysis/units.py:leaf()`. It is not
  stored — path→leaf is a function but leaf→path is not (`'블러셔'` is glowpick's `'블러셔'` and also
  oliveyoung's `'02 > 베이스 메이크업 > 블러셔'`).

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
The loader **holds the declared row counts against what came in** (`db/corpus.load` →
`contract.check_counts`) — when they differ it refuses before switching on. Unlike the rules and the limits
this value differs per version, so the contract does not carry the numbers, only the rule that it compares
them. A CSV that came in truncated has **merely fewer** rows and no error, and then every ratio of this
snapshot quietly changes — `reproduces` recounts the one topic 선크림 alone, so it cannot catch a truncation
outside that. The 2026-08-19 version's values are document 261,317 · mention 105,358 · channel 43, and the
document composition is `video_long` 7,085 · `video_short` 6,888 · `video_unknown` 6 · `comment` 247,338.

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
- **Where the live lineage implements this rule** (#264): `analysis/retrieval/corpus.py`'s
  `youtube_video_text` — `normalize_text(f"{title} {description}")`, title first, one space, the whole
  string normalised **after** the join, tags excluded — reading `tubedepth.video_snapshots.title` and
  `.description` (DDL `tubedepth/006`). Before that column existed the projection was the title alone,
  which is 45 characters of the archive's 856 and about a quarter of its topic mentions. Rule 11 above
  is manifest text and stays verbatim, but the question it leaves open is answered in practice on both
  sides: the archive's reported numbers were produced with tags **out**, and so is this projection.
- **The entity axis, measured on descriptions** (#264): over the archive's 14,467 raw collected video
  rows (`processed/videos.csv`, before any normalisation), **0 titles** carry an HTML entity — which
  reproduces the 0 of 27,318 live `video_snapshots` titles measured on this side — but **1 description**
  does (three `&amp;`), and **0** of either carries a double escape. So the two normalisers still agree
  on this corpus, by a margin of one round rather than by there being no entities at all. Descriptions
  are the side that has them, which is why the fixed-point loop is what keeps a live corpus from being
  flagged un-normalised by `chunks.check_rows` for as long as its rows exist.
- The eight limitation sentences are carried by `interfaces.md` §Limitations of the population — they are not
  a format but **how to read the numbers**, so they belong beside the formulas.

### Which lineage a snapshot belongs to (fork #94, from decision #93 D0)
A snapshot belongs to one of two lineages and the row says which: `corpus_snapshot.lineage` is `archive` or
`live` (030). **An archive snapshot is read, never recomputed** — its rows and its run's output *are* the
observation, and the analysis reaches them through three fixed views (`needs.archive_metrics_topic_quarter` ·
`needs.archive_topic_quarter_judgement` · `needs.archive_topic_quarter_evidence`), each pinned to the run that
`needs.archive_run` resolves from the archive snapshot's id and `analysis/trend/pipeline.py`'s note grammar —
not to a run id written down anywhere. There is **exactly one** archive, carried by a partial unique index the
way `active` is.

The default is `live`, and the archive is said out loud. That is the opposite of what it looks like it should
be, so the reason is worth keeping: every writer of this table inserts a snapshot naming no lineage, so a
default of `archive` would make the *second* snapshot ever loaded collide with the one-archive index and no
re-collection could land again. Special is never what a row gets by saying nothing. The cost is one production
write to mark snapshot 1, and until it runs the three views return **no** rows — an empty answer, never
another lineage's, which is the failure #93 D0 is about. The same is true whenever the archive's run is not
`ok`: `analysis/trend/pipeline.py` re-opens a run by note rather than inserting a new one, so a re-run against
the archive snapshot empties the surface for its duration rather than answering from a half-finished run.

`corpus_snapshot.instrument` (jsonb) records how the observation was made — `listing_route` · `comment_depth` ·
`refetch_window_days` · `dictionary_version` — which is what makes two snapshots comparable at all. Its keys
carry no CHECK, because the instrument grows with the collector and a vocabulary frozen in DDL costs a
migration per knob.

### The long/short rule (fork #95, from ydc v0.4.0 `76db718`)
`content_type` is derived from the video's duration by one rule shared by the archive path and the live
path: a blank or unparseable `duration_seconds` is `video_unknown`, `seconds <= 60` is `video_short`, and
anything longer is `video_long`. **The comparison is inclusive.** 611 of the archive's 13,979 videos have a
duration of exactly 60, so an exclusive `< 60` moves every one of them out of `video_short`, changes every
`composition`, and fails to reproduce the reported split of `video_long` 7,085 · `video_short` 6,888 ·
`video_unknown` 6 — a split the rule was verified against 13,979/13,979 before it was used. The unparseable
branch is kept although `tubedepth.video_snapshots.duration_seconds` is `integer NULL` and can never take
it: without it `video_unknown` quietly becomes "NULL only", and the 6 archive rows it names are live streams
the agreement excludes from both series.

### The live lineage's documents (fork #95, from #93 D1 · D2)
`project:corpus` projects `tubedepth.video_snapshots` and `tubedepth.comments` for the active panel roster
into `needs.corpus_document` under the live snapshot, `ON CONFLICT DO NOTHING` on
`(snapshot_id, source, source_item_id)`, so `collected_at` is the first observation and never moves; current
view and like counts stay in `tubedepth`, where they are current.

**`source_metadata` is copied, not rebuilt.** The video side is the upstream column read as text and handed
back to jsonb without being parsed into Python values, because re-assembly is the step the archive's
spelling is lost in. The comment side has no column to copy and is built from the archive's seven keys in
the archive's spelling — a Python-repr boolean (`is_reply` is the string `"False"`), jsonb `null` for a
value nobody could tell us (never the string `"None"`), and a key that is always null kept present
(`parent_comment_id`, while the reply relationship lives in the `parent_item_id` column). Each of those
three is a way a rebuilt object diverges with no error raised and a measurement changed.

A video is projected only once its `source_metadata` is present: a listing row has none, and the conflict
clause would make that emptiness permanent. `quality_flags` carries exactly one value, `empty_text` before
`duplicate_in_parent`, because every consumer matches the column exactly.

**The live video `text` is the title alone until the collector persists a description**, recorded on the
snapshot as `instrument.text_parts`. The archive's `text_rule` is normalised title + description, and
measured over the 4,283 archive videos that also exist in `tubedepth`, a title-only text carries **915 of
3,534 topic hits — 25.9%**. The same conflict clause that protects `collected_at` also **freezes that short
text forever**: a row written before the description lands is never revised. So a live snapshot built while
`text_parts` is `["title"]` is disposable by design — recovery is a delete of that snapshot's documents and
a re-run, which is cheap only while `active` is false and no mentions have been written against it.
Upstream shk95-1/cosmai#264 is the fix, and it gates the first live collection rather than following it.

**The live lineage's mentions (fork #96).** `match:topic` writes the live snapshot's `corpus_mention` from
`corpus_document` with `match_topics` on the active `retrieval-topic` dictionary — all 15 topics, `trend_use`
as the dictionary says (manifest rule 7). `span_start` is a **0-based character offset into the stored
`text`** and `matched_term` is the dictionary's own spelling of the term — the archive's convention, verified
on all 105,358 of its mentions — and both are derived from the **same** test that decided the match: the
lower-cased substring for `ko` terms, the compiled boundary pattern for `latin` terms. A span taken by a looser
rule than the match can point inside a word the match rejected. The dictionary's version and fingerprint are
merged into `instrument` (`dictionary_version`, `dictionary_fingerprint`); when either differs, that
snapshot's mentions are deleted under the stage's own run and matched again, never mixed. An archive snapshot
is refused before any delete is issued.

### What a comment row keeps of its author (fork #92, from #91 decision 3)
A comment row stores the author's channel identifier **only** as `source_metadata.author_channel_hash`: the
first 24 characters, lower-case hex, of `sha256("youtube:" + channel_id)`. The function is named once, in
`db/corpus/author.py`, and every loader and analysis stage imports that one — a second implementation that
drifts by one character raises nothing, it matches no creator comment at all, and the evidence stops being
consumer speech while the output stays just as plausible. No display name is stored, under that key or any
other; a name-derived signal, if an axis wants one, is stored as a feature (contains a link, digit-run
pattern) and never as the name.

The prefix is a stored fact rather than a choice: the 247,338 comment documents of the 2026-08-19 handover
carry that form. Measured against production 2026-09-12, over 200,000 of them joined to their parent video's
channel — **2,942 match `sha256("youtube:" + channel_id)[:24]` and 0 match `sha256(channel_id)[:24]`** — so
the bare form is not an alternative spelling of this rule, it is a different rule that matches nothing.

Two sites ask the same question of that shape, and they have to agree:
- `db/corpus` refuses a document carrying an `author` or `author_id` key, or an `author_channel_hash` that is
  a raw `UC…` channel id or is not exactly 24 lower-case hex characters.
- `needs.author_identifier_violation` (`db/views/author_identifier_violation.sql`) asks it of the stored rows
  in `needs.corpus_document` and of `tubedepth.comments` after the cutoff `2026-08-24` — the last pre-rule
  comment row was first seen 2026-08-23T22:55:44Z. **Empty means true.**

The negative check alone is not enough, and this is the part worth keeping: `^UC[0-9A-Za-z_-]{22}$` catches an
identifier pasted in whole and nothing else. An untruncated 64-character digest, an upper-case one, or one
hashed without the prefix is the right kind of value with the wrong value, it passes every negative check, and
creator-comment marking then matches zero rows with no error raised. So the shape is asserted positively,
`^[0-9a-f]{24}$`, and the same question is asked of `tubedepth.comments.author_id` as "is it not a hash"
rather than "is it one known raw shape" — a handle (`@name`) or a legacy id is as much an identifier as the
one shape a predicate happens to recognise.

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
  `DO NOTHING` it reads the row back and raises, naming the column, when any fact it writes — `label`,
  `source_tag`, `source_file`, `source_rows`, `max_report_date`, `update_policy` (`note` excepted: nothing
  downstream carries it) — differs from what is stored, so a refresh is a reviewed bump of the snapshot id
  in code, never a rerun over a grown file and never a relabelled rerun over the same one (fork #83: a
  label-only bump used to pass, and since #77 the chunk text quotes the label that loaded the row). `entp_key` is loader-filled (`CHECK (entp_key <> '')`, a company that
  folds to nothing is refused before any DB contact); changing `normalize_company` therefore needs
  `db/seed/mfds.py` `rekey()` over the stored rows — a rerun of the load cannot repair a key, which is why
  028 grants `needs_runtime` UPDATE as well. `update_policy` has no CHECK — this vocabulary is this
  section's to grow and the DDL is additive only.
- **Searched since fork #77.** Each filing is also a retrieval document (`source='mfds'`, one chunk of
  item name · company · `report no. <report_seq>` · `registered <report_date>` · the snapshot label, BM25 only — `entrypoints.md`, the retrieval block). A refresh (a second
  `mfds_snapshot` row) therefore also means a `cosmai retrieval chunk` run, and the chunk text carries the
  snapshot label that loaded the row.

## The ref grammar of a mention row (A20)
| src | `ref` | note |
|---|---|---|
| review | `product_key/review_key` | |
| yt_comment | `video_id/comment_id` | **both** `need_mention` and `wish_mention` use this grammar. One comment carries the same key in two tables |
| yt_transcript | `video_id` | |
| yt_title | `video_id` | `TextUnit` only — it never enters `need_mention` |
| naver_blog | `post_id` | **reserved (unimplemented, #96)** — the source table `needs.naver_blog_post` (004_naver.sql, #9, T15) exists, but the live transport landed with #182 (`collectors/naver/transport.py`) but the analysis branch (`analysis/polarity/pipeline.py`) has not, so no `need_mention` row of this src exists yet |
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
`request_key` values an **anchor rescale** must come first. #90 decided both halves of it: one
global anchor group goes into every request, and the rescale is computed at read time, never stored.

**The anchor is a label over a real search term (#250).** Its `groupName` is `기준_세럼`
(`collectors/naver/scope.py:DATALAB_ANCHOR`) — the vendor echoes that back as `results[].title`, so
it is the value stored in `naver_datalab_point.group_key` and the literal
`db/views/naver_datalab_rescaled.sql` divides by. What the group actually searches is a separate
constant, `DATALAB_ANCHOR_TERMS` = (`세럼`) (user decision 2026-09-06), and it is the terms, never
the label, that reach the vendor as `keywords` and are stored in the row's `terms`. Until #250 the
label was sent as its own keyword: nobody searches that token, DataLab returned the series with an
empty `data` array, no anchor row existed at all and `ratio_rescaled` was NULL on **every** row.
Because the terms are part of the request body they are part of `request_key`, so rows collected
before and after this change never share a boundary — which is right, they are different requests.
**A DataLab request whose response carried no anchor point leaves the run partial (1)** with a note
naming the anchor (`collectors/naver/cli.py`, `contracts/entrypoints.md`): the rows are written and
stay comparable inside their own `request_key`, but nothing of that request can cross a boundary,
and an `ok` run whose rescale is entirely NULL is the state this rule exists to make visible.

**The request boundary is read off the row as `naver_datalab_point.request_key`**
(`contracts/ddl/needs/006_naver_request.sql`, decision (a): `terms` only audits one group's search
terms and carries no `startDate`/`endDate`/`timeUnit`, so it cannot tell one run of the same group
from another). `request_key` = the sha256 hex digest of the canonical JSON
(`json.dumps(sort_keys=True)`) of the request body actually sent
(`keywordGroups`·`startDate`·`endDate`·`timeUnit`) (`collectors/naver/parsing.py:datalab_request_key`)
— the same parameters give the same key, and moving `endDate` by even a day (that window is rescaled)
gives a different one. Every row one response made shares one `request_key`.

**The rescaled value is the view `needs.naver_datalab_rescaled`** (`db/views/naver_datalab_rescaled.sql`,
#90). Its columns are `category` · `group_key` · `month` · `ratio` (the raw one, untouched) ·
`anchor_ratio` · `ratio_rescaled` · `request_key` · `terms` · `captured_at`, one line per stored
point, and `ratio_rescaled` = `ratio / anchor_ratio` rounded to 6 decimal places. Two rules hold it:
- **within one request only** — `anchor_ratio` is the anchor point of the *same* `request_key` and
  `month`, never "the anchor of that month". Comparing two rows is comparing two `ratio_rescaled`
  values; the raw `ratio` stays comparable inside its own `request_key` alone.
- **NULL when the anchor is missing** — no anchor point for that `request_key` and `month`, or an
  anchor whose ratio is 0, leaves `ratio_rescaled` NULL. A NULL means "not comparable across
  requests"; a filled-in number would be the plausible wrong one this whole section exists against.

The anchor point is still written into `needs.naver_datalab_point` like any other group (`group_key`
= the anchor) — #90's readers keep reading it there. **The rescale view's join is against
`needs.naver_datalab_anchor` instead** (`contracts/ddl/needs/009_naver_datalab_anchor.sql`, #248):
one row per `(request_key, month)`, written by the collector alongside every batch. Unlike
`naver_datalab_point`'s key `(category, group_key, month)` — which let a category's later request
overwrite an earlier one's anchor row, since every batch of one category shares that key — a request
boundary is never overwritten by a different request, so every batch keeps the anchor it was sent
with and all of a category's groups rescale, however many requests it took.

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
