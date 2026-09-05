# Section names — the `§` anchor rename ledger (#206 part 2)

Every `§` anchor was a Korean heading until #206 part 2. A reader of an older issue, comment or commit
resolves the old name here; a reference in the tree names only the English one.

| old | new | file |
|---|---|---|
| `§수집기` | `§Collectors` | `entrypoints.md` |
| `§DB 접속 노브` | `§DB connection knobs` | `entrypoints.md` |
| `§공통 운영 뷰` | `§Common operations view` | `entrypoints.md` |
| `§분석` | `§Analysis` | `entrypoints.md` |
| `§검색` | `§Search` | `entrypoints.md` |
| `§분기 시계열` | `§Quarterly time series` | `entrypoints.md` |
| `§민감도·후향 검증` | `§Sensitivity and backtest` | `entrypoints.md` |
| `§근거·카드` | `§Evidence and cards` | `entrypoints.md` |
| `§스케줄` | `§Schedule` | `entrypoints.md` |
| `§종료 코드` | `§exit codes` | a bullet in every `entrypoints.md` command section, not a heading |
| `§사전 CSV` | `§Lexicon CSV` | `formats.md` |
| `§aspect 사전의 ruleset` | `§ruleset` | `formats.md` |
| `§주제 사전 v3` | `§Topic lexicon v3` | `formats.md` |
| `§entity 사전의 kind='stopword'` | `§Query stopwords` | `formats.md` |
| `§카테고리 매핑 CSV` | `§Category map CSV` | `formats.md` |
| `§카테고리 표기` | `§Category notation` | `formats.md` |
| `§패널 명부 CSV` | `§Panel roster CSV` | `formats.md` |
| `§코퍼스 스냅샷` | `§Corpus snapshot` | `formats.md` |
| `§표본 상수` | `§Sample constants` | `formats.md` |
| `§평가셋 CSV` | `§Evaluation set CSV` | `formats.md` |
| `§시간` | `§Time` | `formats.md` |
| `§수식` | `§Formulas` | `interfaces.md` |
| `§분기 표의 행 집합` | `§The quarterly table's row set` | a bullet of `interfaces.md` §Formulas |
| `§판정` | `§Verdict` | `interfaces.md` and `entrypoints.md` (both sections were `판정`) |
| `§판정 상수` | `§Verdict constants` | `interfaces.md` |
| `§판정 순서` | `§Verdict order` | a bullet of `interfaces.md` §Verdict |
| `§민감도` | `§Sensitivity` | `interfaces.md` |
| `§근거` | `§Evidence` | `interfaces.md` |
| `§기회 카드` | `§Opportunity cards` | `interfaces.md` |
| `§대조` | `§Crosscheck` | `interfaces.md` and `entrypoints.md` (both sections were `대조`) |
| `§구성` | `§Composition` | `interfaces.md` |
| `§평가` | `§Rating` | `interfaces.md` |
| `§성분` | `§Ingredients` | `interfaces.md` |
| `§홀드아웃` | `§Holdout` | `interfaces.md` and `entrypoints.md` (both sections were `홀드아웃`) |
| `§홀드아웃 상수` | `§Holdout constants` | `interfaces.md` |
| `§모집단의 한계` | `§Limitations of the population` | `interfaces.md` |
| `§평가 하네스가 대조하는 기준선`, `§기준선` | `§Baselines` | `interfaces.md` (one section had two names) |
| `§규칙 실측` | `§Rule measurement` | `interfaces.md` |
| `§검색 실측`, `§Retrieval-measurements` | `§Retrieval measurements` | `interfaces.md` (one section had three names) |
| `§벡터 하한선` | `§Vector floor` | `interfaces.md` |
| `§소스별 분배` | `§Per-source allocation` | `interfaces.md` |
| `§질의 라우팅` | `§Query routing` | `interfaces.md` |
| `§오라우팅 실측` | `§Misrouting measurement` | `interfaces.md` |

`§ruleset` appears once above, as the target of the merge: the section was cited both as
`§aspect 사전의 ruleset` and, already in English, as `§ruleset`, and the English one is now the only name.
Unchanged because they were already English and already single-named: `§Answer layer`, `§ref`,
`§NAVER DataLab`. The dangling `§Evidence` (`entrypoints.md`) and `§Search`
(`tool/measure-transcript-bimodal`) from earlier waves now resolve to the sections above. `§low_complete` is
not in this ledger at all — it names a column of `product_denominator`, not a section.

Still Korean, and unchanged on purpose, because the line that carries the heading holds a Korean data value
or an anchor into a Korean issue heading and `tool/checks/lang` refuses a rewritten line: `§LLM 실측`
(`interfaces.md`, whose heading cites issue #6 `§산출물 6`) and `§확인할 것` (the `commerce_ranking.py`
heading of `interfaces.md`, which cites issue #7). `§라벨 기준(polarity)` names a bullet of
`formats.md` §Evaluation set CSV whose text is the polarity labels themselves. Every `§` that points into a
GitHub issue rather than into this directory also stays (`#8 §산출물`, `#16 §1단계 판정 4`,
`#48 §범위 확장`, `#7 §확인할 것`).

Two bullet labels inside `§Holdout` are cited as `§창` (`interfaces.md`'s `**창**` bullet, `window_reading`)
and `§플랫폼 구성` (its `**플랫폼 구성**` bullet). Neither was declared anywhere before this branch. They stay
Korean: the first bullet is the `window_reading` output labels (`새 기간이다` · `같은 창이 길어졌다`) and a
verbatim ydc quote, the second sits in the same pinned paragraph.

