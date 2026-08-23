#!/bin/sh
# origin: service/trend-radar/tool/worktree.sh (the install hook call removed -- it pointed at a file that never existed)
# reuse: set INTEGRATION_BRANCH default to your long-lived branch. Worktrees land in ../<repo>-wt/, never inside the repo.
set -e
cd "$(dirname "$0")/.." || exit 1
root=$(pwd)
wt_root="$(dirname "$root")/$(basename "$root")-wt"
integration=${INTEGRATION_BRANCH:-main}

usage() { echo "usage: tool/worktree.sh new <name> [feat|fix] | list | done <name>"; exit 1; }

case "${1:-}" in
  new)
    name=${2:?"name required"}; kind=${3:-feat}
    case "$kind" in feat|fix) ;; *) echo "kind must be feat or fix" >&2; exit 1 ;; esac
    git fetch -q origin "$integration" 2>/dev/null || true
    base="origin/$integration"; git rev-parse -q --verify "$base" >/dev/null || base="$integration"
    git worktree add -b "$kind/$name" "$wt_root/$kind-$name" "$base"
    echo "Worktree ready:  cd $wt_root/$kind-$name   (branch $kind/$name from $base)"
    echo "Hooks carry over via the shared .git; run uv sync --extra dev --frozen there first."
    ;;
  list)
    git worktree list
    echo "Shared resources do not parallelise: the stack's Postgres migrations, a bound port, one fixed data path."
    ;;
  done)
    name=${2:?"name required"}
    dir=$(git worktree list --porcelain | sed -n 's/^worktree //p' | grep -E "/(feat|fix)-${name}\$" | head -1)
    [ -n "$dir" ] || { echo "no worktree matching '$name'" >&2; exit 1; }
    git worktree remove "$dir" && echo "Removed $dir. The branch is kept; delete it after the merge."
    ;;
  *) usage ;;
esac
