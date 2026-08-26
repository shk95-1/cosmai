# secrets

- 파일: `~/.config/cosmai/env` (KEY=VALUE, mode 600). 값은 저장소·로그·문서 어디에도 쓰지 않는다.
- 컴포넌트가 읽는 키:
  - collectors/naver: `COSMA_SRC_NAVER_BLOG_CLIENT_ID`, `COSMA_SRC_NAVER_BLOG_CLIENT_SECRET`
  - collectors/youtube: `YOUTUBE_DATA_API_TOKEN` (trending 전용), tubedepth API 키 `COSMA_SRC_TUBEDEPTH_API_KEY` (api 보호 시)
  - db: `COSMA_DB_MIGRATOR`, `COSMA_DB_RUNTIME` (+ needs 롤 비밀번호는 같은 파일에 `NEEDS_DB_MIGRATOR`, `NEEDS_DB_RUNTIME` 로 추가)
  - collectors/commerce (`collectors/commerce/storage/db.py`): `TREND_RADAR_DB_RUNTIME` — `trend_radar_runtime` 롤 전용. 구 스택이 그 롤에 자기 `.env` 값으로 여전히 붙으므로 `COSMA_DB_RUNTIME` 과 갈라 둔다(#29 — 공유 시 인증 실패).
  - collectors/youtube (`collectors/youtube/storage/db.py`): `TUBEDEPTH_DB_RUNTIME` — `tubedepth_runtime` 롤 전용. 같은 이유로 `COSMA_DB_RUNTIME`, `TREND_RADAR_DB_RUNTIME` 어느 쪽과도 값이 다르다(#29).
  - analysis/polarity (LLM): `CLAUDE_API_KEY` (Anthropic API 키; SDK 기본 env 이름이 아니므로 코드가 명시적으로 넘긴다). 예산 하드스톱은 코드 상수 `LLM_BUDGET_USD = 10.0`, 누적은 `needs.llm_usage`(DDL 003 — 002 는 감사 보강이 먼저 썼다).
  - 미사용(보류): `COSMA_SRC_OPENALEX_API_KEY` (paper-radar 스택 밖), `*_SMOKE_*`, `*_PROBE_*`
- **secret 이 아닌 것**: ollama 주소(OLLAMA_URL, 기본 모델 `gemma4:latest`)는 비밀이 아니라 노브다. 값은 `stack/env.example` → `stack/.env` 에 있고 compose 가 analyze 컨테이너에 넘긴다. 이 파일이 백틱으로 이름 붙인 키에는 레포 어디에도 값이 붙을 수 없으므로(`tests/stack/test_stack_wiring.py` 의 secret 검사) 그 이름은 여기서 백틱을 쓰지 않는다.
- 기동 시 필요한 키의 **존재만** 검사하고 없으면 키 이름을 말하고 종료한다.
