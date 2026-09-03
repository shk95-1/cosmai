# cosmai

Korean: README.ko.md

A cosmetics consumer-needs analysis system — collect → keep the raw copy → normalize → analyze → results.

**Boot from `AGENTS.md`** — boot order, absolute rules, and where each rule is enforced. The only work items are GitHub issues (`tool/issue ready`). `CLAUDE.md` is the one line that imports that file.

This is a monorepo started fresh **without shared history**, per the 2026-08-23 rebuild spec. The old cosmai moved to `slopindustries/cosmai-old` (archived) and the assets worth keeping have already been migrated into `contracts/` and `playbook/` — fetching from an old clone fails for lack of a common ancestor, and that is expected. The previous repositories (`cosmai-old`, `trend-radar`, `yt-scrapper`, `Research_Paper`, `stack`, `data-portal`) are archived (read-only), and this repository replants **code only** — documents, hooks, meta-tests and development philosophy are not carried over; they are extracted separately into `playbook/`.

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
