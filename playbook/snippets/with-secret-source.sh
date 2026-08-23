#!/usr/bin/env bash
# origin: service/cosmai/scripts/with-secret-source.sh (cosmai-old)
# reuse: copy; rename COSMA_SECRET_SOURCE -> your var; default path ~/.config/<project>/env. Exports the PATH only, never values.
#
# Run a command with the Cosmai secret store location validated and exported.
#
#   ./scripts/with-secret-source.sh uv run pytest
#   ./scripts/with-secret-source.sh uv run python -m cosma.worker
#
# This script exports COSMAI_SECRET_SOURCE, the path of the store. It deliberately
# does NOT read or export credential values: nothing inherited by a child process
# can leak a credential into a bundler, a traceback, or an environment dump.
# Values are resolved on demand at the point of use.
#
# See README.md (secrets section).

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
secret_file=${COSMAI_SECRET_SOURCE:-$HOME/.config/cosmai/env}

if [ "$#" -eq 0 ]; then
  echo "usage: ${0##*/} <command> [args...]" >&2
  exit 64
fi

if [ ! -f "$secret_file" ]; then
  cat >&2 <<EOF
error: secret store not found: $secret_file

Create it outside the repository, then restrict its permissions:

  mkdir -p ~/.config/cosmai
  touch ~/.config/cosmai/env
  chmod 600 ~/.config/cosmai/env

Key names are documented in config/env.example.
Procedure: README.md (secrets section)
EOF
  exit 78
fi

secret_real="$(cd "$(dirname "$secret_file")" && pwd -P)/$(basename "$secret_file")"

# Structural invariant: credentials never live inside the working tree.
# See README.md.
case "$secret_real" in
  "$repo_root" | "$repo_root"/*)
    echo "error: secret store is inside the repository working tree: $secret_real" >&2
    echo "move it outside the repository before running this command" >&2
    exit 78
    ;;
esac

# Assign in separate branches: `$(gnu || bsd)` would concatenate the output of a
# partially-failing first attempt with the output of the fallback.
if perms=$(stat -c '%a' "$secret_real" 2>/dev/null); then
  : # GNU coreutils
elif perms=$(stat -f '%Lp' "$secret_real" 2>/dev/null); then
  : # BSD stat
else
  echo "error: cannot determine permissions of $secret_real" >&2
  exit 78
fi

case "$perms" in
  600 | 400) ;;
  *)
    echo "error: $secret_real must be mode 600 or 400 (found $perms)" >&2
    echo "run: chmod 600 $secret_real" >&2
    exit 78
    ;;
esac

export COSMAI_SECRET_SOURCE="$secret_real"

exec "$@"
