# STATE — 구현 세션 상태 파일

**이 파일은 단계가 끝날 때마다 전면 덮어쓴다. 이력은 git에 있다.** (커밋 타입 `docs(state)`)
여기에 적지 않는 것: 완료 기준(→ `contracts/interfaces.md`), 왜 이렇게 하기로 했나(→ `architect/REBUILD.md` §2–§4, private), 다음 할 일의 세부(→ GitHub 이슈).

## 1. 부팅 (이 순서, 이것만)
1. 메모리: 자동 로드되는 것은 **인덱스(한 줄 훅)뿐**이다 — 본문은 다음 셋을 Read 한다: `rebuild-plan-state`, `working-style-preferences`, `ops-steps-run-in-session`
2. `contracts/README.md` → `contracts/{entrypoints,interfaces,formats,secrets,versioning}.md` → `contracts/ddl/needs/*.sql`
3. 이 파일 §2·§3
4. `gh issue view 16`(에픽: 사전 승인·순서·리뷰 등급·판정·원장 규칙) → `gh issue list --state open`. **유닛 이슈는 본문 + 코멘트를 함께 읽는다**(#17 판정 코멘트가 본문보다 우선). 이슈 코멘트가 원장이다.
5. 서브에이전트는 `.claude/agents/{impl-mechanical,impl,reviewer,reviewer-deep,judge}` 정의로 호출(모델·effort 고정).
6. 확인: `tool/checks/test` 녹색(일회용 postgres:18 → `db/migrate.sh` 2회 → 테스트), `git config core.hooksPath` = `.githooks`
슬라이스 상세(`architect/slice-*/README.md`)는 그 슬라이스를 이식하는 유닛만 읽는다. `playbook/`은 노하우 기록이지 필독이 아니다.

## 2. 현재 사실 (2026-08-23)
- 레포 `slopindustries/cosmai`(private). 구 레포 6개 archive. 운영 스택 `Main/service/stack`은 구 레포 로컬 디렉터리에서 빌드된 채 **계속 돈다**.
- DB `shared-postgres` 127.0.0.1:5434, database `app`(trend_radar · tubedepth · **needs**), `cosmai`(cosmai-old). 슈퍼유저 `platform`.
  - `needs`: `db/migrate.sh`로 적용됨 — 확인 `docker exec shared-postgres psql -U platform -d app -Atc "select version from needs.schema_migration"` → `001_needs`.
  - 시드: `uv run python -m db.seed` 2회 동일 — 행 수는 `tests/test_seed.py` EXPECTED(15 테이블). `product_variant`·`product_line_mention` 비어 있음.
- 노출: PostgREST :3000 `Accept-Profile: needs` — `metrics_need`·`metrics_wish`·`entity_lexicon`·`aspect_lexicon`·`product_ref`만 anon SELECT. data-portal :3001 에 `needs` 포함(재빌드됨).
- 수집기 상태: trend-radar 크론 복구됨(review_low 03:30 · review 04:15 · stats 05:00 · new_product 05:30 UTC). tubedepth `watch` **정지**(팬아웃 상한 전까지), worker·flatten·api Up. cosmai-old trendradar.rest 10초 스케줄 **아직 켜짐**(이슈 2단계).
- secret `~/.config/cosmai/env` 키: `contracts/secrets.md`. 값은 어디에도 쓰지 않는다.
- main은 origin과 동기(푸시 승인됨).

## 3. 경계
- **사전 승인됨(2026-08-23, 에픽 이슈에 정본)**: 2단계 DB 행 변경 · 002+ DDL 운영 적용(추가만, DROP·타입변경·데이터손실 제외) · 컷오버 조건부(#10) · LLM(`CLAUDE_API_KEY`, 하드스톱 $10(2026-08-24 $7→$10 승인), 리뷰·댓글 원문 전송 OK) · push·이슈·gh · 1차/2차 패스 기준(계약) · STATE.md 체계.
- **조건부 사전 승인**: 5단계 컷오버·구 컨테이너 정지·watch 재가동은 이슈 #10의 조건 1–5가 전부 기계적으로 참일 때만 세션이 실행(방식 B). 조건 미충족이면 사람 승인으로 대체하지 않는다.
- **승인 필요(매번)**: #10 조건 밖의 기존 스택 컨테이너 재빌드·정지·재시작.
- **금지**: archive 구 레포 수정(로컬 stack·data-portal 설정 편집은 예외, 푸시 없음) · 구 cosmai 플랫폼 확장 · secret 값 출력·`.env` 커밋 · `--no-verify`·force push · 새 수집(컷오버 전; #10 조건 3·5의 그림자 실행·첫 크론 확인만 예외).
- **미결**: 없음 (6단계 화면 위치 = 이 레포 `portal/`로 결정됨).
