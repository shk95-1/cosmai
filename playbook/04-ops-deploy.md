# 04 — 운영·배포

오늘(2026-08-23) 실제 런타임은 `service/stack/docker-compose.yml` 하나(postgres:18 + 컨테이너 14개). yt-scrapper의 systemd 유닛은
stop+disable 된 채 파일만 남아 있고(`stack/README.md:33-34`), trend-radar는 supercronic 컨테이너로 돈다. 따라서 이 섹션의 "효과"는
compose 쪽에서, "비용"은 두 배선 체계가 공존하며 생긴 누락에서 관찰된다.

| ID | 이름 | 등급 |
|---|---|---|
| O01 | 3롤 DB bootstrap — owner NOLOGIN / migrator / runtime, default privileges, 롤별 타임아웃, UTC | 채택 |
| O02 | 멱등 init 스크립트 (`SELECT … WHERE NOT EXISTS \gexec`) | 채택 |
| O03 | compose 한 파일: `${VAR:-default}`, `.env`는 git 밖, `depends_on` healthy/completed, migrate 원샷 | 채택 |
| O04 | systemd 유닛·타이머 — 옵션마다 이유, 샌드박스, `Persistent`/`RandomizedDelaySec` | 변형 |
| O05 | 비밀 저장소는 트리 밖 (`with-secret-source.sh`, `env.example`은 이름만, pytest sessionstart 가드) | 채택 |
| O06 | 헬스체크는 compose에, 이미지에는 `HEALTHCHECK` 없음, `--wait` | 채택 |
| O07 | 크론은 이미지 안 supercronic, UTC, 정시 | 채택 |
| O08 | `flake.nix` 선택적 devshell | 제외 |
| O09 | 스키마 이관 스크립트 (dump → 재생성 → restore → GRANT → 행수 비교) | 변형 |

---

## O01. 3롤 DB bootstrap

- **어디서**: yt-scrapper `deploy/postgres-bootstrap.sql:22-37`(owner NOLOGIN, migrator가 `SET ROLE` owner, runtime은 DML만), `:43-49`
  (`REVOKE CREATE ON SCHEMA public FROM PUBLIC` — 이게 없으면 스키마 분리는 이름 관례), `:64-74`(`ALTER DEFAULT PRIVILEGES` — 없으면
  다음 마이그레이션이 만든 테이블을 runtime이 못 본다, "배포 중이 아니라 배포 후 첫 요청에서 실패"), `:118-131`(statement/lock/idle/transaction
  타임아웃을 롤에), `:133-142`(TimeZone UTC — 실측된 `timestamptz→timestamp` 오프셋 사고), `:153`(CONNECTION LIMIT).
  trend-radar는 4롤(`service-db.json:6-11`, `reader` 추가 — 대시보드용, `stack/docker-compose.yml:171` "serve는 기동 시 이 롤이 못 쓴다는 것을 확인").
  cosmai-old `apps/db/provision.sql`, `stack/init/50-cosmai-bootstrap.sh:41-70`. 규칙 원문 `yt-scrapper/docs/shared-postgres.md`(695줄, 규칙 0~14).
- **관찰된 효과**: 런타임 DDL이 **DB 수준에서** 불가능(T11 테스트가 증명). `test_no_ddl_on_the_boot_path.py:7-10`의 `duplicate column` 사고 재발 없음.
  스키마 4개(trend_radar, tubedepth, cosmai, +needs 예정)가 한 database에 세입자로 공존.
- **관찰된 비용**: (1) 규칙 문서 695줄 + 레포별 bootstrap 중복(trend-radar `tool/db/docker-init.sh` 127줄, yt 153줄, cosmai 119줄 — 같은 패턴 세 벌).
  (2) `search_path` 전략 분기: yt는 migrator도 `tubedepth, pg_catalog`(`:107-116` 마이그레이션이 스키마 비한정이라), cosmai는 `pg_catalog`만(`50-cosmai-bootstrap.sh:62`).
  한 레포에서 통일해야 한다. (3) 테스트 하네스에 `GRANT CREATE ON DATABASE` 같은 예외가 필요(T03).
- **재사용 형태**: `snippets/postgres-bootstrap.sql` (스키마명을 psql 변수로 받는 멱등 3롤 템플릿, 70줄). 새 레포 `db/`에 스키마당 호출 한 번.
- **등급: 채택** — 소유자 제약. `reader` 롤은 PostgREST 노출용으로 4번째로 둔다(trend-radar 방식).

## O02. 멱등 init 스크립트

- **어디서**: `stack/init/50-cosmai-bootstrap.sh:41-57`(`SELECT 'CREATE ROLE …' WHERE NOT EXISTS (…) \gexec`), `:17-29`(비밀번호는 롤 생성 때만 —
  매번 `ALTER ROLE … PASSWORD`하면 `~/.config/cosmai/env`가 조용히 틀린 값이 된다), `:59-60`(세션 기본값은 매번 SET해도 안전).
  반례: `stack/init/30`(yt-scrapper bootstrap)은 `IF NOT EXISTS` 없는 `CREATE ROLE/SCHEMA`라 재실행 불가(`architect/README.md` §6 #4).
- **관찰된 효과**: 이미 초기화된 클러스터에 `docker exec … bash /docker-entrypoint-initdb.d/50-…sh`로 수동 재실행이 가능했다(`stack/README.md:62-66`) —
  첫 실행부터 "재실행 경로"를 탔다(`:6-8`).
- **재사용 형태**: `snippets/postgres-bootstrap.sql`이 이 형태.
- **등급: 채택**.

## O03. compose 한 파일 + `.env` 밖 + 의존 조건

- **어디서**: `stack/README.md:48-61`(한 파일에 전부, `${VAR:-상대경로}` 기본값이 곧 표준 배치 문서, 구조 변경은 gitignored `override.yml`),
  `stack/docker-compose.yml:135-139`(pg_isready 헬스체크), `:147-149, 240-242`(`service_healthy` / `service_completed_successfully` — migrate 원샷 후 기동),
  `:152`(`TREND_RADAR_DATABASE_URL … :?`로 비밀 누락 시 기동 거부), `:174-175`(대시보드는 루프백 바인드, 넓히는 건 변수로만).
  yt-scrapper `Justfile:92-109`(`--build --wait`의 이유).
- **관찰된 효과**: 재부팅 후 순서를 사람이 기억하지 않는다(`stack/README.md:50-51` data-portal 편입 이유). cosmai의 `network_mode: host` 제거·db-net 편입이
  compose 한 파일 수정으로 끝났다(stack 커밋 42dc88d).
- **관찰된 비용**: 레포 안 compose(`trend-radar/docker-compose.deploy.yml`, `yt-scrapper/deploy/docker-compose.yml`)와 stack compose가 **두 벌** —
  T10의 compose 테스트는 레포 안 것만 본다. 이 이중화가 오늘 P16의 크론 누락 원인(`slice-p16…/README.md:46`).
  이미지 태그 미고정(`postgres:18`, `postgrest:latest`), 죽은 키 `TREND_RADAR_LEGACY_HOST_PASSWORD`(`architect/README.md` §6 #4).
- **재사용 형태**: 새 레포 `stack/docker-compose.yml` **하나만**. 레포별 compose 없음. `snippets/compose-service.yml`(서비스 블록 템플릿: depends_on·`:?`·루프백·healthcheck).
- **등급: 채택**.

## O04. systemd 유닛·타이머

- **어디서**: yt-scrapper `deploy/tubedepth-worker.service:16-22`(PATH를 명시하는 이유 — 유저 유닛은 셸 PATH를 상속 안 함, exit 127 실측), `:24-35`
  (URL은 자격증명이라 0600 EnvironmentFile), `:70-80`(`--poll 5` — Restart=always가 프로세스 기동 루프가 되어 10초마다 520ms CPU·68MB 실측),
  `:88-92`(SIGINT — 리스 반납), `:94-104`(샌드박스와 `ReadWritePaths` 세 경로). `deploy/tubedepth-watch.timer:17-29`(`OnBootSec=5min` 워커보다 먼저 큐잉 방지,
  `Persistent=true` 노트북 suspend, `RandomizedDelaySec` 정시 회피).
- **관찰된 효과**: 유닛 파일이 운영 결정의 기록이 됐다 — 각 옵션이 실측 수치를 들고 있다.
- **관찰된 비용**: 오늘 stop+disable 상태(`stack/README.md:33-34`). 107줄 중 실행 줄은 20줄. 같은 설명이 `docs/status.md:1142-1174`("The listing cap is a
  deployment setting, and both units must agree")에 또 있다. api/worker 유닛이 같은 상한 값을 **두 번** 들고 있어야 하는 구조(`:36-42`) 자체가 비용.
- **재사용 형태**: 유닛은 가져가지 않는다. "옵션 옆에 실측 한 줄" 관행만 crontab/compose 주석으로(`snippets/crontab`).
- **등급: 변형**.

## O05. 비밀 저장소는 트리 밖

- **어디서**: cosmai-old `scripts/with-secret-source.sh:8-11`(경로만 export, 값은 export 안 함 — 자식 프로세스·트레이스백·env dump로 새지 않게), `:43-51`
  (저장소가 워킹트리 안이면 거부), `:55-71`(모드 600/400 강제), `config/env.example:3-6, 20-24`(키 이름만 커밋, `credential_ref` = 키 이름),
  `tests/conftest.py:58-73`(`pytest_sessionstart`에서 같은 검사를 **플랫폼 코드의 함수로** 호출 — 두 벌이면 어긋나는 쪽이 누수).
  yt-scrapper `AGENTS.md:72-76`(WireGuard 키 `~/.config/tubedepth/`, `.gitignore`는 방어가 아니라 백스톱), `stack/README.md:52-54`(`.env` 600, git 밖, `env.example`).
- **관찰된 효과**: 네 레포 + stack 전부에서 git 이력에 자격증명 0건(gitleaks CI가 전체 이력 스캔, `checks.yml:60-79`). `~/.config/cosmai/env`가 새 레포 README 원칙 5에 그대로.
- **관찰된 비용**: 실행 명령이 길어진다(`./scripts/with-secret-source.sh uv run pytest`). cosmai-old는 `.envrc`+direnv도 같이 둬서 경로가 둘.
- **재사용 형태**: `snippets/with-secret-source.sh`(75줄 그대로, 변수명만 `COSMAI_SECRET_SOURCE`) + `snippets/env.example`.
- **등급: 채택**.

## O06. 헬스체크는 compose에, `--wait`

- **어디서**: `stack/docker-compose.yml:135-139, 243`(pg_isready / API healthz), yt-scrapper `deploy/docker-compose.yml:210-212`("이미지는 HEALTHCHECK을 일부러
  안 가진다 — compose가 결정"), `Justfile:102-104`(`--wait`로 "올라오지 않은 스택 = 명령 실패").
- **관찰된 효과**: `docker compose ps`를 읽어야 아는 상태가 명령 종료 코드가 된다.
- **등급: 채택** — 새 레포 `stack/`에 그대로.

## O07. 크론은 이미지 안 supercronic, UTC, 정시

- **어디서**: trend-radar `docker/crontab:1-10`(TZ 없음 = UTC = `captured_at` 버킷과 같은 시계; 정시 0분 — 같은 시간 안 재실행이 no-op upsert),
  `Dockerfile`, `CHANGELOG.md:25-27`. 반례: `NOTES.local.md:28-30` "07:00 `up -d`가 정시 런을 중간에 잘랐다 — 다음부터 재기동은 정시를 피할 것".
- **관찰된 효과**: 140 run 중 ranking 69 ok / 5 partial(`slice-p16…/README.md:19`). 시간 버킷 = 실행 단위라 재실행이 멱등.
- **관찰된 비용**: crontab이 이미지 안에 있어 스택에서 데이터셋을 추가하려면 이미지 재빌드. 오늘 누락된 크론 3줄이 그 비용의 실물.
- **재사용 형태**: `snippets/crontab`(UTC·정시·exit code 의미 주석) + T10 변형 테스트("enum 멤버마다 크론 줄").
- **등급: 채택** — 단 crontab 파일은 `stack/`에 두고 볼륨 마운트(이미지 재빌드 없이 배선 변경).

## O08. `flake.nix`

- **어디서**: cosmai-old `flake.nix:1-2`("선택적. 지원 경로는 uv"), `:22-30`(python/ruff를 nix에 넣지 않는 이유 — `.venv` 재생성 실측).
- **등급: 제외** — uv만으로 충분했고, 이 호스트는 nix profile로 uv를 받지만 그건 호스트 설정이지 레포 설정이 아니다.

## O09. 스키마 이관 스크립트

- **어디서**: `stack/migrate-trend-radar.sh`, `migrate-tubedepth.sh`(`stack/README.md:67-68`: dump → 스키마 재생성 → `--schema --no-acl` restore → 정책 GRANT → 행수 비교),
  trend-radar `tool/checks/extraction:9-24`(파괴적 리허설은 `_test`/`_extraction` 접미사 + 이름 반복 확인).
- **관찰된 효과**: public→`trend_radar` 스키마, 5432→5434 이관이 행수 비교까지 스크립트로 끝났다(stack 커밋 27d36e0, 9f0282a).
- **등급: 변형** — 새 레포는 기존 스키마를 **그대로** 옮기므로 이 스크립트를 한 번 더 쓴다. 이후 보관 안 함.
