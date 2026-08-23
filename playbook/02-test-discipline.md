# 02 — 테스트 규율

실측(`metrics.md`): trend-radar 1,152 passed / 45 skipped(db) / 11 deselected(live) in **2.7s**; Research_Paper 805 passed in **1.3s**;
yt-scrapper 632 collected(4 live, 31 postgres 마커); cosmai-old `apps/` 1,199 collected. 네 레포 모두 기본 실행이 오프라인이고
그 이유는 같다 — `conftest.py`가 소켓을 막는다. 빠른 이유도 같다 — 파서는 순수 함수이고 픽스처는 저장된 바이트다.

| ID | 이름 | 등급 |
|---|---|---|
| T01 | 소켓 차단 autouse conftest (`live` 마커 예외, 테스트 DB 포트 허용) | 채택 |
| T02 | 가드의 가드 — 차단이 실제로 막는지 검증하는 테스트 | 채택 |
| T03 | 진짜 Postgres, 테스트당 스키마 (`database_url_for_tests`) | 채택 |
| T04 | `tool/checks/test`가 일회용 Postgres 컨테이너를 띄우고 **프로덕션 bootstrap SQL**로 초기화 | 채택 |
| T05 | AST 계층 가드 — "소스는 아래로만 import" | 채택 |
| T06 | 공허성 가드 — "검사할 것이 있었다"를 먼저 단언 | 채택 |
| T07 | 골든 파일 바이트 동일성 (합성 픽스처 → CSV 5개) | 채택 |
| T08 | 픽스처 스크럽 테스트 — 커밋된 캡처에 실명·프로필키 없음 | 채택 |
| T09 | 문서 진실성 메타테스트 (doc-truth, 번역 동기화, 참조 실재, 컨텍스트 파일 도메인 금지) | 제외 |
| T10 | 설정 진실성 테스트 (compose/systemd 명령줄이 `--help`에 있는가) | 변형 |
| T11 | 부팅 경로 DDL 금지 + runtime 롤 권한 음성 증명 | 채택 |
| T12 | payload shape lock — append-only, bump 강제 | 변형 |
| T13 | 적재된 행 검사 (`tool/checks/data`: hygiene / placeholder 두 계열) | 채택 |
| T14 | live/contract 검사 분리 — 사람이 돌린다 | 채택 |
| T15 | 서브프로세스로 워커 띄우는 테스트 + openssl TLS 스텁 | 변형 |
| T16 | 수용 시나리오 마크다운 (`tests/acceptance/JOB-001…`) | 제외 |
| T17 | 대시보드/뷰 경계 테스트 — 컴파일된 쿼리 객체 검사 | 변형 |

---

## T01. 소켓 차단 autouse conftest

- **어디서**: trend-radar `tests/conftest.py:27-41`(connect/connect_ex/create_connection 세 개 패치, 메시지에 nodeid),
  yt-scrapper `tests/conftest.py:70-104`(`LOCAL_DATABASE_HOSTS` × `TUBEDEPTH_TEST_POSTGRES_URL`의 **포트**만 통과, `:37-51`에 포트로 좁힌 이유),
  Research_Paper `tests/conftest.py:26-39`(같은 세 개 패치, 한국어 메시지), cosmai-old `tests/conftest.py:31-55`(차단이 아니라
  `--run-network` 플래그 없으면 `network` 마커 스킵 — 약한 변형).
- **무엇**: `live` 마커가 없는 테스트가 소켓을 열면 RuntimeError. 실패 메시지가 테스트 이름을 담는다.
- **관찰된 효과**: 세 레포 합계 2,600여 테스트가 네트워크 없이 5초 안에 끝난다. trend-radar `tests/conftest.py:8-11`
  "파서가 조용히 요청을 키우는 것이 이 규칙이 깨지는 방식이고, 이게 없으면 증상은 '느려지고 가끔 빨개지는 스위트'".
  `pyproject.toml:71-78` `-m "not live"`가 addopts — "취향이 아니라 load-bearing".
- **관찰된 비용**: DB를 쓰는 테스트는 예외 경로가 필요하다. trend-radar `tests/storage/conftest.py:35-40`은 `db` 마커에서
  `monkeypatch.undo()`로 통째로 푼다(전부 허용); yt-scrapper는 포트 하나만 연다. 후자가 맞다 — 전자는 `db` 테스트 안에서
  실제 사이트로 나가도 모른다. yt-scrapper 주석 `:41-43` "Task 7이 '로컬 호스트 아무 포트'로 넓혔다가 되돌렸다".
- **재사용 형태**: `snippets/conftest_no_network.py` (yt-scrapper 포트 허용 방식 + trend-radar 세 함수 패치 합본, 40줄).
- **등급: 채택**.

## T02. 가드의 가드

- **어디서**: trend-radar `tests/test_conftest_guard.py:6-13`(13줄: 막히는가, 메시지에 테스트 이름이 있는가),
  Research_Paper `tests/paper_radar/test_guards.py` `SocketGuardTest`, `tests/conftest.py:11-12`가 그쪽을 가리킴.
- **관찰된 효과**: conftest를 고치다 차단을 잃으면 이 두 테스트가 먼저 빨개진다. 비용 0에 가까움.
- **재사용 형태**: `snippets/test_conftest_guard.py`.
- **등급: 채택**.

## T03. 진짜 Postgres, 테스트당 스키마

- **어디서**: yt-scrapper `tests/conftest.py:128-139`(nodeid → 63바이트 식별자, sha1 10자리로 충돌 방지), `:142-197`
  (`DROP SCHEMA … CASCADE; CREATE SCHEMA`, `options=-csearch_path=<schema>,pg_catalog`로 URL에 실어 yield, teardown에서 drop;
  `render_as_string(hide_password=False)` 함정 `:187-191`). `:145-148` "59번 `Database(tmp_path/…)`를 쓰던 18개 파일이 이 한 픽스처로".
  cosmai-old `apps/tests/conftest.py:128-186`은 **세션당** 한 번 `cosmai` 스키마를 리셋하고 default privileges를 다시 건다
  (`:141-158` OID 바뀜 → 권한 소실 함정). trend-radar `tests/storage/conftest.py:8-11`은 `create_all` 대신 **진짜 마이그레이션**을 돌린다.
- **무엇**: 테스트가 SQLite나 fake가 아니라 프로덕션과 같은 Postgres 18에 붙는다. 격리는 database가 아니라 schema 단위(초 단위 vs ms 단위).
- **관찰된 효과**: yt-scrapper `tool/checks/test:18-21` "프로덕션은 Postgres, 테스트는 SQLite — 방언 버그가 그렇게 나간다".
  `decisions/002`는 SQLite 시절 락 문제를 다뤘고 Postgres 이관으로 사문화됐다(`decisions/002…:29-33` 스스로 예견).
- **관찰된 비용**: Docker 필요(`tool/checks/test:30` `require_command uv docker pg_isready`). cosmai-old는 `cosmai_test` database를
  사전 프로비저닝해야 해서(`apps/tests/conftest.py:12-14`) 첫 실행 전 수동 단계가 생긴다. yt-scrapper 방식은 migrator에
  `GRANT CREATE ON DATABASE`가 필요(`tool/checks/test:61-65`, "하네스 전용, bootstrap 파일에는 넣지 않는다").
- **재사용 형태**: `snippets/db_schema_per_test.py` (yt-scrapper 픽스처 45줄, psycopg/SQLAlchemy 중립화).
- **등급: 채택** — 새 레포의 "테스트는 진짜·빠르게" 제약에 정확히 맞는다. 모노레포에서는 패키지마다 `<pkg>_t_…` 접두사로 스키마 이름을 나눈다.

## T04. 일회용 Postgres + 프로덕션 bootstrap SQL

- **어디서**: yt-scrapper `tool/checks/test:34-69`(`TUBEDEPTH_TEST_POSTGRES_URL` 없으면 `postgres:18-alpine` 컨테이너를 띄우고
  `deploy/postgres-bootstrap.sql`을 그대로 흘려 넣은 뒤 URL export, trap으로 정리). `:40-53` 호스트 쪽 `pg_isready` 폴링 이유
  (initdb 중 임시 서버가 컨테이너 안 유닉스 소켓으로만 ready를 답한다 — 실제로 겪은 flake).
- **관찰된 효과**: `deploy/postgres-bootstrap.sql:18-20` "CI가 검사하는 것이 프로덕션의 모양". `tests/test_postgres_privileges.py:1-17`이
  그 위에서 runtime 롤의 음성 증명(DDL 거부)을 돌린다.
- **관찰된 비용**: 푸시마다 컨테이너 기동(R03). 로컬 5434 포트의 `shared-postgres`가 이미 떠 있는 이 머신에서는 URL만 넘기면 된다.
- **재사용 형태**: `snippets/tool-checks/test` (컨테이너 기동 절 포함).
- **등급: 채택**.

## T05. AST 계층 가드

- **어디서**: trend-radar `tests/test_sources_stay_at_their_layer.py:39-43`(`ALLOWED`·`INVERTED` 집합을 이름으로 선언), `:50-64`(AST로 import 수집 —
  함수 안의 지연 import도 잡는다 `:15-17`), `:89-96`(AGENTS.md 규칙이 같은 집합을 말하는지). Research_Paper `tests/paper_radar/test_guards.py:58-79`
  (모듈 경로에서 계층 추출), `:82-103`(AST). cosmai-old `tests/environment/test_p1_isolation.py:73-84`(`apps/`가 `experiments`를 import 금지),
  `test_addon_layer_direction.py`, yt-scrapper `tests/test_repository_hygiene.py:21-30`(transport 생성은 두 곳에서만 — regex).
- **관찰된 효과**: trend-radar `:9-13` "규칙이 AGENTS.md 산문이던 동안 틀려 있었다 — contract·models만이라 했는데 네 소스가 registry를,
  셋이 scrub을 import했다. 코드가 눈에 띄게 어기는 규칙은 규칙으로 읽히지 않는다". Research_Paper `test_guards.py:3-4`가 이걸 "trend-radar 교본
  패턴 3"이라 부르며 이식 — 이미 한 번 재사용된 실적.
- **관찰된 비용**: 허용 집합이 테스트 파일과 AGENTS.md 두 곳(trend-radar는 그래서 `:89-96` 테스트를 하나 더 둠). yt-scrapper regex판은
  변수명으로 우회 가능(`test_queries_stay_inside_the_boundary.py:13-15`가 같은 이유로 regex를 거부).
- **재사용 형태**: `snippets/test_layering_guard.py` (패키지명·계층표만 바꾸면 되는 60줄).
- **등급: 채택** — 새 레포 `collectors/*` → `contracts/`만, `analysis/` → `contracts/` + `db/`만 같은 방향 규칙을 이걸로 건다.

## T06. 공허성 가드

- **어디서**: trend-radar `test_sources_stay_at_their_layer.py:67-69`(`len(_modules()) == len(SOURCES)`), `test_version_is_managed.py:44-47`
  (`len(modules) > 20` — "glob이 테스트 전체"), `test_scope_is_declared.py:175-180`(`total >= 5`, `:17-22`에 실제로 0==0으로 통과했던 사연),
  `test_docs_references_resolve.py:64-66`, `test_fixtures_are_scrubbed.py:48-50`, `test_every_dataset_has_a_collector.py:30-34`.
- **무엇**: 파라미터화/glob 기반 검사마다 "검사 대상이 비어 있지 않다"를 먼저 단언.
- **관찰된 효과**: `test_scope_is_declared.py:17-22` "scope가 dataset 키로 중첩되자 세 검사가 아무것도 안 보고 통과했다" — 실제로 겪고 넣은 것.
- **관찰된 비용**: 검사마다 3줄. 없음.
- **재사용 형태**: 패턴 — 모든 `@pytest.mark.parametrize(..., _discovered())` 옆에 `def test_there_is_something_to_check(): assert _discovered()`.
- **등급: 채택**.

## T07. 골든 파일 바이트 동일성

- **어디서**: Research_Paper `tests/paper_radar/test_trend_golden.py:1-17`(합성 OpenAlex 38건 → CSV 5개, 구 코드가 만든 골든과 바이트 비교),
  `:41-50`(어느 파일 몇 번째 줄인지 메시지), `tests/fixtures/make_trend_golden.py:1-24`(재생성 절차와 "신 코드로 다시 만들면 회귀 감지력을 잃는다"는 경고).
  `docs/judgment-debt.md:33-48`(원본 303MB가 없어 합성으로 대체한 경위와 "틀리면 비용").
- **관찰된 효과**: `papers_trend/` 구 코드를 삭제(T7)한 뒤에도 신 `trend/` 패키지가 같은 CSV를 낸다는 것이 테스트로 고정.
  CSV 컬럼/순서/인코딩이 "과거 산출물과의 조인이 걸린 공개 계약"(`:10-11`).
- **관찰된 비용**: 픽스처를 바꾸면 골든을 다시 만들어야 하고, 그 순간 테스트가 자기 자신과 비교하는 형태가 된다(`make_trend_golden.py:14-19`).
  골든은 "잘 안 바뀌게" 설계해야 한다.
- **재사용 형태**: `snippets/test_golden_files.py`. 새 레포 `eval/`의 labeled_set 660·제품 매핑 80쌍을 같은 형태로 —
  규칙/LLM을 바꿀 때마다 같은 입력으로 같은 출력 파일을 비교.
- **등급: 채택** — "eval-set 회귀 유지" 제약의 구현체.

## T08. 픽스처 스크럽 테스트

- **어디서**: trend-radar `tests/test_fixtures_are_scrubbed.py:1-16`(`profileKey`·닉네임 스크럽, `:12-16` 리뷰 디렉터리만 보다가 랭킹 페이지에서
  실명 61건 발견 → 전체 픽스처로 확대), `:53-57`(`SCRUBBED-` 접두사 검사). 35개 파라미터.
- **관찰된 효과**: 픽스처 재캡처 때 스크럽을 잊어도 커밋 전에 걸린다.
- **관찰된 비용**: 사이트별 키 이름(`profileKey`)이 테스트에 박힌다.
- **재사용 형태**: 패턴 그대로(키 목록만 새 소스 것으로). `snippets/test_fixtures_are_scrubbed.py`.
- **등급: 채택** — 리뷰 본문을 다루는 새 레포에서 필수.

## T09. 문서 진실성 메타테스트

- **어디서**: yt-scrapper `tests/test_documentation_is_true.py`(606줄, 17개 테스트 함수; README·api·status·troubleshooting·AGENTS 한/영 두 벌을 HTML 주석
  마커 `<!-- kinds:start -->`로 잘라 라우트·kind·에러코드·버전 비교 `:1-17`). trend-radar `tests/test_readme_translation_stays_in_step.py`(코드 스팬·링크·
  `TREND_RADAR_*` 변수 집합 비교), `tests/test_docs_references_resolve.py`(`docs/*.md` 참조 실재, 31개 파라미터), `tests/test_agent_context_is_project_only.py`
  (AGENTS.md 규칙부에 소스 키·한글 사이트명 금지).
- **관찰된 효과**: 실제로 README를 고치게 만든 기록 — yt-scrapper `:3-6` "README 첫 예제가 존재한 적 없는 라우트를 불렀고 마일스톤 표는 시작도 안
  했다고 했다". trend-radar `test_docs_references_resolve.py:3-7` "architecture.md는 몇 주 동안 없었다".
- **관찰된 비용**: (1) 메타테스트 16파일 1,620줄(trend-radar), 7파일 2,566줄(yt-scrapper). (2) 잡는 것은 "기계적 주장"뿐이고 의미는 못 잡는다
  (`test_payload_shapes.py:25-27`). (3) 그런데도 오늘 확인된 드리프트: trend-radar `AGENTS.md:79` master-only vs `tool/worktree.sh:22` dev 기본값;
  `service-db.json:4` database `trend_radar` vs 실제 `app`(`architect/README.md` §6 #1); yt-scrapper `.githooks/pre-commit:30`과 `tool/worktree.sh:70-71`이
  가리키는 `decisions/002-hooks-are-opt-in…md`·`decisions/006-verify-the-clone.md`는 존재하지 않는다(참조 실재 테스트는 trend-radar에만 있고,
  trend-radar는 자기 훅에서 그 주석을 지웠다 — 테스트가 없는 쪽에 드리프트가 남았다). (4) 번역 두 벌 유지 비용: README·CHANGELOG·AGENTS·api 각각 `.ko.md`.
- **등급: 제외** — 소유자 결정. 문서를 테스트로 묶는 대신 문서 자체를 줄인다(05 참조). 단 "참조한 파일이 존재한다" 한 줄짜리 검사는
  비용이 0에 가까워 필요해지면 T06 패턴으로 20줄에 넣을 수 있다.

## T10. 설정 진실성 테스트 — compose/systemd 명령줄

- **어디서**: yt-scrapper `tests/test_deployment_units.py:1-8`(유닛 파일의 ExecStart 옵션을 `tubedepth <sub> --help`로 검증, `:23-37` 환경 비우고 실행하는 이유),
  `tests/test_compose.py:1-13`(같은 질문을 compose `command:`에, yaml 앵커 동일성까지 `:34-40`).
- **관찰된 효과**: "재부팅 때만 드러나는 실수"(옵션 삭제, 잘못된 데이터 디렉터리)를 오프라인에서 잡는다. 425 + 362줄.
- **관찰된 비용**: 오늘 P16이 찾은 운영 사고 — `stack` 이관 때 trend-radar `review`/`review_stats`/`new_product` 크론 누락
  (`architect/slice-p16-collector-reliability/README.md:46`), cosmai `trendradar` 스케줄 10초(`:37`), tubedepth page_limit off-by-one(`:38`) — 은
  **이 테스트들이 있는 레포에서도** 잡히지 않았다. 테스트가 레포 안 compose만 보고, 실제 배선은 `stack/docker-compose.yml`에 있었기 때문.
- **재사용 형태**: 새 레포는 `stack/`이 같은 레포 안에 있으므로 한 테스트로 충분: "`stack/docker-compose.yml`과 crontab의 모든 `command:`/크론 줄이
  CLI `--help`에 존재하는 서브커맨드·옵션만 쓴다" + "선언된 데이터셋마다 크론 줄이 하나 있다"(S05와 결합). `snippets/test_stack_commands_resolve.py`.
- **등급: 변형**.

## T11. 부팅 경로 DDL 금지 + 권한 음성 증명

- **어디서**: yt-scrapper `tests/test_no_ddl_on_the_boot_path.py:1-18`(`_database()`가 `create_schema()`를 부르던 시절 → `duplicate column` 사고),
  `:35` `DDL_LEADERS = ("create","alter","drop","truncate")`를 SQLAlchemy 이벤트로 감시. `tests/test_postgres_privileges.py:1-17`(runtime 롤로 접속해
  DDL이 거부되는지). cosmai-old `apps/tests/test_migrate.py`, trend-radar `tests/storage/test_database_policy_acl.py`.
- **관찰된 효과**: 3롤 DB(04-O01)가 "관례"가 아니라 DB가 강제하는 경계임을 테스트가 증명. `docs/shared-postgres.md:411` 규칙 6.
- **관찰된 비용**: `postgres` 마커 테스트는 진짜 롤이 있는 DB가 필요 — T04가 해결.
- **재사용 형태**: `snippets/test_runtime_role_cannot_ddl.py` (15줄: runtime URL로 `CREATE TABLE` 시도 → `InsufficientPrivilege`).
- **등급: 채택**.

## T12. payload shape lock

- **어디서**: yt-scrapper `tests/test_payload_shapes.py:1-28`(모델 모양이 바뀌었는데 `schema_version`이 그대로면 실패; lock은 append-only, 현재 버전과
  다른 모양을 기록하려 하면 거부), `tests/payload_shapes.json`, `Justfile:138-145`, `docs/releasing.md:29-33`.
- **관찰된 효과**: `:5-9` 커밋 31e87bc가 `published_date`를 추가하고 버전을 안 올려 캐시된 artifact가 그 필드 null로 나갔던 사고의 재발 방지.
- **관찰된 비용**: 444줄. "초록 = 기록 안 된 모양 변경 없음"이지 "bump 불필요"가 아니다(`:25-27`) — 의미 변화는 못 본다.
- **재사용 형태**: 새 레포에서는 raw payload가 아니라 **사전·평가셋 테이블의 버전**에 같은 규율 — `entity_lexicon.version`을 올리지 않고 행을 바꾸면
  실패하는 테스트. 40줄이면 된다(`snippets/test_versioned_table_bumps.py`는 형태만).
- **등급: 변형**.

## T13. 적재된 행 검사 — hygiene / placeholder

- **어디서**: trend-radar `tool/checks/data:8-24`(일곱 번 초록이면서 거짓이었던 사고 목록, 두 계열의 의미, exit 69), `:40-142`(SQL 한 벌로
  hygiene 10개 + placeholder 8개 — 보드 전체 같은 별점, 부호 한쪽, 컬럼 통째 null, 전 상품 같은 round number), `:152-160`(hygiene만 실패).
  `docs/working-agreements.md:12-27`(사고 표: 7건 전부 테스트 초록, 6건 exit 0).
- **무엇**: "적재된 행을 조회해 숫자를 말하기 전까지 완료가 아니다"의 실행 가능한 절반. 사람이 수집 후 돌린다.
- **관찰된 효과**: `NOTES.local.md:22-24` 오늘자 기록 "tool/checks/data 비정상 0; LOOK 1건(리뷰 round-number, n=2)" — 실제로 매 변경마다 돌리고 있다.
  오늘 P16 분석도 같은 방식(DB 로그만으로 수집기 신뢰도 표, `slice-p16…/README.md:47` "운영 메타 테이블이 이미 충분").
- **관찰된 비용**: 소스별 placeholder 쿼리가 쌓인다(163줄). 자동화 불가 — `working-agreements.md:178` "✗ 불가. 사람이 돌린다".
- **재사용 형태**: `snippets/data-checks.sh` (hygiene/placeholder 골격 + exit 코드 규약, 쿼리는 새 스키마에 맞춰 작성).
- **등급: 채택** — 새 레포 `db/`에 스키마별 `checks.sql`로. P16의 수집기 건강 표도 같은 파일에.

## T14. live/contract 검사 분리

- **어디서**: trend-radar `tool/checks/contract:1-16`(`pytest -m live`, CI 금지, "막힌 소스는 결과이지 에러가 아님"), `tests/test_live_checks_are_paced.py:1-17`
  (live 테스트가 자기 fetcher를 만들지 않고 게이트를 지나는지 AST로 검사 — 13번 무절제 요청하던 사고), yt-scrapper `Justfile:46-53`(residential 연결에서만).
- **관찰된 효과**: 기본 스위트는 사이트 상태와 무관하게 초록. live는 11개(trend-radar), 4개(yt-scrapper)로 작다.
- **관찰된 비용**: live 테스트도 썩는다 — `docs/sources/` 관측이 날짜를 다는 이유.
- **재사용 형태**: 마커 + `tool/checks/contract` 3줄. `pyproject` addopts `-m "not live"`.
- **등급: 채택**.

## T15. 서브프로세스 워커 테스트 + TLS 스텁

- **어디서**: cosmai-old `apps/tests/conftest.py:430-520`(`start_worker`/`wait_for_worker`/`run_worker`, 타임아웃 시 kill 후 양쪽 스트림을 메시지에),
  `apps/tests/test_outbound_transport.py:104-122`(세션당 `openssl req -x509`로 자가서명 인증서 — cryptography/trustme 의존 회피), `:288-310`(루프백 TLS 서버 스텁).
  yt-scrapper `tests/test_deployment_units.py:23-40`(`--help` 서브프로세스, `env=` 통째 교체).
- **관찰된 효과**: 리스 만료·SIGINT·두 워커 동시 claim 같은 프로세스 경계 동작을 실제 프로세스로 검증(cosmai-old `tests/acceptance/JOB-005…007`).
- **관찰된 비용**: 653줄 conftest. 프로세스 기동 비용으로 스위트가 느려진다(1,199개 수집, 실행 시간은 DB 프로비저닝 필요로 오늘 미측정).
- **재사용 형태**: `start/wait/run` 3함수 패턴(`snippets/subprocess_helpers.py`, 40줄). TLS 스텁은 아웃바운드 정책을 코드로 강제할 때만.
- **등급: 변형** — 잡 큐/워커가 새 레포에 남는 `collectors/youtube`에서만.

## T16. 수용 시나리오 마크다운

- **어디서**: cosmai-old `tests/acceptance/JOB-001…SEC-004` 16개 + `SCENARIO-TEMPLATE.md`, `docs/agent-workflow/task-packets/` 12개.
- **관찰된 비용**: 시나리오 문서와 테스트 코드 두 벌. `docs/agent-workflow/README.md:51-62` "강제되는 것은 한 항목뿐(packet이 ACCEPTED라 주장하려면 PASS 링크)".
- **등급: 제외** — 테스트 함수 이름이 시나리오다(trend-radar `tests/engine/test_report_is_honest_about_stopping_early.py` 식 명명).

## T17. 대시보드/뷰 경계 테스트

- **어디서**: trend-radar `tests/dashboard/test_queries_stay_inside_the_boundary.py:1-16`(컴파일된 Select 객체를 검사 — regex는 변수명에 진다), 343개 파라미터.
  `tests/storage/test_the_schema_derives_nothing.py`(마이그레이션 SQL 쪽), `docs/judgment-debt.md:80-95`(뷰는 쿼리 객체 검사의 사각지대였다 → 뷰 좁힘).
- **관찰된 효과**: "수집 레포는 분석하지 않는다"는 경계가 코드로 유지됐고, 덕분에 오늘 재구성에서 `analysis/`를 별도 패키지로 떼는 결정이 쉬웠다.
- **관찰된 비용**: 새 레포는 **분석이 목적**이라 이 경계 자체가 없다. 343개 테스트 중 상당수가 이 한 파일.
- **등급: 변형** — 방향만 남긴다: `collectors/`는 집계하지 않고, `analysis/`만 집계한다 → T05 계층 가드로 충분.
