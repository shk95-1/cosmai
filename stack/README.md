# stack — 운영 노하우 (실측으로 산 것)

계약은 `contracts/entrypoints.md` §스케줄·§운영 노브, 의도와 승인 경계는 `STATE.md`, 지금 도는 것은 `tool/status`. 여기는 그 셋이 말하지 않는 **함정**만 적는다. 결함은 이슈다 — 여기에 적지 않는다.

- **`docker start` 로 켜면 옛 이미지로 돈다.** 이미지를 바꿨으면 `docker compose up -d --force-recreate <service>`. 신 스택(`cosmai-*`)의 재생성은 코디네이터가 직접 한다(`STATE.md` §3).
- **`tool/stack-build` 2단계는 `stack/.env` 없이는 compose interpolation 에서 죽는다** — 빌드 실패가 아니라 변수 미설정이다. `.env` 는 경로만 담고 secret 값은 없다(`stack/env.example`).
- **analyze 동시성 락(`analysis/locks.py`)은 락 이전에 시작된 프로세스를 못 본다.** 컷오버 뒤 크론은 전부 락 안이므로, 해당하는 것은 호스트에서 옛 체크아웃으로 손 실행할 때뿐이다.
- **GPU 는 하나다.** `cosmai retrieval embed`(38만 청크, 유휴 GPU 20.6분)와 gemma4 패스가 겹치면 5h38m 이 되고 상대편도 같은 대가를 치른다(포크 실측). gemma4 크론 창은 `stack/crontab.d/analyze` 주석과 #32 — `embed` 는 그 창을 피해 돌린다. 어드바이저리 락은 다른 레포의 프로세스를 못 보므로 창은 문서로 정한다.
- **스위트 잔여 컨테이너**: `tool/checks/test` 는 워크트리마다 `cosmai-test-postgres-<port>`(tmpfs, RAM 점유)를 띄우고 trap 으로 지운다. 셸이 SIGKILL 로 죽으면 남는다 — `tool/status` 의 `test-leftovers` 절이 보여 주고, 같은 포트의 다음 실행은 소유 컨테이너 이름을 찍고 멈춘다. 다른 워크트리 것이면 지우지 말고 주인에게.
- **이미지는 retrieval extra 를 싣지 않는다**(`stack/Dockerfile` `uv sync --no-dev`). 컨테이너 안에서 `cosmai retrieval …` 은 `--help` 만 통과한다. 검색 크론(#55)이 생기는 날 upstream 이 싣는다.
- **이미지 베이스가 TLS 지문을 바꾼다**: bookworm(OpenSSL 3.0)은 oliveyoung 리뷰 API 에 막히고 trixie(3.5)는 통과한다. `tests/stack/test_image_tls_stack.py` 가 하한을 지킨다. 전말은 #35.
