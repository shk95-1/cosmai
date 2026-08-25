# STATE — 구현 세션 상태 파일

**이 파일은 단계가 끝날 때마다 전면 덮어쓴다. 이력은 git에 있다.** (커밋 타입 `docs(state)`)
여기에 적지 않는 것: 완료 기준(→ `contracts/interfaces.md`), 왜 이렇게 하기로 했나(→ `architect/REBUILD.md` §2–§4, private), 다음 할 일의 세부(→ GitHub 이슈).

## 1. 부팅 (이 순서, 이것만)
1. 메모리: 자동 로드되는 것은 **인덱스(한 줄 훅)뿐**이다 — 본문은 다음 셋을 Read 한다: `rebuild-plan-state`, `working-style-preferences`, `ops-steps-run-in-session`
2. `contracts/README.md` → `contracts/{entrypoints,interfaces,formats,secrets,versioning}.md` → `contracts/ddl/needs/*.sql`
3. 이 파일 §2·§3
4. `gh issue view 16`(에픽: 사전 승인·순서·리뷰 등급·판정·원장 규칙) → **`gh issue view 37`(인계: 재개 지점·확정 사실·함정)** → `gh issue list --state open`. **유닛 이슈는 본문 + 코멘트를 함께 읽는다**(#17 판정 코멘트가 본문보다 우선). 이슈 코멘트가 원장이다.
5. 서브에이전트는 `.claude/agents/{impl-mechanical,impl,reviewer,reviewer-deep,judge}` 정의로 호출(모델·effort 고정).
6. 확인: `tool/checks/test` 녹색(일회용 postgres:18 → `db/migrate.sh` 2회 → 테스트), `git config core.hooksPath` = `.githooks`
슬라이스 상세(`architect/slice-*/README.md`)는 그 슬라이스를 이식하는 유닛만 읽는다. `playbook/`은 노하우 기록이지 필독이 아니다.

## 2. 현재 사실 (2026-08-25, 컷오버 완료)
- **컷오버 실행됨** (2026-08-25 02:54 UTC, 방식 B — #10 조건 1–5 전부 기계 검사 통과, 계획된 정지점 0회). 상세·판정 근거는 #10 코멘트, 인계는 **#37**.
- 도는 것:
  - 신 스택 `stack/docker-compose.yml` — `cosmai-{analyze, collector-commerce, collector-naver, collector-youtube-work, collector-youtube-flatten, portal}`. `collector-youtube-watch`는 `profiles: ["youtube-watch"]` 뒤(절차 3 대기: 팬아웃 상한 + `watchlist.txt` 채우기).
  - 구 스택 잔여(무정지) — `shared-postgres` · postgrest ×2 · data-portal · trend-radar-dashboard · tubedepth-api · cosmai-old ×4.
  - **정지** — trend-radar-collector · tubedepth-worker · tubedepth-flatten.
- 이미지 `cosmai-needs:local`·`cosmai-needs-cron:local` = **trixie / Python 3.12.14 / OpenSSL 3.5.6**. 이 값이 수집 성패를 가른다(§아래).
- `stack/.env` 필요(경로만, secret 값 없음). 없으면 compose가 `COSMAI_SECRET_FILE_HOST` 미설정으로 죽는다.
- 따뜻한 브라우저 프로필 `var/browser-profiles/{oliveyoung,glowpick}`(798M·3.2M)를 `collector-commerce`가 bind mount로 읽는다. 컨테이너는 `user: "1000:1000"` + `HOME=/tmp`.
- DB `shared-postgres` 127.0.0.1:5434, database `app`(trend_radar · tubedepth · **needs**), `cosmai`(cosmai-old). 슈퍼유저 `platform`.
  - 수집기 롤 비밀번호는 스키마마다 다르다: `TREND_RADAR_DB_RUNTIME`·`TUBEDEPTH_DB_RUNTIME`(#29). `COSMA_DB_RUNTIME`과 값이 다르니 공유하지 마라.
- gemma4 극성이 **선블록 한 scope의 주인**이다(`analysis/polarity/ownership.py`). 규칙 실행은 그 scope를 지우지도 덮지도 않는다(#31). 선블록 언급 13,857행 = `rule-v2.3` + `llm-ollama-gemma4:latest-fs2-20260824`(run 16, 6시간 45분).
- **이미지 베이스가 TLS 지문을 바꾼다**: bookworm(OpenSSL 3.0)에서는 oliveyoung 리뷰 API가 Cloudflare 챌린지로 막히고 trixie(3.5)에서는 통과한다(A/B 실측: 1×200→403 vs 57×200→ok). `tests/stack/test_image_tls_stack.py`가 하한 `(3,5)`를 못 박는다. 전말 #35.
- secret `~/.config/cosmai/env` 키: `contracts/secrets.md`. 값은 어디에도 쓰지 않는다.
- main은 origin과 동기(푸시 승인됨).

## 3. 경계
- **사전 승인됨(2026-08-23, 에픽 이슈에 정본)**: 2단계 DB 행 변경 · 002+ DDL 운영 적용(추가만, DROP·타입변경·데이터손실 제외) · 컷오버 조건부(#10) · LLM(`CLAUDE_API_KEY`, 하드스톱 $10(2026-08-24 $7→$10 승인), 리뷰·댓글 원문 전송 OK) · push·이슈·gh · 1차/2차 패스 기준(계약) · STATE.md 체계.
- **조건부 사전 승인(소진됨)**: 컷오버는 2026-08-25 실행 완료. 남은 것은 `collector-youtube-watch` 재가동뿐이고 조건은 그대로다(팬아웃 상한 확인 + `watchlist.txt`).
- **승인 필요(매번)**: 구 스택 컨테이너(`shared-postgres`·postgrest·data-portal·대시보드·tubedepth-api·cosmai-old) 재빌드·정지·재시작. **신 스택 컨테이너(`cosmai-*`)의 재빌드·재생성은 컷오버 운영의 일부로 세션이 직접 한다** — 단 `--force-recreate`를 써야 새 이미지가 실제로 걸린다(`docker start`는 옛 이미지로 켠다).
- **금지**: archive 구 레포 수정(로컬 stack·data-portal 설정 편집은 예외, 푸시 없음) · 구 cosmai 플랫폼 확장 · secret 값 출력·`.env` 커밋 · `--no-verify`·force push · **브라우저를 흉내 내는 UA·TLS 지문 위장**(수집기는 자기를 밝히는 UA를 쓴다 — `collectors/commerce/contract.py`).
- **미결**: wish_class 2차 패스 미달의 처리(#12 — 미달 기록 / 재라벨 / 기준선 변경 중 택1, 사용자 결정 대기).
