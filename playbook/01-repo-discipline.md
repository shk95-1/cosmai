# 01 — 저장소·브랜치·커밋 규율

네 레포 중 훅이 있는 곳은 trend-radar와 yt-scrapper 둘뿐이고, 두 레포의 `.githooks/commit-msg`·`pre-push`는
바이트 단위로 동일하다(복사본). cosmai-old와 Research_Paper에는 훅이 없다. 그 차이가 커밋 이력에 그대로 나타난다
(`metrics.md` 참조): 훅이 있는 두 레포는 Conventional Commit 준수율 121/122, 219/219, 제목 72자 초과 0건;
훅이 없는 cosmai-old는 23/191, 72자 초과 114건, 최장 150자.

| ID | 이름 | 등급 |
|---|---|---|
| R01 | commit-msg 훅 — Conventional Commits + 72자 | 채택 |
| R02 | pre-commit = format → lint → gitleaks, 정의는 `tool/checks/*` 한 곳 | 채택 |
| R03 | pre-push = 테스트, 삭제 푸시는 건너뜀 | 채택 |
| R04 | exit 69 "검증 불가" ≠ exit 1 "깨짐" (`tool/checks/prerequisite`) | 채택 |
| R05 | CI는 훅과 같은 스크립트를 돌리는 백스톱, "DB 테스트가 정말 돌았나" 가드 포함 | 변형 |
| R06 | `tool/doctor.sh` — 세션 첫 명령, hooksPath 미설정은 경고가 아니라 실패 | 채택 |
| R07 | `tool/worktree.sh` — 형제 디렉터리 `<repo>-wt/` 에 브랜치당 워크트리 | 변형 |
| R08 | 브랜치 모델: 장수 브랜치 하나, 짧은 브랜치, `--no-ff` 머지 | 변형 |
| R09 | 커밋 하나에 판단 하나, 본문은 "무엇을 막는가" | 채택 |
| R10 | 버전 단일 출처 + CHANGELOG `Unreleased` + 버전 테스트 | 변형 |
| R11 | `.gitleaks.toml` — 기본 규칙 확장 + 프로젝트 고유 비밀 패턴 | 채택 |
| R12 | Justfile — 명령 카탈로그, 실체는 `tool/checks/*`에 위임 | 제외 |

---

## R01. commit-msg 훅 — Conventional Commits + 72자 상한

- **어디서**: trend-radar `.githooks/commit-msg:1-40`, yt-scrapper 동일 파일(diff 없음).
- **무엇**: 제목이 `<type>(<scope>)!: <desc>` 패턴이 아니면 거부, `Merge `/`Revert `/`fixup!`은 통과(`:11-13`),
  72자 초과 거부(`:37-40`). 타입 목록은 고정 11개(`:15`).
- **관찰된 효과**: 훅 있는 레포는 122커밋 중 121이 패턴 일치(예외 1건은 훅 설치 전), 219/219. 제목 길이 중앙값 58·59,
  최대 72 — 상한에 정확히 붙어 있다(`git log` 실측, `metrics.md`). `git log --oneline`이 타입별로 읽히고
  `sed 's/^\([a-z]*\).*/\1/' | sort | uniq -c`로 feat 45 / docs 37 / fix 13 같은 분포가 바로 나온다.
- **관찰된 비용**: 오늘 `trend-radar-wt/review-low`에서 에이전트가 쓴 91자 제목이 이 훅에 거부되어 다시 썼다는
  보고(최종 커밋 b9ffa95 제목은 66자). 거부된 메시지는 git에 흔적이 남지 않아 재현 불가 — 훅의 비용은 항상
  이렇게 보이지 않는 곳에 쌓인다. 한국어 제목은 바이트가 아니라 문자 수로 세므로(`${#subject}`) 문제 없음.
  cosmai-old처럼 "Record the owner's GO on the batch: dev goes to main as v0.1.0" 식 서사형 제목은 이 훅에선 전부 거부된다.
- **재사용 형태**: `snippets/commit-msg` (그대로 복사, `core.hooksPath .githooks` 설정 필요).
- **등급: 채택** — 스택 무관, 40줄, 효과가 이력에서 직접 측정된다.

## R02. pre-commit = format → lint → gitleaks, 정의는 한 곳

- **어디서**: trend-radar `.githooks/pre-commit:19-27` (`run_check`가 `tool/checks/format`·`lint`를 실행, 없으면 건너뜀),
  `:34-43` gitleaks. yt-scrapper `Justfile:9-13`("Justfile이 명령을 인라인하면 네 번째 정의가 된다").
- **무엇**: 훅·CI·Justfile이 전부 `tool/checks/<name>` 스크립트를 호출한다. "clean"의 정의는 스크립트 한 곳.
  `tool/checks/format`은 `ruff format --check`(`:13`), `tool/checks/lint`는 `ruff check` + `basedpyright`(`:18-19`).
- **관찰된 효과**: trend-radar `.github/workflows/checks.yml:135-145`가 같은 세 스크립트를 같은 순서로 호출 —
  CI yml에 명령이 하나도 없다. yt-scrapper `tool/checks/*`는 trend-radar에서 복사해 `prerequisite`만 다르다.
- **관찰된 비용**: yt-scrapper `.githooks/pre-commit:29-30`은 `decisions/002-hooks-are-opt-in-so-ci-must-backstop.md`를
  가리키지만 그 파일은 없다(`decisions/`에는 001·002·003이 다른 이름으로 있음). 복사된 훅이 댕글링 참조를 같이 옮겼고,
  trend-radar는 이걸 잡으려고 `tests/test_docs_references_resolve.py`를 만들었다(`:5-7`에 그 사연). 훅 자체의 비용이 아니라
  "훅 주석에 문서 경로를 쓰는" 습관의 비용.
- **재사용 형태**: `snippets/pre-commit` + `snippets/tool-checks/{format,lint,test}`.
- **등급: 채택** — 주석에서 문서 경로 참조만 빼고 그대로.

## R03. pre-push = 테스트, 삭제 푸시는 건너뜀

- **어디서**: trend-radar `.githooks/pre-push:28-40`(stdin의 ref를 읽어 local sha가 전부 0이면 삭제 푸시로 판정).
- **무엇**: `tool/checks/test`가 있으면 돌리고 실패 시 푸시 차단. 브랜치 삭제만 하는 푸시는 테스트 생략.
- **관찰된 효과**: 머지 후 브랜치 정리 때 `--no-verify`를 안 쓰게 된다(`:20-24`의 이유). `tool/checks/test:18-19`는
  `uv sync --frozen` 후 pytest — 잠금 파일도 테스트 대상.
- **관찰된 비용**: yt-scrapper에서는 `tool/checks/test:34-69`가 Docker로 Postgres를 띄우므로 푸시마다 컨테이너 기동
  (`docker run … postgres:18-alpine`, `pg_isready` 60초 폴링). 훅 실행 시간이 수십 초. trend-radar는 2.7초.
- **재사용 형태**: `snippets/pre-push`.
- **등급: 채택** — 새 레포 테스트가 "진짜 DB"를 쓰므로 푸시 전 한 번은 맞는 비용.

## R04. exit 69 "검증 불가" ≠ exit 1 "깨짐"

- **어디서**: trend-radar `tool/checks/prerequisite:12-27`. `require_command`가 도구 부재 시 69로 종료, `REQUIRE_NATIVE=1`이면 1.
  CI `checks.yml:20-29`가 `REQUIRE_NATIVE: 1`을 설정. `tool/checks/data:29-33`도 DB URL 없으면 69.
- **무엇**: "이 호스트는 이 검사를 못 한다"와 "검사했더니 문제"를 종료 코드로 구분. 전자로 푸시가 막히면 사람은
  `--no-verify`를 배운다(`:3-7`).
- **관찰된 효과**: yt-scrapper `docs/definition-of-done.md:46-47`이 이 동작을 M0 완료 항목으로 명시. CI에서는 uv 설치
  실패가 조용한 초록이 되지 않는다(`checks.yml:27-28` "이 프로젝트의 특징적 버그는 아무 뜻 없는 초록").
- **관찰된 비용**: 없음에 가깝다. 27줄.
- **재사용 형태**: `snippets/tool-checks/prerequisite`.
- **등급: 채택**.

## R05. CI는 훅의 백스톱 — 같은 스크립트, 그리고 "정말 돌았나" 가드

- **어디서**: trend-radar `.github/workflows/checks.yml`. 핵심: `:1-8`(훅은 opt-in이라 CI가 있어야 함), `:67-79`
  gitleaks를 의존성 설치 **전에**(`.venv` 40MB 스캔 방지), `:101-133` scope.lock 비교 기준 ref 계산, `:147-165`
  "db 테스트가 실제로 돌았나"를 `-rs` 출력에서 grep.
- **무엇**: 훅과 같은 `tool/checks/*` 3개 + Postgres 서비스 컨테이너 + 스킵 감지.
- **관찰된 효과**: `:54-57` "URL 없으면 32개 db 테스트가 조용히 스킵" — 오늘 로컬 실행에서도 실제로 45개가
  `TREND_RADAR_TEST_DATABASE_URL is not set`으로 스킵됐다(`metrics.md`). 이 가드가 없으면 CI 초록이 45개 작다.
- **관찰된 비용**: 165줄 중 yaml 명령은 20줄, 나머지가 주석. scope.lock 기준 ref 계산(`:101-133`)은 scope.lock을 안 가져가면 통째로 불필요.
- **재사용 형태**: `snippets/checks.yml` (scope.lock 단계 제거, 스킵 감지 유지).
- **등급: 변형** — 스킵 감지와 "스크립트 재사용"만 가져간다. 현재 새 레포는 GitHub Actions 사용 여부 미정이라
  `tool/checks/test`가 로컬에서 컨테이너를 띄우는 yt-scrapper 방식(R03)이 CI 대체.

## R06. `tool/doctor.sh` — 세션 첫 명령

- **어디서**: yt-scrapper `tool/doctor.sh:24-30`(hooksPath 검사는 경고가 아니라 실패), `:45-71`(DB URL 파싱 + `pg_isready`),
  `AGENTS.md:11-14`("Do not skip it").
- **무엇**: 훅 활성, uv, DB 도달성, 파일시스템(drvfs) 같은 "나중에 엉뚱한 곳에서 터지는 것"을 세션 시작에 확인.
- **관찰된 효과**: `:6-10` "새 clone은 hooksPath가 없어 첫 커밋이 포맷·린트·비밀 스캔을 조용히 건너뛴다" —
  trend-radar는 doctor가 없고, 오늘 만든 `trend-radar-wt/review-low` 워크트리는 `.git` 공유 덕에 훅이 살아 있었지만
  새 clone이면 아니다.
- **관찰된 비용**: 110줄 중 절반이 WSL/SQLite 역사 주석(`:73-80`). 호스트 특이 사항이 스크립트에 박힌다.
- **재사용 형태**: `snippets/doctor.sh` (hooksPath·uv·docker·DB 4항목만).
- **등급: 채택**.

## R07. `tool/worktree.sh` — 형제 디렉터리에 워크트리

- **어디서**: trend-radar `tool/worktree.sh:10-15`(레포 안이 아니라 형제 `<repo>-wt/`에 만드는 이유: 파일 워처·LSP 중복),
  `:37-38`(`origin/<integration>`에서 `kind/name` 브랜치), `:55-62`(list가 "공유 자원은 병렬화 안 됨"을 출력).
- **관찰된 효과**: 오늘 `service/trend-radar-wt/review-low`에서 `feat/review-low` 작업이 원본 체크아웃을 건드리지 않고
  진행됐다(커밋 b9ffa95). `.git` 공유라 훅도 그대로 동작(`:14-15`).
- **관찰된 비용**: (1) `:22` `integration=${INTEGRATION_BRANCH:-dev}` — 그런데 trend-radar `AGENTS.md:79`는 "`master`가
  유일한 장수 브랜치"라고 한다. 스크립트는 yt-scrapper(master←dev)에서 복사돼 기본값이 레포 규칙과 어긋난다.
  (2) `:43-47` `tool/checks/install`을 호출하지만 그 파일은 두 레포 모두 없다(`architect/README.md` §6 #8).
  (3) `service/yt-scrapper-wt/`는 빈 디렉터리로 남아 있다(등록된 워크트리 없음).
- **재사용 형태**: `snippets/worktree.sh` (install 호출 제거, 통합 브랜치 기본값을 `main`으로).
- **등급: 변형** — 새 레포는 모노레포라 슬라이스별 병렬 세션이 더 잦을 것. 기본 브랜치만 맞춘다.

## R08. 브랜치 모델 — 장수 브랜치 하나, 짧은 브랜치, `--no-ff`

- **어디서**: trend-radar `AGENTS.md:79-85`(master만 장수, `--no-ff`, 머지 후 삭제, fast-forward 금지 이유).
  yt-scrapper `AGENTS.md:104-106`(master ← dev ← feature/fix). cosmai-old `docs/branching.md:8-14`(area/what → dev → main,
  main은 게이트에서만), `:31`("squash도 rebase도 fast-forward도 안 쓴다").
- **관찰된 효과**: 세 레포 모두 `Merge`/`chore: merge` 커밋이 경계 역할 — trend-radar `git log`에서 `chore: merge oliveyoung-ingredients (#22)`
  아래 5커밋이 한 단위로 읽힌다. revert 단위가 머지 커밋 하나.
- **관찰된 비용**: cosmai-old의 2단 통합(dev→main)은 스스로 "의례인가"를 묻고 있다(`branching.md:19-21`). yt-scrapper의
  `dev`는 릴리스 머지 `Merge dev into master for v1.3.0` 외에 역할이 없다. 단일 작업자 + 에이전트 환경에서 중간 통합 브랜치는 비용만 남는다.
- **재사용 형태**: AGENTS.md 한 단락(`snippets/AGENTS.template.md` §How we work).
- **등급: 변형** — `main` 하나 + `feat|fix/<name>` + `--no-ff`. `dev` 없음.

## R09. 커밋 하나에 판단 하나, 본문은 "무엇을 막는가"

- **어디서**: trend-radar `AGENTS.md:111-112`, `docs/working-agreements.md:134-140`("diff가 무엇을 했는지 말한다. 본문은 왜 이 형태여야 하고
  다른 형태면 무엇이 깨지는가"; 큰따옴표 있으면 `git commit -F`).
- **관찰된 효과**: trend-radar ba11c24 본문("Minor, not patch: … Stops the ingredients scope from identifying itself as Unreleased")이
  그 예. 오늘 b9ffa95 본문 4단락이 `review_low`가 왜 `review`와 다른 걷기인지, 페이지 상한을 왜 요청에 실었는지를 적어
  `architect/REBUILD.md` §3의 변경 1호 근거가 됐다.
- **관찰된 비용**: 강제 불가(`working-agreements.md:184` "커밋 내용 ✗"). 에이전트가 쓴 본문은 길어지는 경향 — yt-scrapper
  165/219, cosmai-old 185/191이 Claude 공동 저자인데 cosmai-old 제목 중앙값이 75자(훅 없음)로 본문 내용이 제목으로 새어 나온다.
- **재사용 형태**: AGENTS.md 두 줄 + R01 훅이 제목 길이를 잡아 준다.
- **등급: 채택**.

## R10. 버전 단일 출처 + CHANGELOG `Unreleased` + 버전 테스트

- **어디서**: trend-radar `tests/test_version_is_managed.py:1-10`(pyproject가 출처, CLI와 CHANGELOG가 파생), `:34-47`(AST로 하드코딩 탐지,
  `len(modules) > 20` 공허성 가드). `CHANGELOG.md:3-5`("이유는 커밋 메시지에, 결정은 judgment-debt에, 이 파일은 둘 다 아님").
  yt-scrapper `docs/releasing.md:3-6`(`__init__.py` 한 곳), `CHANGELOG.md:9-17`(패키지 버전과 `/v1` 계약 버전은 별개).
- **관찰된 효과**: `chore(release): prepare 1.1.0` 한 커밋으로 릴리스. 행마다 `collector_version`이 붙어 CHANGELOG 헤딩으로 돌아간다
  (`test_collection_scope_is_recorded.py:89-96`).
- **관찰된 비용**: Research_Paper `docs/judgment-debt.md:22`는 CHANGELOG를 **안 만들기로** 결정했다 — "git 이력과 원장이 이미 그 역할,
  두 출처가 어긋날 위험만". 실제 어긋남: yt-scrapper `CHANGELOG.md`는 506줄·`.ko.md` 번역본까지 두 벌. 외부 소비자가 없는 레포에서는
  `Unreleased` 절이 scope.lock 테스트의 인질이 된다(S01).
- **재사용 형태**: `snippets/test_version_is_managed.py` (AST 하드코딩 탐지만).
- **등급: 변형** — 버전은 pyproject 한 곳 + 테스트. CHANGELOG는 외부 소비자(PostgREST 스키마 소비자 등)가 생길 때까지 두지 않는다.
  행에 `collector_version`을 남기는 것(S02)은 유지.

## R11. `.gitleaks.toml` — 기본 규칙 확장 + 프로젝트 고유 패턴

- **어디서**: yt-scrapper `.gitleaks.toml:11-22`(`useDefault = true` + WireGuard 키 regex + 픽스처 allowlist). 훅 `pre-commit:34-43`,
  CI `checks.yml:67-79`(전체 이력 스캔, 바이너리 직접 내려받음 — gitleaks-action은 조직 라이선스 요구).
  `tests/test_repository_hygiene.py:26-30`이 같은 패턴을 테스트로 한 번 더 본다(서명된 googlevideo URL, WireGuard 키).
- **관찰된 효과**: yt-scrapper `AGENTS.md:53-57` "만료되는 URL을 픽스처에 두면 gitleaks가 자격증명으로 읽는다" — 훅이 실제로 걸린 사례.
- **관찰된 비용**: 훅 + 테스트 + CI 세 겹이 같은 regex를 들고 있다(`test_repository_hygiene.py:29` ≡ `.gitleaks.toml:17`).
- **재사용 형태**: `snippets/gitleaks.toml` (기본 확장 + 새 레포의 `COSMA_SRC_*=` 값 패턴).
- **등급: 채택** — 훅 한 겹만. 테스트 중복은 안 가져간다.

## R12. Justfile — 명령 카탈로그

- **어디서**: yt-scrapper `Justfile:16-44`(doctor/check/format/lint/test가 전부 `tool/checks/*` 위임), `:133-150`(픽스처 캡처),
  `:161-166`(`update-ytdlp`).
- **관찰된 효과**: `just --list`가 런북 역할(`definition-of-done.md:49`). 주석으로 "왜 이 플래그인가"가 레시피 옆에 있다(`:93-104`).
- **관찰된 비용**: 도구 하나 더(just). `:77-82` "예전에 `serve --with-worker`를 약속하는 `dev` 레시피가 있었는데 그 옵션은 존재한 적이 없다" —
  레시피도 썩는다. trend-radar는 Justfile 없이 `tool/checks/*`만으로 같은 일을 한다.
- **등급: 제외** — `tool/checks/*` 셸 스크립트 + README 한 표로 충분. 카탈로그가 필요해지면 그때.
