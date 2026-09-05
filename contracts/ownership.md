# ownership — what the fork may change, must not change, and must send upstream

Two checkouts write into one repository history and one production database: upstream `cosmai`
and the fork `cosmai-import-ydc`. Incidents #150 · #103 · #115 are the record of what happens
without a written boundary — objects the fork put into production that upstream did not know about
were dropped, or killed a check — and #107 is the record of a view reverted because two checkouts
deployed to one database. #192's methodology row 3 turned that into the rule this file
carries; AGENTS.md names this file, `tests/test_ownership.py` and `tool/checks/ownership` as its
enforcement sites.

## The rules

1. **Fork DDL numbers are ≥ 020.** `contracts/ddl/needs/0[01]*.sql` is upstream's block and
   `contracts/ddl/needs/0[2-9]*.sql` is the fork's. DDL stays additive on both sides
   (`tests/test_ddl_additive_only.py`), and fork DDL reaches production only after it is merged
   upstream through a wave PR — production DB actions run from the upstream checkout only
   (#192 D7, `STATE.md` §3).
2. **The fork changes its own modules, never the upstream guards.** The two lists below say which is
   which. A path on the must-not-change list is changed by upstream and arrives in the fork by
   merge, never the other way around.
3. **A shared-surface change goes upstream in the same wave.** A file that exists on both sides is
   not the fork's property. Changing one is allowed, and it obliges the fork to send that change
   upstream in the same wave — never months of them in one PR (#192 methodology row 1, the gap
   bound). The third list is the record of which surfaces the fork's PRs have already touched.

Before opening a fork → upstream PR the fork runs `tool/checks/ownership upstream/main`, which
intersects `git diff --name-only upstream/main...HEAD` with the must-not-change list and exits 1 on
any hit. `tool/issue audit` prints the same result as an item on a checkout that has an `upstream`
remote.

## Paths the fork owns

Everything PR #59 (`3b464fa...5c04ef5`) added that upstream had no file for, grouped by directory,
plus what the later wave PRs added (PR #219: the MFDS loader, its data and tests; PR #227: the
ydc import pin test).
The fork writes these without asking; upstream does not edit them outside a merge.

```ownership:fork-owned
analysis/cards/
analysis/crosscheck/
analysis/evidence/
analysis/holdout/
analysis/judge/
analysis/retrieval/
analysis/sensitivity/
analysis/trend/
contracts/ddl/needs/0[2-9]*.sql
db/corpus/
db/seed/mfds.py
db/seed/panel.py
db/views/metrics_topic_quarter_violation.sql
db/views/topic_quarter_evidence_quote.sql
db/views/topic_quarter_evidence_violation.sql
db/views/topic_quarter_judgement_violation.sql
eval/mfds/
eval/panel/
tests/fixtures/mfds/
tests/fixtures/trend_sample/
tests/fixtures/yt_handoff/
tests/retrieval/
tests/test_cards_rules.py
tests/test_corpus_import.py
tests/test_crosscheck_*.py
tests/test_evidence_*.py
tests/test_holdout_*.py
tests/test_judge_*.py
tests/test_judgement_contract.py
tests/test_mfds_seed.py
tests/test_month_grain_regression.py
tests/test_panel_quarter_contract.py
tests/test_panel_seed.py
tests/test_sensitivity_*.py
tests/test_trend_*.py
tests/test_ydc_pin.py
tool/compare-ydc-*
tool/measure-*
tool/show-lexicon-stamp
tool/show-vector-stamp
```

## Paths the fork must not change

The schema and its deployment path, the checks and hooks that enforce the rules, the boot page and
the operating documents, and the agent definitions every session loads. A change here that only the
fork has is a change production never sees, or a guard that silently stops guarding one side.

```ownership:must-not-change
.claude/agents/
.claude/settings.json
.githooks/
AGENTS.md
CLAUDE.md
README.ko.md
README.md
STATE.md
contracts/ddl/needs/0[01]*.sql
contracts/ownership.md
db/bootstrap.sql
db/bootstrap_source.sql
db/grants/
db/migrate.sh
tests/tool/
tool/checks/
```

## Shared surfaces the fork has touched — go upstream in the same wave

These existed on both sides before PR #59 and the fork changed them anyway, which is exactly the case
rule 3 governs. They are listed so the next fork → upstream PR knows what to diff first. PR #227
added the last two (the `retrieval_ask` cap lives in the polarity ledger and its tests).

```ownership:shared-surface
analysis/polarity/pricing.py
analysis/types.py
contracts/README.md
contracts/entrypoints.md
contracts/formats.md
contracts/interfaces.md
contracts/versioning.md
cosmai/cli.py
db/lexicon.py
db/seed/__init__.py
db/seed/lexicon.py
eval/README.md
pyproject.toml
stack/README.md
tests/snapshots/cosmai_help.txt
tests/test_cli_help.py
tests/test_cli_lexicon.py
tests/test_contract_ddl.py
tests/test_llm_polarity.py
tests/test_migrate_ledger_and_grants.py
tests/test_seed.py
tool/checks/paths
tool/checks/test
uv.lock
```

Two of those, `tool/checks/paths` and `tool/checks/test`, are also on the must-not-change list. That
overlap is the history, not a permission: those edits reached upstream only through PR #59's review,
and under rule 2 the fork does not make them again. `tool/checks/ownership` would flag them today,
which is the intended answer.
