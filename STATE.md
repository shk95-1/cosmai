# STATE — 구현 세션 상태 파일

**이 파일은 단계가 끝날 때마다 전면 덮어쓴다. 이력은 git에 있다.** (커밋 타입 `docs(state)`)
여기에 적지 않는 것: 완료 기준(→ `contracts/interfaces.md`), 왜 이렇게 하기로 했나(→ `architect/REBUILD.md` §2–§4, private), 다음 할 일의 세부(→ GitHub 이슈).

## 1. 부팅 (이 순서, 이것만)
1. 메모리: 자동 로드되는 것은 **인덱스(한 줄 훅)뿐**이다 — 본문은 다음 셋을 Read 한다: `rebuild-plan-state`, `working-style-preferences`, `ops-steps-run-in-session`
2. `contracts/README.md` → `contracts/{entrypoints,interfaces,formats,secrets,versioning}.md` → `contracts/ddl/needs/*.sql`
3. 이 파일 §2·§3
4. `gh issue list --state open`. **유닛 이슈는 본문 + 코멘트를 함께 읽는다** — 이슈 코멘트가 원장이다. 에픽 #16(2–6단계)은 2026-08-25 에 닫혔고 그 완료 보고가 마지막 코멘트다: 규칙(사전 승인·리뷰 등급·판정·원장)을 다시 봐야 하면 `gh issue view 16`. 인계 #37 도 닫혔다.
5. 서브에이전트는 `.claude/agents/{impl-mechanical,impl,reviewer,reviewer-deep,judge}` 정의로 호출(모델·effort 고정). 브리프에 **"테스트는 전경에서 한 번의 블로킹 호출로"** 를 명시한다 — 백그라운드로 던지고 반환하는 일이 반복됐다.
6. 확인: `tool/checks/test` 녹색(일회용 postgres:18 → `db/migrate.sh` 2회 → 테스트), `git config core.hooksPath` = `.githooks`
슬라이스 상세(`architect/slice-*/README.md`)는 그 슬라이스를 이식하는 유닛만 읽는다. `playbook/`은 노하우 기록이지 필독이 아니다.

## 2. 현재 사실 (2026-08-25, 에픽 #16 완료)
- **2–6단계가 끝났다.** 컷오버 실행(02:54 UTC, 방식 B, 계획된 정지점 0회) → 컷오버 후 첫 장애 규명 → gemma4 선블록 전량 패스 → 단계 말 전체 리뷰 + 수정 + 재리뷰. 완료 보고는 #16 의 마지막 코멘트.
- main = origin/main = **`6e0ef2a`**, 스위트 **877 passed · 1 deselected · 2 xfailed**.
- 도는 것:
  - 신 스택 `stack/docker-compose.yml` — `cosmai-{analyze, collector-commerce, collector-naver, collector-youtube-work, collector-youtube-flatten, portal}`. `collector-youtube-watch`는 `profiles: ["youtube-watch"]` 뒤(#39 — 팬아웃 상한 + `watchlist.txt` 채우기).
  - 구 스택 잔여(무정지) — `shared-postgres` · postgrest ×2 · data-portal · trend-radar-dashboard · tubedepth-api · cosmai-old ×4.
  - **정지** — trend-radar-collector · tubedepth-worker · tubedepth-flatten.
- 이미지 `cosmai-needs:local`·`cosmai-needs-cron:local` = **trixie / Python 3.12.14 / OpenSSL 3.5.6**. 이 값이 수집 성패를 가른다(아래).
- `stack/.env` 필요(경로만, secret 값 없음). 없으면 compose가 `COSMAI_SECRET_FILE_HOST` 미설정으로 죽는다.
- 따뜻한 브라우저 프로필 `var/browser-profiles/{oliveyoung,glowpick}`(798M·3.2M)를 `collector-commerce`가 bind mount로 읽는다. 컨테이너는 `user: "1000:1000"` + `HOME=/tmp`.
- DB `shared-postgres` 127.0.0.1:5434, database `app`(trend_radar · tubedepth · **needs**), `cosmai`(cosmai-old). 슈퍼유저 `platform`.
  - 수집기 롤 비밀번호는 스키마마다 다르다: `TREND_RADAR_DB_RUNTIME`·`TUBEDEPTH_DB_RUNTIME`. `COSMA_DB_RUNTIME`과 값이 다르니 공유하지 마라.
  - **ydc 통합 흔적**(#57): `needs.schema_migration` 원장에 `020_retrieval_chunk`(2026-08-24 23:47)가 있다. **020번대는 포크 `slopindustries/cosmai-import-ydc` 의 것이고 upstream 은 `006~019` 만 쓴다.** `db/migrate.sh` 는 체크아웃에 있는 파일만 훑으므로 main 배포는 그 행을 그냥 지나간다.
  - `needs.retrieval_chunk` **381,950행·256MB** 가 공유 DB 에 있다(DB `app` 1,118MB 의 23%) — `pg_dump -Fc app`(#10 조건 4)이 그만큼 커졌다. 통합 본체는 Draft PR #59 로만 온다.
- **분석 파이프라인이 매일 05:00 UTC 에 전량을 다시 라벨한다**(`cosmai analyze all`, 규칙 극성). 규칙 전량 실행은 **47~88초**다 — 6시간대 숫자는 gemma4 LLM 패스의 것이지 이 줄의 것이 아니다.
  - gemma4 극성이 **선블록 한 scope의 주인**이다(`analysis/polarity/ownership.py`). 규칙 실행은 그 scope를 지우지도 덮지도 않는다. **운영 실증**: run 18(2026-08-25 06:28, 규칙 전량)이 선블록 gemma4 13,857행을 한 행도 안 지웠다.
  - 그 전량 패스의 소요가 처음 측정됐다 — run 16 = **6h44m**(선블록 하나, `attempted_need=13,857`). 크론 줄을 넣는 조건과 계산은 `stack/crontab.d/analyze` 에 적혀 있다.
- **이미지 베이스가 TLS 지문을 바꾼다**: bookworm(OpenSSL 3.0)에서는 oliveyoung 리뷰 API가 Cloudflare 챌린지로 막히고 trixie(3.5)에서는 통과한다(A/B 실측: 1×200→403 vs 57×200→ok). `tests/stack/test_image_tls_stack.py`가 하한 `(3,5)`를 못 박는다. 전말 #35.
- secret `~/.config/cosmai/env` 키: `contracts/secrets.md`. 값은 어디에도 쓰지 않는다.

## 3. 경계
- **사전 승인됨(2026-08-23, #16 에 정본)**: 2단계 DB 행 변경 · 002+ DDL 운영 적용(추가만, DROP·타입변경·데이터손실 제외) · LLM(`CLAUDE_API_KEY`, 하드스톱 $10, 리뷰·댓글 원문 전송 OK) · push·이슈·gh · 1차/2차 패스 기준(계약) · STATE.md 체계. 운영 DB migrate/seed/UPDATE·`analyze` 실행·secret 파일 기록은 **세션이 직접 한 명령씩** 한다(서브에이전트 디스패치가 분류기에 막힌다).
- **조건부 사전 승인(소진됨)**: 컷오버는 2026-08-25 완료. 남은 것은 `collector-youtube-watch` 재가동뿐이고 조건은 그대로다(#39).
- **승인 필요(매번)**: 구 스택 컨테이너(`shared-postgres`·postgrest·data-portal·대시보드·tubedepth-api·cosmai-old) 재빌드·정지·재시작. **신 스택 컨테이너(`cosmai-*`)의 재빌드·재생성은 세션이 직접 한다** — 단 `--force-recreate`를 써야 새 이미지가 실제로 걸린다(`docker start`는 옛 이미지로 켠다).
- **금지**: archive 구 레포 수정(로컬 stack·data-portal 설정 편집은 예외, 푸시 없음) · 구 cosmai 플랫폼 확장 · secret 값 출력·`.env` 커밋 · `--no-verify`·force push · **브라우저를 흉내 내는 UA·TLS 지문 위장**(수집기는 자기를 밝히는 UA를 쓴다 — `collectors/commerce/contract.py`).

## 4. 함정 (실측으로 산 것)
- **`analyze all --scope <lexicon_category>` 는 진짜 숫자를 안 준다.** polarity 는 `lexicon_category` 로, aggregate 는 **원천 카테고리**로 거른다(선블록의 원천 값은 `01 > 선케어 > 선블록`). 축 정렬은 #38 에 남아 있고, 지금은 그 run 이 `partial` + 종료 코드 1 로 **조용하지 않게** 끝나며 원천 카테고리 문자열을 말해 준다.
- **`metrics_need` 에 제품 축·월 축 행이 없다.** 집계기가 `need_key` 하나당 한 행만 내므로 portal 화면 3(제품별)은 실제 run 에서 영원히 0행이다. 시드 run 1 의 30행은 슬라이스가 만든 것이다. → #41
- **`docker start` 로 켜면 옛 이미지로 돈다.** 이미지를 바꿨으면 `up -d --force-recreate`.
- **락은 구코드 실행을 못 본다.** analyze 동시성 락(`analysis/locks.py`) 이전에 시작된 프로세스는 락 밖이다.
- **골든·시드 테스트 4종이 레포 밖 `architect/` 에 의존한다.** 없으면 조용히 skip 한다 — `877 passed` 는 그 트리가 있는 머신의 숫자다. 후보 경로 둘은 각각 주 체크아웃용과 워크트리용이라 어느 쪽도 죽은 경로가 아니다. → #42 M6
- **이 머신에서 다른 Claude 세션이 동시에 작업할 수 있다** — 포크 클론 `Main/cosmai-import-ydc`(retrieval, 포트 55452; 구 워크트리·브랜치 `feat/ydc-import` 는 폐기, 이 레포에서 쓰지 않는다). 스위트 포트가 겹치지 않게 고를 것.
- **`tool/checks/ddl-drift` 는 needs 스키마를 안 본다.** 비교 대상은 `app:trend_radar`·`app:tubedepth`·`cosmai:cosmai` 뿐이라 운영 needs 가 계약과 어긋나도 잡는 검사가 없다(020 이 안 보이는 것도 이 때문이지 020 이 특별해서가 아니다).
- **GPU 는 하나다.** `cosmai retrieval embed`(38만 청크, 유휴 GPU 20.6분)와 gemma4 패스가 겹치면 **5h38m** 이 되고 상대편도 같은 대가를 치른다(#28 실측). #32 의 크론 창은 이 겹침을 피해야 한다.
- `tool/stack-build` 2단계는 `stack/.env` 없이는 compose interpolation 에서 죽는다(빌드 실패가 아니라 변수 미설정).

## 5. 다음
큰 작업은 없다. 열린 이슈가 할 일 목록이다. 흐름이 있는 묶음만 적는다.
- **gemma4 확장**: #32(컨테이너→ollama 배선 + 증분 크론) → #33(26개 카테고리 패스) → #34(카테고리 없는 리뷰 18,531행 = `category_map` 문제). #33 은 #38 의 축 정렬이 먼저 서야 6시간을 헛되이 쓰지 않는다.
- **화면**: #41(집계 제품·월 축 + `analysis_run` 화이트리스트 + 화면 2 marginal).
- **평가셋**: #40(wish blind60_v2 라벨 규약 충돌 재라벨). 이 충돌이 wish_class 만의 문제인지 자산 전체의 문제인지가 그 이슈의 첫 질문이다.
- **운영**: #39(watch 재가동) · #36(UTC→KST) · #57(ydc 환경) → PR #59 리뷰. ydc 통합 본체는 포크 `cosmai-import-ydc` 가 원장이고 여기엔 PR 로만 온다.
- **이월**: #42(전체 리뷰 10건) · #14 · #21 · #20 · #18 · #15.
