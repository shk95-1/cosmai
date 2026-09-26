#!/bin/sh
# Emergency collection pause after #181. Retained viewers and both databases remain running.
# Historical collectors are never revived. Provider recovery is documented in stack/README.md.
# Usage: stack/rollback.sh [--dry-run]
set -e
repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"
dry_run=0
case "${1:-}" in
    '') ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) echo 'usage: stack/rollback.sh [--dry-run]'; exit 0 ;;
    *) echo 'rollback: unknown argument' >&2; exit 1 ;;
esac
[ "$#" -eq 0 ] || { echo 'rollback: unexpected argument' >&2; exit 1; }
REQUIRE_NATIVE=1
export REQUIRE_NATIVE
. tool/checks/prerequisite
require_command docker
new_services='collector-commerce collector-naver collector-youtube-watch collector-youtube-work collector-youtube-flatten analyze'
compose() {
    docker compose --profile commerce --profile youtube-watch -f stack/docker-compose.yml "$@"
}
defined=$(compose config --services)
for service in $new_services; do
    printf '%s\n' "$defined" | grep -qx -- "$service" || {
        echo "rollback: no service named $service" >&2
        exit 1
    }
done
echo "rollback: pause schedulers: $new_services"
echo 'rollback: viewers, Docker PostgreSQL and the independent Run snapshot stay running.'
[ "$dry_run" -eq 0 ] || exit 0
compose stop $new_services
