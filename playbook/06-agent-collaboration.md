# 06 — AI agent collaboration rules

Most commits across the four repos were written by an agent: the `Co-Authored-By: Claude` trailer appears on trend-radar 85/122, yt-scrapper 165/219, cosmai-old 185/191, Research_Paper 58/61
(`metrics.md`). So whether an AGENTS.md rule was "followed" can be read directly from git history. The conclusion up front — **a rule backed by a hook or a test was followed, and
a rule that was prose alone broke in the form of the document itself confessing the drift.**

| ID | Name | Grade |
|---|---|---|
| A01 | `CLAUDE.md` → `AGENTS.md` (a symlink, or `@AGENTS.md`) | Adopt |
| A02 | "A session starts here" — doctor → blocked issues → a status document | Adapt |
| A03 | Done is defined as something observable ("query the loaded rows and say a number") | Adopt |
| A04 | Role separation — orchestrator / planner / worker / attacker + a task packet | Drop |
| A05 | An attacker subagent with `disallowedTools: Write, Edit` | Adapt |
| A06 | "Commit and push only when asked", a co-author trailer | Adopt |
| A07 | `BLOCKED` when it can't be verified — no conditional PASS, a `blocked/<what>` issue label | Adopt |
| A08 | Context files hold only project knowledge; domain knowledge stays outside | Adapt |
| A09 | Rules go into tests/hooks — measured compliance of prose-only rules | Adopt |
| A10 | "Don't write convention as if it were control" — state the enforcement mechanism | Adopt |
| A11 | No conversation logs or session snapshots in the tree (`.superpowers/`, etc.) | Adapt |
| A12 | "Classify a failing test before fixing it" / "a feature with no caller is not a feature" | Adopt |
| A13 | Batching → issue → clear: don't execute a fragmentary instruction immediately, queue it until decisions close, write the whole implementation plan into an issue, then implement it from a fresh context | Adopt (new, 2026-08-23) |
| A14 | Per-role model · effort pinned in an agent definition file — the coordinator stays cheap, only the judgment call is delegated to an expensive model | Adopt (new, 2026-08-23, effect unmeasured) |

---

## A01. `CLAUDE.md` → `AGENTS.md`

- **Where**: trend-radar `CLAUDE.md -> AGENTS.md` (a symlink), cosmai-old `CLAUDE.md` (one line, `@AGENTS.md`), yt-scrapper `CLAUDE.md:1-7` ("just a compatibility entry point, no policy lives here").
- **Observed effect**: one file survives a tool change. The only friction was trend-radar `tests/test_docs_references_resolve.py:46` needing code to skip the symlink.
- **Grade: Adopt** — the `@AGENTS.md` form (a symlink breaks on a Windows checkout).

## A02. Session-start checklist

- **Where**: yt-scrapper `AGENTS.md:9-18` (`tool/doctor.sh` → `gh issue list --label blocked` → `docs/status.md`), `:20-40` (a milestone/issue is "what", status.md is "why").
  cosmai-old `AGENTS.md:28-35` ("go read" 7 documents). trend-radar `AGENTS.md:119` ("read contract.py first") + `NOTES.local.md`.
- **Observed effect**: today's `review_low` session got its context from just AGENTS.md + NOTES.local.md in trend-radar. yt-scrapper depends on `gh` — this machine has `gh`, but
  the issues close too once a repo is archived.
- **Observed cost**: cosmai-old's "go read 7 documents" loads 2,000+ lines every session. Whether an agent actually read all of it can't be verified.
- **Reuse form**: AGENTS.md's first section, 3 lines: `tool/doctor.sh` / `NOTES.local.md` (if present) / `docs/decisions.md`.
- **Grade: Adapt**.

## A03. Done is defined as something observable

- **Where**: trend-radar `AGENTS.md:87-89` ("a green test was wrong seven times, six of them exit 0"), `docs/working-agreements.md:12-27` (the incident table), `tool/checks/data` (T13).
  yt-scrapper `docs/definition-of-done.md:3-5` ("'works correctly' can't be checked; '404 on a deleted record' can"), `:23-33` a 6-item checklist per change.
- **Observed effect**: `NOTES.local.md:18-24`: "visited 84, filled 84, null 0. distinct 83… length 95–41,365, median 520" — the rows were actually counted before this morning's merge.
  yt-scrapper `decisions/003` ("tests, type check, and lint all passed, and there was no behavior") is the evidence behind this rule.
- **Observed cost**: not automatable. A person has to push back every time on an agent's tendency to report "tests pass" as done.
- **Reuse form**: one AGENTS.md line + `tool/checks/data` (T13). A completion report's shape: "N rows loaded, M distinct, K null, run time".
- **Grade: Adopt**.

## A04. Role separation + a task packet

- **Where**: cosmai-old `AGENTS.md:43-54` (orchestrator/planner/worker/attacker, the flow `owner decision → packet → result → attack → acceptance`), `docs/agent-workflow/`,
  29 md files (4 role docs + 2 templates + a prompt + 12 packets + 7 reviews), `.claude/agents/`, 3 files.
- **Observed effect**: `docs/agent-workflow/README.md:176-180` — an independent reviewer caught a real defect like "12 fetches at max_pages=2, 600 rows emitted". A test
  (`tests/environment/test_agent_packet_record.py`) enforces a packet's ACCEPTED condition.
- **Observed cost**: (1) 29 documents, and a review round spawns a review of the review (`REVIEW-TASK-001`, `-R2`, `-R3`). (2) the only thing enforced is "does the packet have a PASS link" (`README.md:51-62`).
  (3) of 191 commits over 8 days, the code that ended up used in a slice is only the DataLab and blog collectors (`architect/REBUILD.md` §3). (4) today's 7 slices got done without this model, using a single session plus scripts.
- **Grade: Drop** — owner's call. When an independent review is needed, do it ad hoc via A05.

## A05. The attacker subagent

- **Where**: cosmai-old `.claude/agents/adversarial-reviewer.md:1-5` (`disallowedTools: Write, Edit, NotebookEdit`, "report only, no repairs"), `docs/agent-workflow/README.md:65-75`
  ("Bash was still left in, and on 2026-08-19 a reviewer made a file and edited a test with Bash, then reverted it — a mitigation, not a write barrier. The real property was **working from a copy**").
- **Observed effect**: the reviewer broke the author's confirmation bias once (`README.md:176-183`, a case that read "the add-on cooperated" as "the platform enforced it").
- **Observed cost**: the frontmatter blocks only `Write/Edit`. 7 review reports, plus reviews of reviews.
- **Reuse form**: no subagent definition. When needed, one "read-only review" prompt line from a `git worktree` (R07) copy — the copy itself is the barrier.
- **Grade: Adapt**.

## A06. Commit and push only when asked, a co-author trailer

- **Where**: cosmai-old `AGENTS.md:26` ("Commit or push only when asked"), every repo's commit body carries `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` +
  `Claude-Session: https://claude.ai/code/session_…` (trend-radar 20b875e, ba11c24).
- **Observed effect**: which session made which commit can be traced — today's b9ffa95 (`review_low`) is identified by the trailer too. Distinguishable from human commits
  (trend-radar `ea2b591 Mount the dev Postgres volume…`, `8b01202 Merge product rows…` — 2 non-conventional subjects from before the hook existed).
- **Observed cost**: none.
- **Grade: Adopt**.

## A07. `BLOCKED` — no conditional PASS

- **Where**: cosmai-old `AGENTS.md:53` ("no access, no evidence — that is not a PASS"), yt-scrapper `docs/definition-of-done.md:11-19` (`gh issue create --label blocked --label blocked/<what>`),
  `AGENTS.md:15-16` (checks blocked issues at session start), trend-radar `tool/checks/prerequisite` (exit 69 — the shell version of the same principle, R04).
- **Observed effect**: yt-scrapper `AGENTS.md:120-125` — on discovering "no Docker" was a false premise, it wrote down "two decisions need re-deciding" rather than moving on.
- **Reuse form**: one AGENTS.md line + a "## BLOCKED" section in `NOTES.local.md` (with no dependency on an issue tracker).
- **Grade: Adopt**.

## A08. Context files hold only project knowledge

- **Where**: trend-radar `tests/test_agent_context_is_project_only.py:1-15` ("only rules that survive a site changing tomorrow"; forbids a source key or a Korean site name — names come from the registry,
  auto-extending when a 5th source appears), `AGENTS.md:1-5` (only the first paragraph is exempt).
- **Observed effect**: AGENTS.md's 145 lines were edited 14 times over 8 days, and site-specific knowledge never once got in (the test blocks it).
- **Observed cost**: 80 lines of test + maintaining the split between `docs/domain.md` · `docs/sources/`. The new repo is a monorepo, so "domain" is the project itself — the boundary is different.
- **Reuse form**: the rule only: "don't put in AGENTS.md a sentence that could move to `collectors/<x>/NOTES.md`". No test.
- **Grade: Adapt**.

## A09. Rules go into tests/hooks — measured compliance of prose-only rules

- **Evidence** (`metrics.md`):

  | Rule | Enforcement | Measured compliance |
  |---|---|---|
  | Conventional Commits | hook (trend-radar, yt) | 121/122, 219/219 |
  | Conventional Commits | none (cosmai-old; not even mentioned in AGENTS.md) | 23/191 |
  | Conventional Commits | none (Research_Paper; no AGENTS.md at all) | 59/61 — exceptionally high; one session stayed consistent on one plan (`.superpowers/sdd`) |
  | Subject ≤72 chars | hook | 0 / 0 over |
  | Subject ≤72 chars | none | cosmai-old 114/191 over, longest 150 chars |
  | Source only imports downward | prose (initially) → test | 4/4 sources broke it while it was prose (`test_sources_stay_at_their_layer.py:9-11`) |
  | 결정 색인 최신 | 산문 + `[측정]` 경고 | cosmai-old DP-028~035 8건 누락(경고문 바로 아래에서) |
  | "No Docker on this host" | prose | wrong, for "a long time" (`yt AGENTS.md:122`) |
  | Record a scope change | test + lock | today's b9ffa95 recorded it from the first commit |
  | No hardcoded version | AST test | 0 violations |

- **Conclusion**: agents followed a rule backed by a hook or a test close to 100% of the time, and broke a prose rule more the more documents there were (cosmai-old). "Add checks, not rules" is the common
  lesson across the four repos, and trend-radar `docs/working-agreements.md:174` ("only what could be judged went into a test") put it into words.
- **Grade: Adopt** — AGENTS.md rule count ≤8, an enforcement-mechanism column per rule (D12).

## A10. "Don't write convention as if it were control"

- **Where**: cosmai-old `AGENTS.md:54`, `docs/agent-workflow/README.md:45-47` ("read together, the protocol gets trusted instead of checked"), `.claude/agents/adversarial-reviewer.md:7-12`
  ("the `effort` frontmatter is silently ignored — don't add it thinking it works, confirmed 2026-08-18").
- **Observed effect**: the cheapest rule for keeping an agent from overstating its own ability. Today's P16 held the same posture ("0 code changes, DB logs only").
- **Grade: Adopt** — the same item as the D12 table.

## A11. No conversation logs or session snapshots in the tree

- **Where**: cosmai-old `AGENTS.md:88-89`. Yet the actual tree: `cosmai/.superpowers/` (480K), `Research_Paper/.superpowers/` (148K), `yt-scrapper/docs/superpowers/` (76K),
  `cosmai/docs/superpowers/` (100K) — the superpowers plugin's SDD ledger and plan files. Research_Paper `docs/judgment-debt.md:4-6` **cites** `.superpowers/sdd/…/progress.md`
  as its "ledger" (a document depends on it regardless of whether it's gitignored).
- **Observed cost**: the rule and reality diverge. Once a ledger sits inside the tree, a document starts pointing at it, and the link breaks on a clone that lacks it.
- **Reuse form**: `.superpowers/` · `*.local.md` in `.gitignore`; a document never cites inside it (if it's worth citing, move it into `docs/decisions.md`).
- **Grade: Adapt**.

## A12. Classify then fix / a feature with no caller

- **Where**: cosmai-old `AGENTS.md:41` ("classify a failing test as an implementation, spec, assumption, evaluation, or goal failure before fixing it"), yt-scrapper `decisions/003…:1-4, 26-30`
  ("add a feature, then grep its name to count callers"), `tests/conftest.py:54-60` (the reason `--record-payload-shapes` is a pytest option, not a CLI one = src code with no caller is forbidden).
- **Observed effect**: after `decisions/003`, a "the worker calls it" test was added (`:21-24`). trend-radar `docs/judgment-debt.md:100-131`'s cleanup of "wired in but nothing reads it"
  (5 deleted, 2 kept deliberately) is the same rule at work.
- **Observed cost**: today's `architect/README.md` §6 has 10 "unfinished/no execution path" items — an agent kept writing code with no caller even with the rule in place
  (`ProxiedEgress`, `PlaceholderScreen.tsx`, a `Source` protocol that's only declared). A rule a person greps is a rule a person forgets.
- **Reuse form**: two AGENTS.md lines + one `git grep -c <new_symbol>` before merging. The same thing as the new repo's principle 1 ("only a path a slice has proven").
- **Grade: Adopt**.

## A13. Batching → issue → clear (2026-08-23, first adopted in the new repo)

- **Where**: no such practice in the four old repos. Derived from `cosmai`'s stage-1 session (2026-08-23): a two-stage structure — design discussion (an architect session) → `HANDOFF.md` → an implementation session — worked well, while acting on a user's fragmentary instruction immediately, within the same session, increased revision rounds.
- **What**: while an agent is coding, a user's major change request is **queued, not implemented immediately**. Once the decision list closes (0 open items, or a stage boundary), the whole implementation plan — down to exact values — is written into a GitHub issue, and a fresh session with a cleared context implements it from reading only the issue. The issue's comments are the ledger (judgment, revision rounds, the completion commit).
- **관찰된 효과**: `[측정]` 1단계 Task 2 — 조정자가 즉석에서 정한 `site_axis_map` 규칙이 2라운드 뒤 철회됨(25→21→34행). 원본 소스를 보고 계획했으면 1라운드. 같은 세션의 Task 1은 브리프가 값을 verbatim으로 담아 수정 0라운드. 계획의 정확도가 곧 라운드 수다.
- **Observed cost**: batching a small fix (a typo, one line) makes the delay more expensive than the fix. With no exit condition for "waiting", a decision never closes. If the issue becomes another copy of the truth alongside the contracts and decision records, drift comes back.
- **Reuse form**: the batching bar = a change touching a contract, DDL, an interface, ordering, or an approval boundary. Everything else goes immediately. Exit condition = the user says "final", or a stage boundary. An issue is the execution plan + prior approval + judgment + the ledger; the contracts and decision records get only a link. The issue body is written at subagent-brief precision (exact values, paths, row counts, a forbidden list). The queue has to be visible to the user (a draft comment on the issue). The state file (`STATE.md`) carries only the boot order, current facts, and approval boundaries.
- **Grade: Adopt**. Enforcement: none (a prose rule) — though pre-push could check "no implementation branch merges without an issue" via the branch-name pattern `feat/<issue#>-…` (not installed).

## A14. Per-role model · effort pinned in a definition file (2026-08-23, effect unmeasured)

- **Where**: no such practice in the old repos (a light variant of A04's role separation — just a model/effort choice, no packet or ritual). The new repo's `.claude/agents/{impl-mechanical,impl,reviewer,reviewer-deep,judge}.md`.
- **What**: pins `model` · `effort` · `disallowedTools` per subagent role in frontmatter. Two implementers (mechanical = sonnet·medium, judgment = opus·high), two reviewers (sonnet·high / opus·high, read-only), one judge (fable·high, read-only — only for a cutover condition, a full review, or a contract change). The coordinating session stays on a mid-tier model (Opus)·high, delegating only the expensive judgment call to `judge`. The alternative to changing the session's model is a `/model` switch, or a new session via `claude --model …` (with no loss, since the issue and STATE.md carry the context).
- **Measured facts**: ① the Agent tool has only `model`, no effort — without a definition file a subagent simply inherits the session's effort (measured 2026-08-18: while the session's effort moved high→xhigh→low, this went uncontrolled). ② a definition file is pinned at session start — in the session that created it, `Agent type not found` (2026-08-18). ③ Stage-1 measurements: a sonnet implementer, 11 minutes, 0 revision rounds (values verbatim in the brief); an opus implementer, 24 minutes, 3 revision rounds (13 mapping judgment calls) — round count tracked brief accuracy more than model choice. 6 reviews ≈ 26 minutes, ~25% of the total.
- **Hypotheses (to measure next session)**: dropping a mechanical unit to medium does not raise the revision-round count; dropping the coordinator to Opus keeps judgment quality (1 withdrawal out of 10 in stage 1); delegating to `judge` keeps human intervention at 0 through wave 4. Measured via: per-unit implementation time · revision rounds · review time · judgment withdrawals, tallied from issue comments.
- **Observed cost**: since a definition file is pinned at session start, "make it now, use it now" doesn't work (it has to align with a clear boundary). More roles means more definitions to drift — capped at 5.
- **Reuse form**: 5 definition files (each a ≤15-line system prompt: scope, forbidden actions, report format only). The selection rule ties 1:1 to the issue's review grade (A/B/C) (`#16`). In a repo with no grades, "are all the values in the brief" is the only branch.
- **Grade: Adopt** (effect unmeasured — regrade after measuring).
