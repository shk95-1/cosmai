# cosmai

Korean: README.ko.md

A cosmetics consumer-needs analysis system — collect → keep the raw copy → normalize → analyze → results.

**Boot from `AGENTS.md`** — boot order, absolute rules, and where each rule is enforced. The only work items are GitHub issues (`tool/issue ready`). `CLAUDE.md` is the one line that imports that file.

This monorepo began without shared history in the 2026-08-23 rebuild. `shk95-1/cosmai` is now the only active development and deployment repository; the temporary `cosmai-import-ydc` fork is retained as history after absorption (#322). Historical repositories are reuse/reference inputs, not parallel queues: their actual archive status varies, so do not infer active ownership from a README or an archive flag. Canonical contracts, additive migrations and import provenance remain in this repository.

The product objective is #321: discover niche cosmetics requirements and produce grounded development briefs. The user-selected Docker development/local runtime is restored under #181, including all fifteen formerly running containers; service simplification follows resolution of the remaining issue plans. The current destination provider and approval boundaries are in `STATE.md`, and executable work is in issues.


## Layout (contracts first)

| Directory | Role | Origin |
|---|---|---|
| `contracts/` | Only contracts a machine can check: DDL, entry conventions, run/fetch_log shapes, lexicon and eval-set formats, analysis package interfaces | new |
| `collectors/commerce/` | The four commerce collectors (+ `review_low` board generalization) | trend-radar `src/` |
| `collectors/youtube/` | YouTube collector (fan-out cap, transcript recovery) | yt-scrapper (tubedepth) `src/` |
| `collectors/naver/` | DataLab · blog collectors (config row + collect) | cosmai-old `apps/addons/collector.naver.*` + outbound policy |
| `analysis/` | linker · extractor · polarity (LLM insertion point) · aggregate | `architect/slice-*/` scripts, merged |
| `db/` | Per-schema migrations and initialization in one place (app.trend_radar, app.tubedepth, cosmai, **app.needs**) | each repo's migrations + stack/init |
| `stack/` | compose · cron · environment — all the wiring | stack |
| `eval/` | labeled_set 660 · regression fixtures (80 product-mapping pairs, etc.) | `architect/slice-*/` |
| `playbook/` | Catalogue of the development methodology extracted from the previous repos (adopted / adapted / rejected) | extracted |

For the images in `stack/`, **the build is the check**: the last `RUN` in `stack/Dockerfile` executes `cosmai --help` · `db/migrate.sh --help` · `ls contracts/ddl/needs/*.sql` and a `scope_threshold()` import through site-packages, inside the image. That build succeeding was the evidence for "verified working inside the image", condition 2 of the closed cutover issue #10, and it is still the same check — there is no separate procedure to run. The scheduler (supercronic) is layered on top of that image by `stack/Dockerfile.cron`.

The build has two steps and `tool/stack-build` runs both at once (from the repo root):

```sh
tool/stack-build
# that is,
#   docker build -f stack/Dockerfile -t cosmai-needs:local .
#   docker compose -f stack/docker-compose.yml build
```

The tag is `cosmai-needs:local`, not `cosmai` — on the deployment host `cosmai` is already the app image of the archived old fleet, and the `shared-db` container set is running it. Running `docker compose build` on its own does not fail; it picks up that other image as its base.

## Principles
1. Only a path a slice has proven is formalized (`architect/REBUILD.md` §2 matrix).
2. Interfaces are contract-first; behaviour is implemented and verified incrementally.
3. Lexicons and eval sets are versioned tables, not files.
4. The LLM is used at one point only (review polarity), and only past a 400-sentence eval set.
5. Secrets are referenced by the `~/.config/cosmai/env` path only; no values live in the repository.
