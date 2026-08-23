# secrets

- 파일: `~/.config/cosmai/env` (KEY=VALUE, mode 600). 값은 저장소·로그·문서 어디에도 쓰지 않는다.
- 컴포넌트가 읽는 키:
  - collectors/naver: `COSMA_SRC_NAVER_BLOG_CLIENT_ID`, `COSMA_SRC_NAVER_BLOG_CLIENT_SECRET`
  - collectors/youtube: `YOUTUBE_DATA_API_TOKEN` (trending 전용), tubedepth API 키 `COSMA_SRC_TUBEDEPTH_API_KEY` (api 보호 시)
  - db: `COSMA_DB_MIGRATOR`, `COSMA_DB_RUNTIME` (+ needs 롤 비밀번호는 같은 파일에 `NEEDS_DB_MIGRATOR`, `NEEDS_DB_RUNTIME` 로 추가)
  - 미사용(보류): `COSMA_SRC_OPENALEX_API_KEY` (paper-radar 스택 밖), `*_SMOKE_*`, `*_PROBE_*`
- 기동 시 필요한 키의 **존재만** 검사하고 없으면 키 이름을 말하고 종료한다.
