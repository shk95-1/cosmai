# HANDOFF — 임시 세션 인계 (2026-08-24 저녁)

**용도**: WSL 크래시 복구를 위해 `wsl --shutdown` 직전에 남기는 일회성 인계다. 다음 세션이
STATE.md §1 부팅 **후에** 이 파일을 읽고, 여기 적힌 재개 항목이 끝나면 **이 파일을 삭제**한다
(`docs(state)` 아님 — 커밋 메시지는 `chore: drop the handoff file` 정도).

읽는 순서: STATE.md §1 부팅 → 이 파일 → #10 의 최근 코멘트 3개(컷오버 중단 판정·A-2 판정·A-1 판정).

---

## 0. 왜 세션이 끊겼나 (한 문단)

`tool/checks/test` 가 `docker rm -f` 를 `-v` 없이 써서 실행마다 익명 볼륨(~300MB)을 흘렸다.
이 세션의 방법론이 "모든 유닛·리뷰·머지가 자기 스위트를 돌린다"라 하루에 40회 이상 돌았고,
391개·116.7GB 가 쌓여 vhdx 265GB → C: 83% → docker 대량 IO 중 커널 page allocation
failure(order:4/7, GFP_NOFS) → VM 강제 종료 2회. 수정은 `c29af7b`(rm -v · --rm ·
--tmpfs PGDATA)로 **머지·푸시 완료**, 실행 전후 볼륨 0 검증됨. 사용자가 `.wslconfig` 를
`autoMemoryReclaim=dropcache` 로 바꿨고, `wsl --shutdown` 으로 vhdx 압축(~140GB)과 함께
적용된다 — 그 shutdown 이 이 파일이 존재하는 이유다.

## 1. 기준 상태 (전부 검증된 값)

- **main = origin/main = `c29af7b`** · 615 passed, 1 deselected, 2 xfailed (66s, tmpfs 로 빨라짐)
- 운영: 구 스택 무정지(컷오버 안 함). `analysis_run` 9 ok clean. LLM 지출 $1.24/$10, 미정산 0.
- production DB 접근·secret 은 세션만(서브에이전트 금지) — STATE.md §3 그대로.

## 2. 미머지 브랜치 2개 — 코드는 완전, 리뷰만 유실/미시행

크래시로 서브에이전트가 전부 죽었지만 **커밋은 전부 살아 있다.** 워크트리 미커밋 변경 없음.

### (a) `feat/10-transport` (워크트리 `../cosmai-wt/transport`, base `e7e0e1e`)
`b0c8f66` commerce 라이브 트랜스포트 · `7a51ed9` hwahae 예산 20(사용자 승인) · `42d9035` 이미지 Chromium.
- 스위트 727 passed(당시). 구현자 자체 뮤테이션 27종 → 생존 1(M16, CLI 가 트랜스포트를 안 닫음) → 테스트 3개 추가 후 0.
- 구현 보고서: 스크래치패드는 세션별이라 **날아갔을 수 있다** — 요지는 #10 컷오버 중단 코멘트와 이 파일로 충분하다.
- **재개 = 등급 A 심층 리뷰 재디스패치.** 리뷰 브리프에 반드시 넣을 것:
  - status→에러 매핑을 원본(`service/trend-radar/src/trend_radar/transport/http.py`·`challenge.py`)과 직접 대조.
    특히 **401→ChallengeBlocked 가 원본대로인지**(401 은 보통 인증 실패지 차단이 아니다 — 틀리면 exit 2 가 거짓말한다).
  - **200 본문 마커가 상태를 이기는 규칙의 오탐**: 한국어 리뷰 본문에 마커 문자열이 우연히 들어가면 멀쩡한 수집이 blocked 로 죽는다.
  - `ThreadPoolExecutor(max_workers=1)` 와 게이트 동시성의 어긋남(오늘은 oliveyoung concurrency=1 이라 무해)이 감지되는지.
  - `Retry-After` 거대값: `gate.py:47-48` 이 명시 Retry-After 를 MAX_INTERVAL_S **너머로** 존중한다 — 상한 검사가 어디 있는지.
  - `DEFAULT_PROFILE_DIR = Path("var/browser-profiles")` 가 **상대 경로** — 호스트에서 다른 cwd 로 부르면 엉뚱한 곳에 생긴다.
  - 뮤테이션 재확인 최소 4종(M4 challenge→Transient · M9 fetch.transport 무시 · M15 생성자 Chromium · M23 5xx→Permanent).
  - 이미지 빌드는 `cosmai-needs:local` 태그만(맨 `cosmai` 는 구 fleet 앱 — `tests/stack/test_image_tag.py` 가 지킨다).
- 구현자가 남긴 우려: compose 에 브라우저 프로필 볼륨이 없어 oliveyoung 이 매 런 빈 프로필로 시작
  (`collector-commerce` 에 `- commerce-profiles:/srv/cosmai/var/browser-profiles` 필요 — `<<:` 가 anchor volumes 를
  **덮으므로** secret 마운트 줄도 같은 volumes 블록에 다시 적어야 한다, M1 참조) · oliveyoung timeout_s=120 이라
  그림자 실행이 예산 계산보다 길 수 있음 · Chromium 이 실제 챌린지를 통과하는지는 조건 3 만이 답한다.

### (b) `feat/6-ollama-eval` (워크트리 `../cosmai-wt/ollama-eval`, base `e7e0e1e`)
`f4f5f9c` `--impl ollama:<model>` 팩터리 등록(paid=False) · `8c41413` autocommit 수정.
- autocommit 이 필수인 이유: `needs_runtime` 은 `idle_in_transaction_session_timeout=15s` 인데 ollama 는
  단건 순차(문장당 수 초)라 **첫 문장 생성 중에 커넥션이 죽는다** — 세션이 실측으로 밟았다. Claude 경로가 살아남는
  이유는 `pricing.py` 의 `reserve()` 가 API 호출 전에 커밋해서다(우연의 보호).
- **재개 = 등급 B 리뷰 → 머지.** 리뷰 확인점: autocommit 이 실제로 걸리는지(압축 타임아웃 테스트가 있다면
  `current_setting` 사전확인이 있는지 — 없으면 시간 단언이 공짜로 통과), `llm` 팩터리 무변경, 버전 문자열
  `llm-ollama-gemma4:latest-20260824` 가 계약 정규식에 맞는지(맞다 — `.+` 가 콜론을 삼킨다).

## 3. 재개 순서 (사용자 승인 완료된 계획)

1. **(b) 리뷰 → 머지 → 푸시.** 작은 쪽 먼저 — 4가 이걸 기다린다.
2. **(a) 심층 리뷰 재디스패치 → 수정 라운드(있으면) → 머지 → 푸시.**
3. **compose 에 브라우저 프로필 볼륨 추가** — 작은 유닛 또는 세션이 직접. 2(a) 우려 참조.
4. **few-shot 튜닝 유닛 디스패치** (§5 의 확정 파라미터로).
5. 튜닝 확정 → **선블록 전량 패스**(로컬, ~2.7h, 백그라운드) → 그동안 **조건 3·4·5 컷오버** 진행.
6. 컷오버 후: #11 portal · 전 카테고리 패스(~40h) · #12.

## 4. 컷오버 (#10) 위치 — 정확히 어디서 멈췄나

- §A 선행 조건 **전부 닫힘**(A-1·2·3·4·5·6·8-1·8-2, 전부 머지됨). §C 절차 1(스택 배선) 머지됨.
- **조건 1 ✅** `tool/checks/ddl-drift`(운영 = 기준선 + additive 파일, 세 스키마 diff 0).
  **조건 2 ✅** 스위트 + 이미지 빌드 시점 RUN 검증.
- **조건 3 🛑 트랜스포트 부재로 중단했었다** → (a) 가 머지되면 재평가 가능. 그림자 실행은
  **구 크론을 피해서**(매시 :00–:02 ranking · 02:10 product · 03:30 · 04:15 · 04:45 · 05:30 UTC 회피,
  안전 창은 매시 :03–:50). 기준선은 #10 코멘트에 사전 등록: **records 5,978~7,306(±10%), blocked 0**.
  새 수집기는 소스 순차라 구(90s)보다 느린 ~3.4분+ 이 정상. 락이 있으니 구 크론과 겹치면 소스를 건너뛴다 —
  그건 결함이 아니라 행 수 비교가 무효가 되는 것.
- 조건 4 = `pg_dump -Fc app` → `Main/service/stack/backups/` (리허설 7.2s·93MB, 디스크 여유 확인됨).
- 조건 5 = 첫 크론 ok, 실패 시 `stack/rollback.sh` (구 스택을 **기본 파일 탐색**으로 읽는다 — `-f` 하나만 주면
  override 가 빠져 §A-4 크론탭 마운트가 사라진 채 recreate 된다. 이미 고쳐져 있음).
- §E: 조건 미충족이면 사람 승인으로 강행하지 않는다. 컨테이너 정지 순서 등 실행 명령렬은 #10 의
  stack 유닛 보고 코멘트 참조.

## 5. LLM 극성 (#6/#21) — 사용자 결정과 실측 (전부 이 세션에서 확정)

- **결정: 전량 패스는 gemma4 로컬($0), Claude 예산 $10 은 보존.** 부분-Claude 패스는 기각
  (반쯤 라벨된 데이터셋이 남고 metrics 가 두 분류기를 섞는다).
- **결정: B 안 — thinking OFF + few-shot 튜닝으로 sun acc 회복.** 선블록 먼저, 전 카테고리는 나중
  (자연키에 polarity_version 이 없어 재라벨은 제자리 upsert — 카테고리별 집계는 깨끗, rollup 만 혼합 주의).
- 홀드아웃 실측 (gemma4:latest 8B Q4_K_M, RTX 4060):

  | | sun holdout 100 | p1 blind40 | 소요 |
  |---|---|---|---|
  | 규칙(교체 기준) | acc .870 · P:불만 .915 | acc .475 | — |
  | thinking ON | acc .900 · P .955 | acc .875 · P .950 | 1,300s |
  | thinking OFF | acc **.850** · P .976 | acc .850 · P 1.000 | **134s** |

- 병목은 **gemma4 의 사고 토큰**(eval_count 에 안 잡히고 호출당 6~9s). 프리픽스 재사용·재시도·스키마는
  전부 무죄로 실측됨(142호출/140문장 · 스키마 제약이 오히려 4배 빠름). `think:false` 로 9.7배.
- ollama 는 요청을 **직렬화**한다(동시 2=2배, 4=4배 — VRAM 8GB 라 NUM_PARALLEL=1). 병렬화 무익.
  ollama 부하 중 스위트는 +26%(96s)로 **동시 작업 가능**.
- **튜닝 유닛 브리프에 넣을 것**: 목표 = thinking OFF 로 sun acc > .870(지금 .850 — 100문장 중 5개 차이).
  `--split tune`(260행, 회당 ~4.3분)만 사용, **홀드아웃은 최종 1회**(세션이 이미 2회 소모해 블라인드가 닳았다).
  `think:false` 는 스크래치패드 몽키패치가 아니라 **실제 코드**로(`chat_payload` 또는 팩터리에서).
  프롬프트가 바뀌면 `PROMPT_DATE` 도 함께. **`불만` P 를 깎지 말 것**(OFF 에서 .976/1.000 — 채택 조건이 보는 값).
  few-shot 은 tune 셋에서 뽑되 홀드아웃 문장 금지.
- 홀드아웃 숫자의 caveat: 각 1회 실행, 셋이 작아(100·40) acc 5pt = 문장 5개. 계약이 점값 비교라 그대로 쓴다.
- `contracts/interfaces.md` §LLM 실측 표에 gemma4 줄 추가는 **아직 안 했다** — 튜닝 확정 후 최종 숫자로 한 번에.

## 6. 이 세션에서 반복된 함정 (다음 세션도 그대로 적용해라)

1. **"통과하지만 아무것도 검사하지 않는 테스트"가 여섯 번 나왔다.** 예산 강제 무테스트 · TOCTOU 경합 테스트
   3연속 거짓 초록 · `.dockerignore` 매처(이건 리뷰어가 틀렸고 실측으로 기각) · 분0 회피 테스트 ·
   세션 자신의 sink 부정 테스트(주장이 반증되어 다시 씀) · secret 마운트 검사(YAML merge key 오모델링).
   → 유닛 브리프에 RED 확인 + 자체 뮤테이션을 계속 요구하고, 리뷰어에게 뮤테이션 재현을 시켜라.
2. **idle-in-transaction 15s** 를 세 번 밟았다(락 유닛 · 세션 자신 · ollama 팩터리). DB 커넥션을 쥐고
   느린 일을 하는 코드는 전부 의심하라.
3. **돌려본 적 없는 것은 작동하지 않는다** — rollback 의 `-f` 누락과 `cosmai` 태그 충돌은 564개 테스트가
   전부 통과한 상태에서 리뷰어가 docker 에 실제로 물어봐서만 드러났다.
4. 계약 소요 수치는 **하한**이다(백오프 300s 상한·재시도·hwahae 무예산이 전부 위로 민다). "상한" 이라 적지 마라.

## 7. 열린 이슈 스냅샷 (재개와 무관한 것 포함)

#5(닫기 가능 여부 확인) · #6/#21(§5) · #10(§4) · #11(portal, 미착수 — "B 먼저" 조건은 해소됨) ·
#12 · #14 · #15 · #16(에픽) · #18 · #19/#22/#23(외부 architect) · #20(보안, 컷오버 후 —
노출은 bootstrap 한 줄뿐, migrator_psql 은 이미 PGPASSWORD) · #24(Sink 규약, **닫아도 됨** —
`d333869` 로 규약+테스트 반영됨, 이슈만 열려 있다) · #25(병렬 레인, 컷오버 후 — 선행이
"#24 재작업"이라 적혀 있으나 코멘트로 정정됨: sink 는 이미 안전, 커넥션 예산 계산이 진짜 선행).

후속으로 열 이슈 (아직 안 열었다): youtube `collector_health` 팔 복귀(초안은 A-2 유닛 보고에 있었으나
스크래치패드라 유실 — §A-2 판정 코멘트의 근거 4개로 재구성 가능) · `youtube-payloads` 볼륨 무한 증가
(`PayloadStore.delete` 호출자 없음) · 전부-skip 런이 `collector_health` 에서 "양보"인지 "전부 실패"인지
구분 안 되는 문제.
