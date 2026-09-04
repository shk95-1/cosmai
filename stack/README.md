# stack — 운영 노하우 (실측으로 산 것)

계약은 `contracts/entrypoints.md` §스케줄·§운영 노브, 의도와 승인 경계는 `STATE.md`, 지금 도는 것은 `tool/status`. 여기는 그 셋이 말하지 않는 **함정**만 적는다. 결함은 이슈다 — 여기에 적지 않는다.

- **`docker start` 로 켜면 옛 이미지로 돈다.** 이미지를 바꿨으면 `docker compose up -d --force-recreate <service>`. 신 스택(`cosmai-*`)의 재생성은 코디네이터가 직접 한다(`STATE.md` §3).
- **`tool/stack-build` 2단계는 `stack/.env` 없이는 compose interpolation 에서 죽는다** — 빌드 실패가 아니라 변수 미설정이다. `.env` 는 경로만 담고 secret 값은 없다(`stack/env.example`).
- **analyze 동시성 락(`analysis/locks.py`)은 락 이전에 시작된 프로세스를 못 본다.** 컷오버 뒤 크론은 전부 락 안이므로, 해당하는 것은 호스트에서 옛 체크아웃으로 손 실행할 때뿐이다.
- **GPU 는 하나다. gemma4 크론 창은 매일 08:00–16:00 UTC(17:00–01:00 KST)다** — `stack/crontab.d/analyze` 의 `0 8` 줄이 그 창을 연다. `cosmai retrieval embed`(38만 청크, 유휴 GPU 20.6분)를 이 창에 걸치면 둘 다 5h38m 을 치른다(포크 실측, #28) — `embed` 는 창을 피해 돌린다. 폭 8h 는 실측 T 가 아니라 상한 추정이다(증분 패스의 T 는 미측정, 아는 값은 전량 패스의 6h44m 뿐 — #32): 조정자가 T 를 재면 폭도 같이 좁힌다. 어드바이저리 락은 다른 레포의 프로세스를 못 보므로 창은 문서로 정한다.
- **호스트 ollama 주소는 Tailscale 인터페이스다.** `OLLAMA_URL` 기본값 `http://100.102.193.98:11434` 는 WSL2 미러링이 물려받은 Windows 쪽 주소이고 **Tailscale 이 꺼지면 사라진다**. 브리지 게이트웨이·LAN·`host.docker.internal` 은 컨테이너 안에서 전부 닿지 않았다(2026-08-25 실측). 갈아 끼우는 자리는 `stack/.env`, 못 닿는 밤은 조용한 0건이 아니라 failed run 이다.
- **스위트 잔여 컨테이너**: `tool/checks/test` 는 워크트리마다 `cosmai-test-postgres-<port>`(tmpfs, RAM 점유)를 띄우고 trap 으로 지운다. 셸이 SIGKILL 로 죽으면 남는다 — `tool/status` 의 `test-leftovers` 절이 보여 주고, 같은 포트의 다음 실행은 소유 컨테이너 이름을 찍고 멈춘다. 다른 워크트리 것이면 지우지 말고 주인에게.
- **이미지는 retrieval extra 를 싣지 않는다**(`stack/Dockerfile` `uv sync --no-dev`). 컨테이너 안에서 `cosmai retrieval …` 은 `--help` 만 통과한다. 검색 크론(#55)이 생기는 날 upstream 이 싣는다.
- **이미지 베이스가 TLS 지문을 바꾼다**: OpenSSL 3.0 베이스는 oliveyoung 리뷰 API 에 막히고 3.5 베이스(현행)는 통과한다. `tests/stack/test_image_tls_stack.py` 가 하한을 지킨다. 전말은 #35.
- **`tool/checks/test` syncs `--extra retrieval`, `stack/Dockerfile` does not (until #55).** Cost is `kiwipiepy-model` (88 MB sdist, no wheel) plus `kiwipiepy` (11.5 MB), paid once per uv cache; every run after that is near zero.
