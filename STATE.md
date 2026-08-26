# STATE — 계산할 수 없는 사실과 승인 경계

**부팅 순서는 `AGENTS.md`, 할 일은 이슈(`tool/issue ready`), 계산 가능한 운영 사실은 `tool/status` 가 찍는다.** 이 파일에는 그 셋이 못 주는 것만 남긴다: 의도(무엇이 돌아야 하는가), 승인 경계, 되돌림 절차. 갱신은 도는 것을 바꾸는 `ops` 이슈를 닫는 커밋에서 한다. 이력은 git.

## 2. 사실 (계산 불가) — §1(부팅)은 `AGENTS.md` 다
- **신 스택**(`stack/docker-compose.yml`)이 돌아야 한다: `cosmai-{analyze, collector-commerce, collector-naver, collector-youtube-work, collector-youtube-flatten, portal}`. `collector-youtube-watch` 는 `profiles: ["youtube-watch"]` 뒤 — 재가동 조건은 #39.
- **구 스택 잔여(무정지)**: `shared-postgres` · postgrest ×2 · data-portal · trend-radar-dashboard · tubedepth-api · cosmai-old ×4. **정지 상태여야 하는 것**: trend-radar-collector · tubedepth-worker · tubedepth-flatten.
- 이미지 `cosmai-needs:local`·`cosmai-needs-cron:local` 의 베이스는 **trixie / OpenSSL ≥ 3.5** 여야 한다 — bookworm(3.0)은 oliveyoung 리뷰 API 의 Cloudflare 챌린지에 막힌다(A/B 실측, #35). `tests/stack/test_image_tls_stack.py` 가 하한을 못 박는다.
- `stack/.env` 가 있어야 한다(경로만, secret 값 없음). 없으면 compose 가 `COSMAI_SECRET_FILE_HOST` 미설정으로 죽는다.
- 브라우저 프로필 `var/browser-profiles/{oliveyoung,glowpick}` 을 `collector-commerce` 가 bind mount 로 읽는다(`user: "1000:1000"`, `HOME=/tmp`). 사람이 `cosmai login` 으로 만든다.
- DB `shared-postgres` 127.0.0.1:5434, database `app`(trend_radar · tubedepth · needs) · `cosmai`(cosmai-old). 슈퍼유저 `platform`. 수집기 롤 비밀번호는 스키마마다 다르다(`TREND_RADAR_DB_RUNTIME` · `TUBEDEPTH_DB_RUNTIME` · `COSMA_DB_RUNTIME`) — 공유하지 마라.
- 분석: 매일 05:00 UTC `cosmai analyze all`(규칙) + 08:00 UTC gemma4 증분 패스(`analyze polarity --impl ollama:gemma4:latest --missing`, #32). `OWNERS` 는 27개 scope — 선블록(since=ALWAYS)과 #33 의 26개(since=2026-08)이고, 규칙 실행은 주인의 (scope, 달)을 지우지도 덮지도 않는다(`analysis/polarity/ownership.py`). GPU 창 08:00–16:00 UTC 는 포크의 `retrieval embed` 가 피한다.
- secret 키 이름: `contracts/secrets.md`. 값은 어디에도 쓰지 않는다.
- DDL 번호: upstream 006~019, 포크 020~(`contracts/versioning.md`).

## 3. 경계
- **상시 승인(2026-08-23, 원장은 닫힌 #16; 규칙 변경은 #74 `[규약]`)**: DDL 운영 적용(추가만 — DROP·타입 변경·데이터 손실 제외) · LLM(`CLAUDE_API_KEY`, 하드스톱 $10, 리뷰·댓글 원문 전송 OK) · push·이슈·gh · 신 스택 컨테이너(`cosmai-*`)의 재빌드·재생성(세션 직접, `up -d --force-recreate`). 운영 DB migrate/seed/UPDATE·`analyze` 실행·secret 파일 기록은 **코디네이터 세션이 직접 한 명령씩**(서브에이전트 디스패치는 분류기에 막힌다).
- **매번 승인**: 구 스택 컨테이너(`shared-postgres` · postgrest · data-portal · 대시보드 · tubedepth-api · cosmai-old) 재빌드·정지·재시작 · `DROP` 류 · 예산 증액. 건별 승인은 해당 `ops`/`decision` 이슈의 코멘트가 원장이다.
- **금지**: archive 구 레포 수정(로컬 stack·data-portal 설정 편집은 예외, 푸시 없음) · 구 cosmai 플랫폼 확장 · secret 값 출력·`.env` 커밋 · `--no-verify`·force push · 브라우저를 흉내 내는 UA·TLS 지문 위장(수집기는 자기를 밝히는 UA — `collectors/commerce/contract.py`; 현재 값의 사정은 #35).

## 4. 관계
- **architect(설계 채널)와의 관계는 2026-08-25 에 끊었다.** 시드 입력은 레포 안으로 옮긴다(#79). 남는 접점은 **수신창구 하나**: 바깥에서 온 제안은 `memo` + `external` 라벨 이슈로 들어오고, 한 세션 안에 채널 이슈로 승격되거나 닫힌다. 승인 게이트·문서 미러링·정기 동기화는 없다. `contracts/` 가 정본이며 옛 설계 문서는 출생 기록일 뿐 개정을 따라오지 않는다.
- **포크 `cosmai-import-ydc`**: ydc 흡수의 원장은 포크 이슈. upstream 에는 PR 로만 온다(#83). 공유 자원(GPU · 운영 DB · `needs_runtime` 12연결)은 착수 코멘트의 `자원:` 으로 두 레포를 합쳐 `tool/issue ready` 가 본다. 포크는 이 파일을 편집하지 않는다.
- **운영 노하우**(`docker start` 는 옛 이미지 · 락과 구코드 · `stack/.env` · GPU 하나 · 스위트 잔여 컨테이너)는 `stack/README.md`.
