# 04 — Ops · deploy

Today's (2026-08-23) actual runtime is one `service/stack/docker-compose.yml` (postgres:18 + 14 containers). yt-scrapper's systemd units
are left stopped and disabled, files only (`stack/README.md:33-34`), and trend-radar runs via a supercronic container. So this section's "effect" is
observed on the compose side, and its "cost" in the gaps that two coexisting wiring systems create.

| ID | Name | Grade |
|---|---|---|
| O01 | 3-role DB bootstrap — owner NOLOGIN / migrator / runtime, default privileges, per-role timeouts, UTC | Adopt |
| O02 | Idempotent init script (`SELECT … WHERE NOT EXISTS \gexec`) | Adopt |
| O03 | One compose file: `${VAR:-default}`, `.env` outside git, `depends_on` healthy/completed, migrate as a one-shot | Adopt |
| O04 | systemd unit · timer — a reason per option, sandboxing, `Persistent`/`RandomizedDelaySec` | Adapt |
| O05 | Secrets storage outside the tree (`with-secret-source.sh`, `env.example` names only, a pytest sessionstart guard) | Adopt |
| O06 | The health check lives in compose; no `HEALTHCHECK` in the image; `--wait` | Adopt |
| O07 | cron via supercronic inside the image, UTC, on the hour | Adopt |
| O08 | `flake.nix`, an optional devshell | Drop |
| O09 | Schema-migration script (dump → recreate → restore → GRANT → row-count comparison) | Adapt |

---

## O01. 3-role DB bootstrap

- **Where**: yt-scrapper `deploy/postgres-bootstrap.sql:22-37` (owner NOLOGIN, migrator does `SET ROLE` owner, runtime is DML only), `:43-49`
  (`REVOKE CREATE ON SCHEMA public FROM PUBLIC` — without this, schema separation is just a naming convention), `:64-74` (`ALTER DEFAULT PRIVILEGES` — without it,
  runtime can't see a table the next migration creates, "failing not during deploy but on the first request after it"), `:118-131` (statement/lock/idle/transaction
  timeouts on the role), `:133-142` (TimeZone UTC — a measured `timestamptz→timestamp` offset incident), `:153` (CONNECTION LIMIT).
  trend-radar has 4 roles (`service-db.json:6-11`, adds `reader` — for the dashboard, `stack/docker-compose.yml:171`: "serve confirms at startup that this role can't write").
  cosmai-old `apps/db/provision.sql`, `stack/init/50-cosmai-bootstrap.sh:41-70`. The original rules are in `yt-scrapper/docs/shared-postgres.md` (695 lines, rules 0–14).
- **Observed effect**: runtime DDL is impossible **at the database level** (proven by the T11 test). No repeat of the `duplicate column` incident from `test_no_ddl_on_the_boot_path.py:7-10`.
  4 schemas (trend_radar, tubedepth, cosmai, +needs planned) coexist as tenants in one database.
- **Observed cost**: (1) a 695-line rules document + duplicate per-repo bootstraps (trend-radar `tool/db/docker-init.sh` 127 lines, yt 153 lines, cosmai 119 lines — the same pattern three times over).
  (2) `search_path` strategy diverges: yt sets the migrator's too, to `tubedepth, pg_catalog` (`:107-116`, since migrations are schema-unqualified), cosmai gives the migrator only `pg_catalog` (`50-cosmai-bootstrap.sh:62`).
  These need unifying in one repo. (3) the test harness needs an exception like `GRANT CREATE ON DATABASE` (T03).
- **Reuse form**: `snippets/postgres-bootstrap.sql` (an idempotent 3-role template that takes the schema name as a psql variable, 70 lines). One call per schema in the new repo's `db/`.
- **Grade: Adopt** — owner's constraint. A `reader` role goes in as the fourth, for PostgREST exposure (trend-radar's approach).

## O02. Idempotent init script

- **Where**: `stack/init/50-cosmai-bootstrap.sh:41-57` (`SELECT 'CREATE ROLE …' WHERE NOT EXISTS (…) \gexec`), `:17-29` (the password is set only when the role is created —
  running `ALTER ROLE … PASSWORD` every time would quietly leave `~/.config/cosmai/env` holding the wrong value), `:59-60` (session defaults are safe to SET every time).
  Counter-example: `stack/init/30` (the yt-scrapper bootstrap) uses `CREATE ROLE/SCHEMA` with no `IF NOT EXISTS`, so it cannot be rerun (`architect/README.md` §6 #4).
- **Observed effect**: manually rerunning it against an already-initialized cluster with `docker exec … bash /docker-entrypoint-initdb.d/50-…sh` worked (`stack/README.md:62-66`) —
  the "rerun path" was exercised from the very first run (`:6-8`).
- **Reuse form**: `snippets/postgres-bootstrap.sql` is this shape.
- **Grade: Adopt**.

## O03. One compose file + `.env` outside + dependency conditions

- **Where**: `stack/README.md:48-61` (everything in one file, `${VAR:-relative-path}` defaults double as the standard deployment doc, structural changes go in a gitignored `override.yml`),
  `stack/docker-compose.yml:135-139` (pg_isready health check), `:147-149, 240-242` (`service_healthy` / `service_completed_successfully` — start after migrate finishes as a one-shot),
  `:152` (`TREND_RADAR_DATABASE_URL … :?` refuses to start on a missing secret), `:174-175` (the dashboard binds to loopback; widening it happens only through a variable).
  yt-scrapper `Justfile:92-109` (the reason for `--build --wait`).
- **Observed effect**: no person needs to remember the startup order after a reboot (`stack/README.md:50-51` gives the reason data-portal was folded in). Removing cosmai's `network_mode: host`
  and folding it into db-net was done entirely by editing the one compose file (stack commit 42dc88d).
- **Observed cost**: two copies exist — the in-repo compose (`trend-radar/docker-compose.deploy.yml`, `yt-scrapper/deploy/docker-compose.yml`) and the stack compose —
  and T10's compose test only looks at the in-repo one. This duplication is the reason for today's P16 missing-cron incident (`slice-p16…/README.md:46`).
  Image tags are unpinned (`postgres:18`, `postgrest:latest`), and there's a dead key `TREND_RADAR_LEGACY_HOST_PASSWORD` (`architect/README.md` §6 #4).
- **Reuse form**: **only one** `stack/docker-compose.yml` in the new repo. No per-repo compose. `snippets/compose-service.yml` (a service-block template: depends_on · `:?` · loopback · healthcheck).
- **Grade: Adopt**.

## O04. systemd unit · timer

- **Where**: yt-scrapper `deploy/tubedepth-worker.service:16-22` (the reason PATH is explicit — a user unit doesn't inherit the shell's PATH, exit 127 measured), `:24-35`
  (the URL is a credential, so a 0600 EnvironmentFile), `:70-80` (`--poll 5` — with Restart=always this becomes a process-spawn loop, measured at 520ms CPU · 68MB every 10 seconds),
  `:88-92` (SIGINT — releases the lease), `:94-104` (sandboxing and 3 `ReadWritePaths` entries). `deploy/tubedepth-watch.timer:17-29` (`OnBootSec=5min` to avoid queuing ahead of the worker,
  `Persistent=true` for a laptop suspend, `RandomizedDelaySec` to avoid the top of the hour).
- **Observed effect**: the unit file became a record of operational decisions — every option carries a measured number.
- **Observed cost**: stopped and disabled today (`stack/README.md:33-34`). Of 107 lines, only 20 are executable. The same explanation appears again in `docs/status.md:1142-1174`
  ("The listing cap is a deployment setting, and both units must agree"). The structure itself is a cost: the api and worker units must each carry the same cap value **twice** (`:36-42`).
- **Reuse form**: the unit itself is not taken. Only the practice of "one measured line beside each option", as crontab/compose comments (`snippets/crontab`).
- **Grade: Adapt**.

## O05. Secrets storage outside the tree

- **Where**: cosmai-old `scripts/with-secret-source.sh:8-11` (exports only the path, never the value — so it doesn't leak via a child process, a traceback, or an env dump), `:43-51`
  (refuses if the store sits inside the working tree), `:55-71` (enforces mode 600/400), `config/env.example:3-6, 20-24` (only key names are committed, `credential_ref` = key name),
  `tests/conftest.py:58-73` (`pytest_sessionstart` calls the same check as **a function in the platform code** — two copies means the one that drifts is the leak).
  yt-scrapper `AGENTS.md:72-76` (a WireGuard key at `~/.config/tubedepth/`, `.gitignore` is a backstop, not the defense), `stack/README.md:52-54` (`.env` at 600, outside git, `env.example`).
- **Observed effect**: zero credentials in git history across all four repos plus stack (gitleaks CI scans the full history, `checks.yml:60-79`). `~/.config/cosmai/env` carries straight into the new repo's README, principle 5.
- **Observed cost**: the run command gets longer (`./scripts/with-secret-source.sh uv run pytest`). cosmai-old also keeps `.envrc`+direnv alongside it, so there are two paths.
- **Reuse form**: `snippets/with-secret-source.sh` (75 lines as is, only the variable name changes to `COSMAI_SECRET_SOURCE`) + `snippets/env.example`.
- **Grade: Adopt**.

## O06. The health check lives in compose, `--wait`

- **Where**: `stack/docker-compose.yml:135-139, 243` (pg_isready / API healthz), yt-scrapper `deploy/docker-compose.yml:210-212` ("the image deliberately carries no HEALTHCHECK —
  compose decides"), `Justfile:102-104` (`--wait` turns "a stack that never comes up" into a command failure).
- **Observed effect**: a state you'd otherwise learn only by reading `docker compose ps` becomes the command's exit code.
- **Grade: Adopt** — as is, in the new repo's `stack/`.

## O07. cron via supercronic inside the image, UTC, on the hour

- **Where**: trend-radar `docker/crontab:1-10` (no TZ = UTC = the same clock as the `captured_at` bucket; :00 on the hour — a rerun inside the same hour becomes a no-op upsert),
  `Dockerfile`, `CHANGELOG.md:25-27`. Counter-example: `NOTES.local.md:28-30`: "a 07:00 `up -d` cut a scheduled run in half — from now on, avoid restarting on the hour".
- **Observed effect**: of 140 runs, ranking is 69 ok / 5 partial (`slice-p16…/README.md:19`). Since the hour bucket is the unit of execution, a rerun is idempotent.
- **Observed cost**: since crontab lives inside the image, adding a dataset in the stack requires rebuilding the image. Today's 3 missing cron lines are that cost made concrete.
- **Reuse form**: `snippets/crontab` (comments on what UTC, the hour mark, and the exit code mean) + the T10 variant test ("a cron line for every enum member").
- **Grade: Adopt** — except the crontab file itself lives in `stack/` and is volume-mounted (rewiring with no image rebuild).

## O08. `flake.nix`

- **Where**: cosmai-old `flake.nix:1-2` ("optional. The supported path is uv"), `:22-30` (why python/ruff aren't put in nix — a measured `.venv` regeneration).
- **Grade: Drop** — uv alone was enough, and while this host gets uv via a nix profile, that's host configuration, not repo configuration.

## O09. Schema-migration script

- **Where**: `stack/migrate-trend-radar.sh`, `migrate-tubedepth.sh` (`stack/README.md:67-68`: dump → recreate the schema → `--schema --no-acl` restore → policy GRANT → row-count comparison),
  trend-radar `tool/checks/extraction:9-24` (a destructive dry-run requires a `_test`/`_extraction` suffix + confirming the name is repeated).
- **Observed effect**: the public→`trend_radar` schema and 5432→5434 migrations ran end to end via script, row-count comparison included (stack commits 27d36e0, 9f0282a).
- **Grade: Adapt** — the new repo carries the existing schema over **as is**, so this script gets used once more. Not kept after that.
