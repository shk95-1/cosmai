# 05 — Docs · decisions management

Measured (`metrics.md`): markdown lines / code lines — trend-radar 0.35, yt-scrapper 0.69, Research_Paper 0.10, **cosmai-old 0.88** (217 md files, 39,861 lines,
over 8 days). Commit involvement of governance documents — yt-scrapper `docs/status.md` 62/219 commits (28%), cosmai-old `docs/decisions/` 40/191 + `project-state.md` 34/191,
trend-radar `docs/judgment-debt.md` 26/122. The more documents, the higher the share of code commits that also touched a document, and drift still remained anyway (see each item below).

| ID | Name | Grade |
|---|---|---|
| D01 | AGENTS.md structure — "rules that are expensive to break" / "how we work" / layout / checks, ~150 lines | Adapt |
| D02 | `docs/judgment-debt.md` — three bins (not done / explored and empty / decided not to) + the condition to revisit | Adopt |
| D03 | `NOTES.local.md` — a gitignored session note; once it sets, it moves to judgment-debt | Adopt |
| D04 | `docs/sources/<key>.md` — a dated record of site observations | Adapt |
| D05 | Decision records: yt-scrapper `decisions/` (only after it's hit, cost stated) vs. cosmai-old `DP-*` (a 12-section template) | Adapt / Drop |
| D06 | `experiments/EXPERIMENT-TEMPLATE.md` — hypothesis · falsification · exit condition | Drop |
| D07 | Comment voice — "beside the rule, the failure it prevents" | Adapt |
| D08 | 증거 라벨 `[확인 사실]`·`[측정]`·`[추론]`·`[가설]`·`[결정]` | 변형 |
| D09 | Keeping `docs/status.md`·`docs/plan.md` vs. "we don't keep plan documents" | Drop |
| D10 | `docs/troubleshooting.md` — a heading is an error message, made for grep | Adopt |
| D11 | A README `.ko.md` translation pair | Drop |
| D12 | `docs/working-agreements.md` — an "is it enforced" table per agreement | Adopt |

---

## D01. AGENTS.md structure

- **Where**: trend-radar `AGENTS.md` (145 lines): `:7-72` "The rules that are expensive to break", 8 of them (each with why and what breaks), `:74-115` "How we work here", 7 of them,
  `:117-138` Layout (a document map + `*.local.md`), `:140-145` Checks. yt-scrapper `AGENTS.md` (163 lines): `:9-18` "Every session starts here", `:46-81` rules, `:83-115` Workflow,
  `:117-131` This host, `:148-163` a Layout table. cosmai-old `AGENTS.md` (107 lines): points to 12 documents to read (`:9-36`), a role model (`:43-54`), labels (`:70-77`).
- **Observed effect**: all 8 of trend-radar's rules have a test or a hook behind them (`docs/working-agreements.md:172-184`, the table) — the result of hitting "the rule was wrong
  the whole time it was prose" (`test_sources_stay_at_their_layer.py:9-13`) and moving it into a test. Today, a new session adding `review_low` read only this file and kept scope · no-ff · commit format correct.
- **Observed cost**: (1) drift — yt-scrapper `:120-125`: "this line has said 'no Docker' for a long time, and two decisions rest on that false premise. Both need re-deciding"
  (the document's own confession). trend-radar `:79` master-only ↔ `tool/worktree.sh:22`'s dev. (2) cosmai-old's AGENTS.md itself is short, but "go read" spreads into 12 documents, 2,192 lines
  (`docs/conventions/*` + `branching.md` + `agent-workflow/README.md`). (3) the forbidden-site-name test (T09) kept AGENTS.md to "project rules only", at the cost
  of 4 more files under `docs/domain.md` · `docs/sources/`.
- **Reuse form**: `snippets/AGENTS.template.md` — 4 sections (rules ≤8 / how we work ≤6 / a layout table / check commands), a **120-line cap**, each rule marked "enforced: hook|test|none".
- **Grade: Adapt**.

## D02. `docs/judgment-debt.md` — three bins

- **Where**: trend-radar `docs/judgment-debt.md:1-7` (definition: what / why / what would reopen it), `:11-151` §1 decided not to do, `:153-238` §2 known limitations left in place,
  `:240-255` §3 explored and empty, `:318-543` §5 verification/resolution history (225 of 543 lines). `AGENTS.md:91-93` ("mixed together, a decision reads as incomplete and gets quietly reverted").
  Research_Paper `docs/judgment-debt.md:1-7` (explicitly names it "trend-radar's playbook pattern" and ports it, 86 lines, table form); cosmai-old spreads this across `docs/open-questions/`, 16 files (10 OPEN, 4 RESOLVED).
- **Observed effect**: `:23-43` — `Dataset.PRODUCT` was deleted (08-20) → its condition was met (08-21, issue #21) → it came back. Because "the condition to revisit" was written down,
  coming back was recorded as the condition being fulfilled, not a reversal. Today's `architect/REBUILD.md` §3 "cosmai experiments/ deletion candidate" is also something to write down using this pattern, not cosmai-old's document form.
- **Observed cost**: it grew to 543 lines — §5's "resolution history" duplicates the changelog (`CHANGELOG.md:3-5` says "decisions go in judgment-debt", and they point at each other). 26/122 commits touched this file.
- **Reuse form**: `snippets/judgment-debt.template.md` — only §1–§3, 3 lines per item (what/why/condition to revisit), no history section. Once resolved, **delete** the item (git is the history).
- **Grade: Adopt**.

## D03. `NOTES.local.md`

- **Where**: trend-radar `.gitignore:101` `*.local.md`, `AGENTS.md:133-138`, `NOTES.local.md:1-11` (never committed; a judgment that sets moves to judgment-debt),
  `docs/working-agreements.md:163-166` ("whoever clones this won't know what's in progress — a trade-off accepted").
- **Observed effect**: `NOTES.local.md:13-33`, this morning's session record (verified numbers, what got deployed, what's open), is exactly the next session's starting point. 175 lines, outside git.
  cosmai-old's `AGENTS.md:88-89` says the opposite — "do not put a session snapshot anywhere in the tree" — and as a result, progress went into `docs/project-state.md` (435 lines, edited 34 times).
- **Observed cost**: gone if you switch machines. That's intended.
- **Reuse form**: one `*.local.md` line in `.gitignore` + one AGENTS.md paragraph.
- **Grade: Adopt**.

## D04. `docs/sources/<key>.md` — dated observations

- **Where**: trend-radar `docs/sources/oliveyoung.md:1-30` (a recon date, the literal curl 403 vs. Playwright 200, the evidence behind a policy value), `docs/working-agreements.md:81-109`
  ("an undated fact — you don't know since when it's been false"; a table of three cases that mistook a trimmed fixture or response metadata for an observation).
- **Observed effect**: numbers like the 2026-08-19 measurement in `test_user_agent_is_honest.py:9-12` and `CHANGELOG.md:43-49`'s "13 samples, 3.0–34.7s" all came out of this practice.
- **Observed cost**: 4 files. The observation easily duplicates the code-constant comment (S04).
- **Reuse form**: one `collectors/<source>/NOTES.md` file in the new repo (3-line entries: date · command · the literal response). No separate `docs/` tree.
- **Grade: Adapt**.

## D05. Decision records — two approaches

- **Where**: yt-scrapper `decisions/README.md:1-13` ("only after hitting it, only with a measured cost; a rule never hit goes in status.md"), the table at `:15-19` (cost: 8× throughput, p99 1,434→19.9ms,
  0 lease-renewal calls), 30–36 lines per file, 3 files. cosmai-old `docs/decisions/DP-TEMPLATE.md` (67 lines, 12 sections), 33 DPs, **5,051 lines**, longest 349 lines (`DP-033`), `README.md:32-36`
  ("`[측정]` 이 목록은 DP-018~022를 누락했다 — 낡은 색인은 색인 없음보다 나쁘다").
- **Observed effect**: yt's approach — each decision is summarized in one number and writes down "what would make this stop" (`001…:31-36`). Even going dead letter was foreseen: `002…:29-33`
  "once on Postgres, this distinction earns nothing" → after the actual Postgres migration (#15) only a `readonly=True` branch remains (`src/tubedepth/collection.py:167-177`).
- **관찰된 비용**: (1) cosmai-old 색인이 **또** 낡았다 — 위 `[측정]` 경고 아래에서 DP-028~035 8건이 색인에 없다(`grep -c DP-03 docs/decisions/README.md` = 0).
  (2) of 5,051 DP lines, few decisions have a live execution path — by `architect/analysis/cosmai-apps.md`, no slice used experiments at all. (3) yt's `decisions/002` is dead letter too, yet
  `README.md:18` 표에 "활성"으로 남아 있다(삭제 규칙은 있지만 실행 안 됨).
- **Reuse form**: a shrunk version of the yt format — **one file**, `docs/decisions.md`, ≤10 lines per item (rule / the cost incurred, in numbers / condition to retire), "only after hitting it". `snippets/decision-entry.md`.
- **Grade: Adapt (yt) / Drop (DP template)** — owner's call.

## D06. Experiment template

- **Where**: cosmai-old `experiments/EXPERIMENT-TEMPLATE.md` (138 lines: hypothesis · falsification · exit condition · input source · environment · observation · interpretation · result · checklist), `AGENTS.md:103-104`.
- **Observed effect**: today's 7 slices (`architect/slice-*/README.md`) did the same job with no template, using 5 sections — question / data used / result / requirements / limitations — and became the evidence for the rebuild spec.
- **관찰된 비용**: 템플릿 자체가 `[가설]`·`[측정]` 라벨 규칙과 결합돼 한 실험 문서가 수백 줄.
- **Grade: Drop** — the slice-README's 5-section shape carries over to `eval/README.md`, as a convention only.

## D07. Comment voice — "beside the rule, the failure it prevents"

- **Where**: trend-radar `AGENTS.md:114-115`, `docs/working-agreements.md:138-139`. Real examples: `sources/oliveyoung.py:74-78`, `tool/checks/data:8-24`, `.githooks/pre-push:16-27`.
- **Observed effect**: every number carries its evidence, so today's P16 and REBUILD could recover a policy value's intent from reading only the code (the memory rule: "analyze the code, not a plan").
- **Observed cost**: measured — src-directory prose/code: trend-radar 0.39, Research_Paper 0.61, **yt-scrapper 0.70**; files with ≥20 code lines where prose ≥ code:
  trend-radar 3/30, Research_Paper 7/38, yt-scrapper 7/37, cosmai-old 28/192 (max `apps/addon_host/settings.py` at 23 code lines : 49 prose lines). A file docstring grows into an incident narrative
  (`tests/test_fixtures_are_scrubbed.py:1-16` 16 lines, `test_collection_scope_is_recorded.py:1-25` 25 lines).
- **Reuse form**: an amended rule — "**one sentence** beside a constant or condition: date, measured value, the failure it prevents. An incident narrative goes in the commit body (R09)". File docstrings ≤5 lines.
- **Grade: Adapt**.

## D08. Evidence labels

- **어디서**: cosmai-old `AGENTS.md:70-77`, `docs/conventions/evidence-labels.md`(246줄). 사용량: docs+experiments에서 `[측정]` 1,054 / `[확인 사실]` 850 / `[추론]` 664 /
  `[결정]` 461 / `[가설]` 168; 커밋 본문에도 68회.
- **Observed effect**: as in `docs/agent-workflow/README.md:160-190`, "who measured what, and what is only inference" got actually corrected during review rounds (R2, R3).
- **Observed cost**: a label on every sentence makes reading expensive, and the rules document for applying labels is 246 lines. Today's slice READMEs made the same distinction with no labels, using "measured" and "limitations" sections instead.
- **Reuse form**: replaced, in `eval/` · `analysis/` notes, by writing **the date and the command beside the number**. No label system.
- **Grade: Adapt**.

## D09. Keeping `status.md` · `plan.md`

- **Where**: yt-scrapper `docs/status.md` **1,687 lines** (some 20 sections under the heading "Decisions that are expensive to reverse"), `docs/plan.md` 1,126 lines (`AGENTS.md:28-29`: "M0–M9 are all done,
  kept only as a record — don't pick work from here"). The opposite: trend-radar `docs/working-agreements.md:159-162`: "we don't keep plan documents. A stale plan is worse than none — it reads as the current state".
- **Observed cost**: a separate document is needed just to say "don't read" the 1,126 lines of a finished plan. status.md was edited 62 times out of 219 commits.
- **Grade: Drop** — trend-radar's principle adopted. Decisions in D05's one file, progress in `NOTES.local.md`, order from `git log`.

## D10. `docs/troubleshooting.md` — made for grep

- **Where**: yt-scrapper `AGENTS.md:42-44` ("a heading is the actual error message. Don't read top to bottom, grep it"), 265 lines.
- **Observed effect**: a test docstring points at this file's heading, as in `test_no_ddl_on_the_boot_path.py:9-10`'s `duplicate column name`.
- **Grade: Adopt** — one `docs/troubleshooting.md` in the new repo, heading = the literal error.

## D11. README translation pair

- **Where**: yt-scrapper `AGENTS.md:85-98` (README · api · CHANGELOG · AGENTS — 4 documents in English original + `.ko.md`, everything else Korean only), trend-radar `README.ko.md` + the T09 test.
- **Observed cost**: 4 document types × 2 + a sync test. There is no external reader.
- **Grade: Drop** — Korean only. Identifiers and paths stay English (the same rule as this playbook).

## D12. The "is it enforced" table

- **Where**: trend-radar `docs/working-agreements.md:172-184` (7 agreements × enforcement mechanism), cosmai-old `docs/agent-workflow/README.md:43-75` ("1 item enforced, the rest convention —
  don't write convention as if it were control"), `AGENTS.md:54`.
- **Observed effect**: shows at a glance which rule relies on a person, with no hook or test. Writing this table for cosmai-old caught, in review, that "2 enforced" was wrong (`docs/agent-workflow/README.md:51-52`).
- **Grade: Adopt** — absorbed as one column of AGENTS.md's rule table (D01).
