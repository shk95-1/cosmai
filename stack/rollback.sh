#!/bin/sh
# 조건 5 의 자동 롤백: 컷오버가 실패했을 때 **구 수집기를 다시 올리고 새 cosmai 크론을 멈춘다**.
#
# 두 compose 프로젝트를 건드린다. 구 스택(`shared-db`, service/stack/)은 읽기만 한다 -- 그 파일들을
# 고치지 않고 `docker compose up -d <서비스>` 로 이미 거기 정의된 것을 다시 올릴 뿐이다. 새 스택
# (`cosmai`, 이 디렉터리)은 stop 까지만 한다: down 이 아니라 stop 인 이유는 컨테이너를 남겨 두면
# 되돌리기가 `up -d` 한 번이고, 롤백은 되돌릴 수 없는 조치를 하면 안 되기 때문이다.
#
# `up -d` 는 무해하지 않다. 이미 도는 컨테이너라도 compose 가 계산한 config-hash 가 그 컨테이너의
# 라벨과 다르면 **recreate** 한다 -- 그래서 무엇을 할지 먼저 출력하고, 각 구 서비스에 대해 그 두 해시를
# 대조한 뒤에야 움직인다(--dry-run 은 그 대조까지만 하고 멈춘다). 해시가 어긋난 채로 도는 롤백은
# "구 수집기가 살아났다"고 찍으면서 그 컨테이너가 읽던 마운트를 떨어뜨릴 수 있다.
#
# tubedepth-worker·tubedepth-flatten 은 `depends_on: tubedepth-migrate
# (service_completed_successfully)` 를 달고 있어서, 그 원샷 마이그레이션이 여기서 한 번 더 돈다.
# 멱등한 스크립트라 안전하지만 "읽기만 한다"의 예외이므로 적어 둔다.
#
#   stack/rollback.sh [--dry-run]
#   OLD_STACK_DIR=/다른/경로 stack/rollback.sh
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

# 롤백이 못 도는 것은 "확인 안 됨"이 아니라 고장이다: docker 가 없으면 exit 69(unverified)로 조용히
# 끝나게 두지 않는다. tool/checks/* 는 반대 기본값이 맞고, 여기는 아니다 -- 그래서 셸이 들고 있는
# REQUIRE_NATIVE=0 도 존중하지 않는다. 존중하면 장애 한가운데서 아무것도 안 하고 69 로 끝난다.
REQUIRE_NATIVE=1
export REQUIRE_NATIVE
. tool/checks/prerequisite
require_command docker

# 기존 스택 compose 의 위치. 리터럴이 아니라 변수이고, 기본값은 이 레포 옆에 service/ 가 있는 표준
# 배치다. 프로세스 환경이 먼저이고, 없으면 운영자가 채운 stack/.env 를 본다 -- env.example 이 그
# 값을 담고 있으므로 `cp env.example .env` 하고 채운 것이 여기서도 통해야 한다.
if [ -z "${OLD_STACK_DIR:-}" ] && [ -f stack/.env ]; then
    OLD_STACK_DIR=$(sed -n 's/^[[:space:]]*OLD_STACK_DIR[[:space:]]*=[[:space:]]*//p' stack/.env | tail -n 1)
fi
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
    # -f 를 주지 않고 그 디렉터리에서 부른다. -f 를 하나라도 명시하면 compose 는 기본 파일 탐색을 꺼서
    # 같은 디렉터리의 docker-compose.override.yml 을 병합하지 않는다 -- 그리고 구 스택은 지금 바로
    # 그 파일로 trend-radar-collector 에 호스트 크론탭을 덮고 있다(#10 §A-4, 재빌드 없이). 병합하지
    # 않은 파일 집합으로 계산한 config-hash 는 도는 컨테이너와 달라서 up -d 가 recreate 로 바뀌고,
    # 새로 만든 컨테이너에는 그 마운트가 없다. 가드도 리허설도 조작도 전부 이 함수를 지난다.
    ( CDPATH='' cd -- "$old_stack" && docker compose "$@" )
}
new_compose_cmd() {
    # --profile: collector-youtube-watch 는 프로필 뒤에 있어서, 없으면 compose 가 그 이름을 모른다.
    docker compose --profile youtube-watch -f "$new_compose" "$@"
}

# 이름이 드리프트했으면 여기서 멈춘다 -- 롤백이 "성공"을 찍고 아무것도 되살리지 않는 것이 최악이다.
defined=$(old_compose_cmd config --services)
for service in $old_services; do
    printf '%s\n' "$defined" | grep -qx -- "$service" || {
        echo "rollback: $old_compose 의 파일 집합에 $service 라는 서비스가 없다" >&2
        exit 1
    }
done

echo "rollback: 구 수집기를 다시 올리고 새 cosmai 크론을 멈춘다."
echo "  구 스택 : $old_stack (기본 탐색 -- override 포함)"
echo "  up   -> $old_services"
echo "  새 스택 : $new_compose"
echo "  stop -> $new_services"
echo "  그대로 : shared-postgres, tubedepth-api, postgrest, 대시보드, portal"
echo "  지금 도는 구 서비스: $(old_compose_cmd ps --services --status running | tr '\n' ' ')"

# 리허설. up -d 가 "있는 컨테이너를 켜는" 것인지 "다시 만드는" 것인지를 정하는 값이 config-hash 이고,
# 그것이 이 파일 집합과 도는 컨테이너 사이에서 어긋나면 recreate 다.
hash_drift=0
for service in $old_services; do
    want=$(old_compose_cmd config --hash="$service" | awk '{print $2}')
    container=$(old_compose_cmd ps -aq "$service" | head -n 1)
    if [ -z "$container" ]; then
        echo "  리허설 $service: 컨테이너가 없다 -- up -d 가 새로 만든다 ($want)"
        continue
    fi
    have=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$container")
    if [ "$want" = "$have" ]; then
        echo "  리허설 $service: config-hash 일치 -- up -d 는 그대로 켠다"
    else
        echo "  리허설 $service: config-hash 불일치 -- up -d 가 recreate 한다" >&2
        echo "      도는 컨테이너 $have" >&2
        echo "      이 파일 집합   $want" >&2
        hash_drift=1
    fi
done

[ "$dry_run" = 0 ] || { echo "rollback: --dry-run, 여기서 멈춘다."; exit "$hash_drift"; }

status=$hash_drift
# 순서가 중요하다: 먼저 새 크론을 세우고 그 다음 구 수집기를 올린다. 반대로 하면 둘이 같은 소스를
# 동시에 걷는 창이 생긴다. 다만 앞이 실패해도 뒤는 돈다 -- 이 스크립트의 더 중요한 절반은 구 수집기를
# 되살리는 쪽이고, 그것을 stop 의 실패에 인질로 잡히게 두면 장애가 "새 것도 애매, 구 것도 down" 에서
# 멈춘다. 실패는 모아서 마지막에 non-zero 로 낸다.
echo "rollback: stopping the new schedulers"
# shellcheck disable=SC2086 -- 공백으로 나눈 서비스 이름 목록이라 그대로 넘긴다.
new_compose_cmd stop $new_services || {
    echo "rollback: 새 스케줄러 stop 이 실패했다 -- 구 수집기는 그대로 올린다." >&2
    status=1
}
echo "rollback: starting the old collectors"
# shellcheck disable=SC2086
old_compose_cmd up -d $old_services || {
    echo "rollback: 구 수집기 up -d 가 실패했다." >&2
    status=1
}

# up -d 는 컨테이너가 뜬 직후 죽어도 0 을 돌려준다. 되살렸다고 말하기 전에 셋이 실제로 running 인지
# 본다 -- 이름 드리프트는 위 가드가 잡지만, 기동 실패는 여기서만 보인다.
running=$(old_compose_cmd ps --services --status running || true)
for service in $old_services; do
    printf '%s\n' "$running" | grep -qx -- "$service" || {
        echo "rollback: $service 가 up -d 뒤에도 running 이 아니다." >&2
        status=1
    }
done
echo "rollback: done. 구 서비스: $(printf '%s\n' "$running" | tr '\n' ' ')"
exit "$status"
