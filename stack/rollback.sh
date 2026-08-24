#!/bin/sh
# 조건 5 의 자동 롤백: 컷오버가 실패했을 때 **구 수집기를 다시 올리고 새 cosmai 크론을 멈춘다**.
#
# 두 compose 프로젝트를 건드린다. 구 스택(`shared-db`, service/stack/docker-compose.yml)은 읽기만
# 한다 -- 그 파일을 고치지 않고 `docker compose ... up -d <서비스>` 로 이미 거기 정의된 것을 다시
# 올릴 뿐이다. 새 스택(`cosmai`, 이 디렉터리)은 stop 까지만 한다: down 이 아니라 stop 인 이유는
# 컨테이너를 남겨 두면 되돌리기가 `up -d` 한 번이고, 롤백은 되돌릴 수 없는 조치를 하면 안 되기
# 때문이다.
#
# 멱등하다. `up -d` 는 이미 도는 컨테이너에 아무것도 안 하고 `stop` 은 이미 멈춘 것에 아무것도 안
# 한다 -- 이미 롤백된 상태에서 다시 돌려도 같은 자리에 선다. 그래서 무엇을 할지 **먼저 출력**하고,
# --dry-run 은 거기서 멈춘다.
#
#   stack/rollback.sh [--dry-run]
#   OLD_STACK_DIR=/다른/경로 stack/rollback.sh
set -e

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_root"

dry_run=0
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        --dry-run) dry_run=1; shift ;;
        *) echo "rollback: unknown argument: $1" >&2; exit 1 ;;
    esac
done

# 롤백이 못 도는 것은 "확인 안 됨"이 아니라 고장이다: docker 가 없으면 exit 69(unverified)로 조용히
# 끝나게 두지 않는다. tool/checks/* 는 반대 기본값이 맞고, 여기는 아니다.
REQUIRE_NATIVE=${REQUIRE_NATIVE:-1}
export REQUIRE_NATIVE
. tool/checks/prerequisite
require_command docker

# 기존 스택 compose 의 위치. 리터럴이 아니라 변수이고, 기본값은 이 레포 옆에 service/ 가 있는 표준
# 배치다 (stack/env.example 의 OLD_STACK_DIR).
old_stack=${OLD_STACK_DIR:-../service/stack}
old_compose=$old_stack/docker-compose.yml
new_compose=stack/docker-compose.yml

# 컷오버가 멈춘 셋. tubedepth-api·대시보드 둘·postgrest 둘·shared-postgres 는 컷오버에서 멈추지
# 않았으므로 여기서도 건드리지 않는다.
old_services='trend-radar-collector tubedepth-worker tubedepth-flatten'
# stack/docker-compose.yml 의 스케줄러 전부. portal 은 수집이 아니라 정적 노출이라 뺀다.
new_services='collector-commerce collector-naver collector-youtube-watch collector-youtube-work collector-youtube-flatten analyze'

[ -f "$old_compose" ] || {
    echo "rollback: no compose file at $old_compose (set OLD_STACK_DIR)" >&2
    exit 1
}

old_compose_cmd() {
    docker compose --project-directory "$old_stack" -f "$old_compose" "$@"
}
new_compose_cmd() {
    # --profile: collector-youtube-watch 는 프로필 뒤에 있어서, 없으면 compose 가 그 이름을 모른다.
    docker compose --profile youtube-watch -f "$new_compose" "$@"
}

# 이름이 드리프트했으면 여기서 멈춘다 -- 롤백이 "성공"을 찍고 아무것도 되살리지 않는 것이 최악이다.
defined=$(old_compose_cmd config --services)
for service in $old_services; do
    printf '%s\n' "$defined" | grep -qx -- "$service" || {
        echo "rollback: $old_compose defines no service named $service" >&2
        exit 1
    }
done

echo "rollback: 구 수집기를 다시 올리고 새 cosmai 크론을 멈춘다."
echo "  구 스택 : $old_compose"
echo "  up   -> $old_services"
echo "  새 스택 : $new_compose"
echo "  stop -> $new_services"
echo "  그대로 : shared-postgres, tubedepth-api, postgrest, 대시보드, portal"
echo "  지금 도는 구 서비스: $(old_compose_cmd ps --services --status running | tr '\n' ' ')"
[ "$dry_run" = 0 ] || { echo "rollback: --dry-run, 여기서 멈춘다."; exit 0; }

# 순서가 중요하다: 먼저 새 크론을 세우고 그 다음 구 수집기를 올린다. 반대로 하면 둘이 같은 소스를
# 동시에 걷는 창이 생긴다.
echo "rollback: stopping the new schedulers"
# shellcheck disable=SC2086 -- 공백으로 나눈 서비스 이름 목록이라 그대로 넘긴다.
new_compose_cmd stop $new_services
echo "rollback: starting the old collectors"
# shellcheck disable=SC2086
old_compose_cmd up -d $old_services
echo "rollback: done. 구 서비스: $(old_compose_cmd ps --services --status running | tr '\n' ' ')"
