# Research sessions

Maintained system development follows issues, channel ownership, PR review and deployment rules.
Runtime questions, hypotheses, tasks, decisions and discussions are application state in
`needs.research_session`, `needs.research_event` and `needs.research_action` (031). Runtime state
never authorizes changes to maintained code, production permissions or the mission's acceptance
criteria. #321 adopts agent-managed research and prepared participation; #327's negative evaluation,
#351/#353 holds and #355's finite no-qualified-candidate outcome remain historical results.

## Interfaces and data

`cosmai research create --mission-file <relative-file>` validates `state.Mission` and prints an ID.
`run <id> --worker codex --evidence-file <relative-file> --source-profile <name>` also resumes.
`--evidence-url-env <key>` instead explicitly selects a database evidence source. Session persistence
uses the environment key `COSMAI_RESEARCH_SESSION_URL` or `--session-url-env <key>`. Neither
connection falls back to `runtime_url()`. Source profiles are non-secret names. Frozen-pack hashes
are pinned to the session. Runtime DB profiles must refer to the same configured source on resume;
the controller does not infer identical databases from equal display names.

`status <id>` shows findings, uncertainties, pending packets, evidence locators and resource position;
`--json` exposes the full record. `feedback <id> --text <text>` always accepts spontaneous input.
`--discussion <id>` optionally binds a reply to a specific packet version; natural unbound replies
are also supported. Input is queued with an ID and observed revision, fencing in-flight old decisions.
The worker must interpret pending input through `integrate_feedback` before other work: information,
ideas, decision responses and genuine goal changes have separate effects. Information/ideas retain the
agenda; a decision releases only its tasks not still blocked by other packets. Only a goal change
increments goal_revision and supersedes explicitly affected tasks plus their dependency descendants,
related discussions and named hypotheses. Unrelated pending discussions survive a scoped goal change.
Permissions, resource limits and deadlines cannot be edited by interpretation. The worker supplies
the interpretation and response; humans never need to name event types or dependency IDs.
Replies to superseded/answered packet versions are retained unapplied. Queued replies made stale by
an intervening scoped change cannot apply to a replacement packet. Earlier findings retain their
original goal revision. `pause <id>` prevents subsequent execution transitions.
`reconcile <id> --execution <id> --disposition <text>` records an externally verified disposition of
uncertain work, leaving its reservation consumed. It never retries an external effect.

`participate <id> --worker codex --evidence-file <relative-file> --source-profile <name>`
opens terminal participation using the same explicit source options as `run`. It continues a ready
session, presents a participant brief with durable findings, uncertainty, next validation and all
pending prepared packets, and accepts natural text. The default brief shows unique source links
and explicit usage/limits/deadline without diagnostic IDs, evidence hashes or repeated quotation dumps.
It does not truncate or rewrite substantive findings, caveats, questions, recommendations or objections.
Earlier-objective findings and resource stops remain clearly identified. `status` and `status --json`
retain the complete audit presentation.

Within participation, `/sources` displays all original selected citation passages from the displayed
revision (or inspected evidence when no citations have yet been prepared). Identical repeated passages
are shown once. This local inspection does not invoke a worker, request sources, record feedback,
change a revision/deadline or imply consent. Natural source questions remain ordinary contributions;
the slash command is optional. Source inspection never changes which displayed revision protects
the next submitted response. A saved contribution automatically invokes the existing runner; no separate `feedback`
and `run` commands or technical input labels are needed. Blank input, EOF and interrupt at the prompt
exit without supplying an answer or consent. No discussion is fabricated to require participation.
The contribution transaction compares the displayed state revision with the current revision under
the session lock. A changed display rejects the submission and asks the participant to review and
re-enter it; it cannot silently answer a replacement question. Existing unbound `feedback` callers
continue to observe the revision at submission. This conservative display check can require rereading
after unrelated concurrent progress; independent discussions themselves remain intact.

An active lease, uncertain execution, consumed allowance or expired deadline prevents automatic
continuation and soliciting further input. A deadline that expires while reading does not discard a
submitted contribution: it remains queued, and execution stops without renewing any resources.
Input is committed before continuation, so a crash or failed execution cannot erase it. Restart uses
the durable queue and original limits; uncertain actions still require explicit reconciliation.
The terminal performs no background work while awaiting input and wall-clock limits include that
waiting time. This interface does not establish human effort savings or replace matched observation.

`state.py` defines version 1 records. Evidence includes source, locator, attribution (possibly unknown),
capture time, immutable SHA-256, passage and access limitations. Interpretation, hypothesis, question,
criteria revision and proposed validation are distinct fields. Exact-span validation checks attribution,
not semantic entailment: the latter requires the evaluation below. New worker citations contain
`evidence_id` and `span_id`, never retyped quotations. Context supplies deterministic spans bound to
an immutable evidence hash and original codepoint offsets; code extracts and stores the original
passage without Unicode normalization. Missing IDs, modified evidence and fabricated spans fail.
Historical exact-passage records remain inspectable and retain their original failed dispositions. Current summaries derive from
accepted assessments/findings; all previous assessments, decisions and feedback remain in the ledger.

## Execution

The runner acquires a renewable lease and monotonically increasing fence. Every manager request and
accepted action is stored with a unique request/kind key and input digest before execution. Repeated
identical delivery returns the same action; changed input conflicts. No transaction spans a provider
or research call. Provider proposals must match request, state and goal revisions. Stale results remain
observations of their old inputs and cannot update current hypotheses or conclusions.

Calls, failed attempts and uncertain executions consume reservations. A crash changes unfinished
reservations to uncertain; continuation blocks until explicit reconciliation, including manager calls.
This conservative first slice does not auto-retry even safe interrupted reads. A resumed session retains
its original deadline and resource envelope. Wall-clock limits include absence between invocations;
resumption cannot reset the budget. Provider subprocess groups and read procedures have enforced
wall-clock bounds. Context over 150,000 serialized characters stops explicitly; tool artifacts over
2 MB fail. Long source texts expose only the first 8,000 characters with that limitation attached.
Results and their state transition are committed atomically in PostgreSQL as execution-ID artifacts.

The worker chooses actions; registered Pydantic argument schemas and executor effect classes enforce
the capability boundary. Evidence/tool output is untrusted data. The initial public action set is
frozen-pack search/read, explicit-datasource discovery sampling/corroboration, unchanged discovery
selection, assessment/agenda revision, discussion, findings, pause and a structured development request.
Sampling/corroboration are versioned historical procedures with their existing limitations and checks,
not universal policy. No arbitrary SQL, code execution, crawling, maintained-code edit or production
write is a research action. There is no paid retrieval or automatic fallback chain.

Capabilities are source-specific. A frozen discovery snapshot exposes `sample` as immutable snapshot
import and `analyze` against that completed action; a plain evidence pack exposes neither. Database
profiles retain sample/corroborate/analyze with explicit connections. Nested discovery review, candidate,
claim, query and anchor schemas expose downstream requirements. Claim/anchor span IDs are extracted
before calling unchanged discovery validators. Validation feedback names fields and bounded application
errors, excluding input payloads and provider/connection errors; failed attempts still consume limits.

An evidence-file profile may explicitly include `{"literature":{"max_requests":11}}` (allowed 1–12).
It adds `literature_search` and `literature_read` against the fixed Europe PMC REST search endpoint,
using lite metadata and core abstracts respectively. No redirects, hidden retries, arbitrary URLs,
full-text access or credentials. Reads accept only MED/PMC IDs discovered in this session. Search
results alone are not evidence. Original abstract text/markup, article metadata, retrieval timestamp,
API response hash and limitations are stored. Repeated reads reuse the immutable captured result.
Every reserved literature action, including failed/stale/uncertain actions and cached reads, consumes
the conservative request quota. Each action sends at most one HTTP request; responses are bounded to
1 MB and 20 seconds inside the outer action deadline. Abstracts alone cannot establish detailed
manufacturing feasibility, consumer demand or clinical generalization. No-result/access failures
cannot establish absence. Official interface: https://europepmc.org/RestfulWebService .

Fulltext access additionally requires `"fulltext": true` in that literature configuration. Existing
abstract-only profiles retain their capabilities; changing the configuration changes its pinned hash.
Search results expose `pmcid` and `isOpenAccess`. `literature_fulltext` accepts only a PMC identifier
discovered in this session and flagged open access; it uses the fixed
`https://www.ebi.ac.uk/europepmc/webservices/rest/<PMCID>/fullTextXML` endpoint with the same response,
timeout, redirect and retry bounds. Invalid/returned-mismatched article IDs, non-UTF-8 or malformed
XML, and DTD/entity declarations fail. Fulltext failures are access limits, not negative research.

The action stores original XML, response SHA-256, capture time, discovered article metadata and a
capture hash in its durable result. It exposes a stable section index. Body/back text is extracted
in document order with original Unicode text-node characters and no inserted whitespace. Tables
also have separately indexed XML serialization preserving cells, headers and spanning attributes;
linearized text alone cannot establish their relationships. Each representation is split into
explicit chunks of at most 8000 characters, with all chunks indexed by XML response hash and offsets.
No section is silently truncated. Figure images and supplementary files are not supported.

`literature_section` selects a captured execution and exact section ID. Code verifies the capture,
XML response hash and recomputed index before extracting evidence; changed/fabricated references
fail. Evidence retains capture time and links to its original article, capture hash and section
offsets. Repeated fulltext requests reuse the original capture. All four literature actions,
including cached fulltext and local section reads, conservatively consume the configured literature
allowance as well as their normal executor reservations; a section read sends no HTTP request.
The worker sees the remaining literature-action allowance. Successful results distinguish actual
network requests from local cache/section reads; failed or uncertain attempts remain conservative
reservations rather than a claim of known network usage.

Manager context contains the full section index and explicit instructions for reading it instead of
the raw XML document. Selected evidence remains fully visible in `state.evidence` and citation spans. Observation results
replace only exactly identical evidence copies with explicit ID/hash references to that same context;
unknown or differing evidence stays in the observation. No durable result is mutated or text dropped;
the full XML remains in the durable action artifact. The 150,000-character context and 2 MB result
bounds are unchanged, including after restart; overly large indexes or selected evidence stop
explicitly. Exact extracted citations do not prove semantic support, formulation equivalence,
manufacturing feasibility, consumer demand or independent replication.

Remaining resources also expose measured serialized context characters/headroom, the unchanged hard
bound, pressure signals and a two-action synthesis reserve. Calls signal pressure at three remaining,
literature at two remaining and time at two per-call allowances remaining. These signals instruct the
worker to narrow, assess/qualify hypotheses and finish supported findings before exhaustion; they do
not renew or guarantee capacity. At 110,000 characters before accounting fields, further acquisition
actions are unavailable, leaving headroom under the existing 150,000 hard bound. Assessment, feedback
interpretation, genuine discussion and completion remain available. All inspected evidence, contrary
passages, observations and immutable source artifacts remain intact; no scientific conclusion is
written by this guard. A single oversized result can still hit the hard bound and must remain an
honest failure rather than a silently truncated source.

Codex CLI's documented `exec` interface uses ChatGPT authentication, JSON-schema output, an isolated
temporary working directory, ignored user configuration, read-only sandbox, disabled shell/apply-patch/
multi-agent/web tools and bounded execution. Returned tool executions fail validation. Claude Code's
alternative uses subscription authentication, disabled tools, empty MCP configuration and settings
sources, structured print output and no persisted conversation. The live probe found expired Claude
OAuth; an installed/auth-status-positive executable is not proof of availability. Environment API keys
and paid-provider routing switches are not inherited. Token reports and reported equivalent costs are
preserved; subscription billing is not an exact monetary guarantee. Zero paid budget remains binding.
Interfaces: [Codex non-interactive](https://learn.chatgpt.com/docs/non-interactive-mode) and
[Claude Code programmatic use](https://code.claude.com/docs/en/headless).

## Participation and limits

A packet supplies the question, why now, findings/evidence/uncertainty, options, recommendation,
strongest objection, requested contribution, dependent tasks, continuing work and observed versions.
The first slice permits no implicit authorized default. Only dependent tasks block; independent
ready tasks can proceed. Optional packets with no dependent tasks do not block reads or findings;
they remain visible after completion. All-dependent agendas stop for participant input. No response is consent.
A worker must not use a pending discussion to bypass blocked task IDs or remove their dependency.

States are ready/running/paused/blocked/finished, with separate outcomes. Resource/context stops
are not successful research. This CLI has no scheduler, web UI or arbitrary research-code sandbox.
Historical frozen packs can calibrate behavior; repeat success on them is not independent validation.

## Evaluation contract

`tests/fixtures/research/controlled.json` is a synthetic, answerable/unresolved/misleading pack.
The dosing question is answerable only for the measured lotion/setup: lower observed variation is
supported. Transit leakage is decisive counterevidence. Skin tolerance, consumer demand, population
failure rates and a deployable new-product opportunity are unresolved. Dosing cannot establish safety
or demand. A usable result proposes a shipment-leakage test and demand/tolerance investigation where
relevant. Universal abstention, invented efficacy/demand or ignoring transit leakage fails the rubric.
This is fixed before live evaluation; fixture-scripted proposals verify protocol, not live semantics.

Four integrated scenarios are covered by `tests/test_research.py`: grounded and invalid citations;
selective participation/independent work/changed priorities/stale response; restart/duplicate/fence;
source failure/finite resources/uncertain provider usage. Actual worker traces receive a separate
claim-by-claim check against the pack and rubric. Calls/tokens/time/retries/source type are reported.
Cosmetics pilot quality and participant burden are not inferred from this synthetic demonstration.
A matched pilot needs observed human participation, comparable missions/evidence/intelligence/budgets,
no unsupported decisive claims, preserved counterevidence and useful discriminating validation.
Its burden target is fewer routing/context-reconstruction interventions without a quality-floor
failure; voluntary ideas and consequential preferences are measured separately. Without the matched
baseline and observation, the pilot assessment is inconclusive and #321 validation remains open.
