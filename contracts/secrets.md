# secrets

- 파일: `~/.config/cosmai/env` (KEY=VALUE, mode 600). 값은 저장소·로그·문서 어디에도 쓰지 않는다.
- 컴포넌트가 읽는 키:
  - collectors/naver: `COSMA_SRC_NAVER_BLOG_CLIENT_ID`, `COSMA_SRC_NAVER_BLOG_CLIENT_SECRET`
  - collectors/youtube: `YOUTUBE_DATA_API_TOKEN` (trending 전용), tubedepth API 키 `COSMA_SRC_TUBEDEPTH_API_KEY` (api 보호 시)
  - db: `COSMA_DB_MIGRATOR`, `COSMA_DB_RUNTIME` (+ needs 롤 비밀번호는 같은 파일에 `NEEDS_DB_MIGRATOR`, `NEEDS_DB_RUNTIME` 로 추가)
  - analysis/polarity (LLM): `CLAUDE_API_KEY` (Anthropic API 키; SDK 기본 env 이름이 아니므로 코드가 명시적으로 넘긴다). 예산 하드스톱은 코드 상수 `LLM_BUDGET_USD = 10.0`, 누적은 `needs.llm_usage`(DDL 003 — 002 는 감사 보강이 먼저 썼다).
  - 선택: `OLLAMA_URL` (기본 `http://localhost:11434`, 모델 `gemma4:latest`; 배관 테스트 전용, 채택 판정에 쓰지 않는다)
  - 미사용(보류): `COSMA_SRC_OPENALEX_API_KEY` (paper-radar 스택 밖), `*_SMOKE_*`, `*_PROBE_*`
- 기동 시 필요한 키의 **존재만** 검사하고 없으면 키 이름을 말하고 종료한다.
