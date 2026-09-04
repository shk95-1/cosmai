#!/bin/sh
# The automatic rollback for condition 5: when cutover fails, **bring the old collectors back up and
# stop the new cosmai cron**.
#
# This touches two compose projects. The old stack (`shared-db`, service/stack/) is only ever read --
# its files are never edited, this only brings back up what is already defined there through
# `docker compose up -d <service>`. The new stack (`cosmai`, this directory) only ever goes as far as
# stop: the reason it is stop and not down is that leaving the containers standing makes undoing this a
# single `up -d`, and a rollback must never take an action it cannot undo.
#
# `up -d` is not harmless. Even for an already-running container, compose **recreates** it if the
# config-hash it computes differs from that container's label -- so this prints what it will do first,
# and only moves after comparing those two hashes for each old service (--dry-run stops right after that
# comparison). A rollback that runs with the hashes disagreeing could print "the old collector is back"
# while dropping the mount that container used to read.
#
# tubedepth-worker and tubedepth-flatten carry `depends_on: tubedepth-migrate
# (service_completed_successfully)`, so that one-shot migration runs once more here. Safe, since it is an
# idempotent script, but it is an exception to "only ever reads", so it is written down.
#
#   stack/rollback.sh [--dry-run]
#   OLD_STACK_DIR=/different/path stack/rollback.sh
set -e

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

dry_run=0
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        --dry-run) dry_run=1; shift ;;
        *) echo "rollback: unknown argument: $1" >&2; exit 1 ;;
    esac
done

# A rollback that cannot run is not "unverified", it is broken: with no docker, this must not quietly
# end with exit 69 (unverified). The opposite default is right for tool/checks/*, not here -- so this
# does not even honor a REQUIRE_NATIVE=0 the shell is carrying. Honoring it would end with 69 and do
# nothing, right in the middle of an incident.
REQUIRE_NATIVE=1
export REQUIRE_NATIVE
. tool/checks/prerequisite
require_command docker

# The location of the existing stack's compose. A variable, not a literal, and the default is the
# standard layout with service/ sitting next to this repo. The process environment comes first, and
# failing that, this looks at the stack/.env an operator filled in -- env.example carries that value,
# so `cp env.example .env` and filling it in has to work here too.
if [ -z "${OLD_STACK_DIR:-}" ] && [ -f stack/.env ]; then
    OLD_STACK_DIR=$(sed -n 's/^[[:space:]]*OLD_STACK_DIR[[:space:]]*=[[:space:]]*//p' stack/.env | tail -n 1)
fi
old_stack=${OLD_STACK_DIR:-../service/stack}
old_compose=$old_stack/docker-compose.yml
new_compose=stack/docker-compose.yml

# The set cutover stopped. tubedepth-api, the two dashboards, the two postgrests and cosmai-postgres
# were never stopped by cutover, so they are left untouched here too.
old_services='trend-radar-collector tubedepth-worker tubedepth-flatten'
# Every scheduler in stack/docker-compose.yml. portal is left out -- it is static exposure, not
# collection.
new_services='collector-commerce collector-naver collector-youtube-watch collector-youtube-work collector-youtube-flatten analyze'

[ -f "$old_compose" ] || {
    echo "rollback: no compose file at $old_compose (set OLD_STACK_DIR)" >&2
    exit 1
}

old_compose_cmd() {
    # Called from that directory with no -f. Naming even one -f turns off compose's default file
    # discovery, so it never merges that same directory's docker-compose.override.yml -- and the old
    # stack is right now using exactly that file to overlay a host crontab onto trend-radar-collector
    # (#10 §A-4, with no rebuild). A config-hash computed from the unmerged file set differs from the
    # running container, turning up -d into a recreate, and the newly created container has no such
    # mount. Every guard, rehearsal, and mutation passes through this one function.
    ( CDPATH='' cd -- "$old_stack" && docker compose "$@" )
}
new_compose_cmd() {
    # --profile: collector-commerce and collector-youtube-watch sit behind a profile, so without it
    # compose does not know that name and stop fails outright -- meaning the new collectors keep running
    # through the rollback. A new profile has to be added here too, and
    # tests/stack/test_live_collection_is_behind_a_profile.py checks that pairing.
    docker compose --profile commerce --profile youtube-watch -f "$new_compose" "$@"
}

# Stop right here if a name has drifted -- the worst outcome is a rollback that prints "success" and
# revives nothing.
defined=$(old_compose_cmd config --services)
for service in $old_services; do
    printf '%s\n' "$defined" | grep -qx -- "$service" || {
        echo "rollback: no service named $service in $old_compose's file set" >&2
        exit 1
    }
done

echo "rollback: bringing the old collectors back up and stopping the new cosmai cron."
echo "  old stack : $old_stack (default discovery -- override included)"
echo "  up   -> $old_services"
echo "  new stack : $new_compose"
echo "  stop -> $new_services"
echo "  untouched : cosmai-postgres, tubedepth-api, postgrest, the dashboards, portal"
echo "  old services currently running: $(old_compose_cmd ps --services --status running | tr '\n' ' ')"

# Rehearsal. config-hash is the value that decides whether up -d "turns on an existing container" or
# "recreates it", and it becomes a recreate whenever this drifts from the file set and the running
# container.
hash_drift=0
for service in $old_services; do
    want=$(old_compose_cmd config --hash="$service" | awk '{print $2}')
    container=$(old_compose_cmd ps -aq "$service" | head -n 1)
    if [ -z "$container" ]; then
        echo "  rehearsal $service: no container -- up -d will create one ($want)"
        continue
    fi
    have=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$container")
    if [ "$want" = "$have" ]; then
        echo "  rehearsal $service: config-hash matches -- up -d just turns it on"
    else
        echo "  rehearsal $service: config-hash mismatch -- up -d will recreate it" >&2
        echo "      running container $have" >&2
        echo "      this file set     $want" >&2
        hash_drift=1
    fi
done

[ "$dry_run" = 0 ] || { echo "rollback: --dry-run, stopping here."; exit "$hash_drift"; }

status=$hash_drift
# Order matters: the new cron is stopped first, then the old collectors come up. Reversed, there is a
# window where both walk the same source at once. But the second step still runs even if the first
# fails -- the more important half of this script is reviving the old collectors, and holding that
# hostage to stop's failure would leave an incident stuck at "the new thing is unclear and the old thing
# is down too". Failures are collected and returned non-zero at the end.
echo "rollback: stopping the new schedulers"
# shellcheck disable=SC2086 -- a space-separated list of service names, passed through as-is.
new_compose_cmd stop $new_services || {
    echo "rollback: stopping the new schedulers failed -- the old collectors still come up." >&2
    status=1
}
echo "rollback: starting the old collectors"
# shellcheck disable=SC2086
old_compose_cmd up -d $old_services || {
    echo "rollback: up -d on the old collectors failed." >&2
    status=1
}

# up -d returns 0 even if a container dies right after starting. Before calling this a success, this
# checks whether all three are actually running -- a drifted name is caught by the guard above, but a
# startup failure only shows up here.
running=$(old_compose_cmd ps --services --status running || true)
for service in $old_services; do
    printf '%s\n' "$running" | grep -qx -- "$service" || {
        echo "rollback: $service is still not running after up -d." >&2
        status=1
    }
done
echo "rollback: done. old services: $(printf '%s\n' "$running" | tr '\n' ' ')"
exit "$status"
