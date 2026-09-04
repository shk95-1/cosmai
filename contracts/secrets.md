# secrets

- File: `~/.config/cosmai/env` (KEY=VALUE, mode 600). The values are written nowhere else -- not in the repository, the logs or the documents.
- Keys each component reads:
  - collectors/naver: `COSMA_SRC_NAVER_BLOG_CLIENT_ID`, `COSMA_SRC_NAVER_BLOG_CLIENT_SECRET`
  - collectors/youtube: `YOUTUBE_DATA_API_TOKEN` (trending only), the tubedepth API key `COSMA_SRC_TUBEDEPTH_API_KEY` (when the api is protected)
  - db: `COSMA_DB_MIGRATOR`, `COSMA_DB_RUNTIME` (+ the needs role passwords are added to the same file as `NEEDS_DB_MIGRATOR`, `NEEDS_DB_RUNTIME`)
  - collectors/commerce (`collectors/commerce/storage/db.py`): `TREND_RADAR_DB_RUNTIME` — for the `trend_radar_runtime` role alone. The old stack still attaches to that role with its own `.env` value, so it is kept apart from `COSMA_DB_RUNTIME` (#29 — sharing them fails authentication).
  - db, empty-database bootstrap only (`db/migrate.sh` step (0)): `TREND_RADAR_DB_READER` — the
    password of `trend_radar_reader`, the role trend-radar-dashboard logs in with
    (`contracts/anon_exposure.md`). Read together with `TREND_RADAR_DB_RUNTIME` and
    `TUBEDEPTH_DB_RUNTIME` when, and only when, those two schemas are absent — a database that has
    them, which is every production run, is never asked for any of the three.
  - collectors/youtube (`collectors/youtube/storage/db.py`): `TUBEDEPTH_DB_RUNTIME` — for the `tubedepth_runtime` role alone. For the same reason its value differs from both `COSMA_DB_RUNTIME` and `TREND_RADAR_DB_RUNTIME` (#29).
  - analysis/polarity (LLM): `CLAUDE_API_KEY` (the Anthropic API key; not the SDK's default env name, so the code passes it explicitly). The budget hard stop is the code constant `LLM_BUDGET_USD = 10.0` and the running total is `needs.llm_usage` (DDL 003 — 002, the audit reinforcement, wrote it first).
  - Unused (on hold): `COSMA_SRC_OPENALEX_API_KEY` (outside the paper-radar stack), `*_SMOKE_*`, `*_PROBE_*`
- **What is not a secret**: the ollama address (OLLAMA_URL, default model `gemma4:latest`) is a knob, not a secret. Its value lives in `stack/env.example` → `stack/.env` and compose passes it to the analyze container. No value may attach anywhere in this repo to a key this file names in backticks (the secret check in `tests/stack/test_stack_wiring.py`), so that name is not backticked here.
- At start-up only the **existence** of the required keys is checked; a missing one is named and the process exits.
