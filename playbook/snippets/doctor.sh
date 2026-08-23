#!/bin/sh
# origin: service/yt-scrapper/tool/doctor.sh (hooks + toolchain + database sections, host-specific parts removed)
# reuse: first command of every session. Set DB_URL_ENV; add a check only for a failure that is confusing far from its cause.
set -e
failures=0
ok()   { printf '\033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '· %s\n' "$1"; }
bad()  { printf '\033[31m✗ %s\033[0m\n' "$1" >&2; failures=$((failures + 1)); }

DB_URL_ENV=COSMAI_DATABASE_URL

# The one that cannot be a warning: a fresh clone has no hooks until this is set.
if [ "$(git config core.hooksPath 2>/dev/null)" = ".githooks" ]; then
  ok "git hooks enabled (core.hooksPath=.githooks)"
else
  bad "git hooks are NOT enabled. Run:  git config core.hooksPath .githooks"
fi

for tool in uv docker pg_isready gitleaks; do
  if command -v "$tool" >/dev/null 2>&1; then ok "$tool"; else warn "$tool not installed"; fi
done
command -v uv >/dev/null 2>&1 || bad "uv is required (https://docs.astral.sh/uv/)"

url=$(eval "printf '%s' \"\${$DB_URL_ENV:-}\"")
if [ -z "$url" ]; then
  warn "$DB_URL_ENV is not set; database checks skipped"
else
  hp=$(python3 -c "import os,sys;from urllib.parse import urlsplit;u=urlsplit(sys.argv[1]);print(u.hostname or '',u.port or 5432)" "$url")
  host=${hp% *}; port=${hp#* }
  if pg_isready -h "$host" -p "$port" -q 2>/dev/null; then ok "PostgreSQL reachable at $host:$port"; else bad "PostgreSQL at $host:$port not accepting connections"; fi
fi

secret=${COSMAI_SECRET_SOURCE:-$HOME/.config/cosmai/env}
[ -f "$secret" ] && ok "secret store $secret" || warn "secret store $secret missing (see env.example)"

[ "$failures" -eq 0 ] || { printf '\n%s check(s) failed\n' "$failures" >&2; exit 1; }
