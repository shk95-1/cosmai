# contracts — 기계가 검사할 수 있는 것만

이 디렉터리는 설계서가 아니다. 여기 있는 것은 전부 **테스트나 스크립트가 대조할 수 있는 형태**여야 한다.

| 파일 | 내용 | 검사 방법 |
|---|---|---|
| `ddl/current/*.sql` | 2026-08-23 현재 DB의 실제 스키마 덤프 (app.trend_radar 13 · app.tubedepth 13 · cosmai.cosmai 12 테이블). 수집기 이식 후에도 **이 테이블 모양을 유지**해야 한다 | `pg_dump --schema-only` 재덤프 → diff |
| `ddl/needs/001_needs.sql` | 신규 `app.needs` 스키마 (17 테이블) | 마이그레이션 적용 후 `information_schema` 대조 테스트 |
| `entrypoints.md` | CLI 진입점·종료 코드·run/fetch_log 공통 뷰 | CLI `--help` 스냅샷 + 뷰 존재 테스트 |
| `interfaces.md` | 분석 패키지 4개의 입출력 타입, 평가 하네스가 넘어야 할 기준선 | `eval run <task>` 가 기준선과 비교 |
| `formats.md` | 사전·평가셋 CSV/테이블 포맷 | 적재 스크립트가 검증 |
| `secrets.md` | secret 파일 경로와 키 이름 (값 없음) | 기동 시 키 존재 검사 |
| `versioning.md` | `*_version` 컬럼 규칙 | 코드 리뷰 |

원칙: 인터페이스는 여기서 먼저 고정하고, 동작은 패키지 안에서 점진 구현·검증한다. 계약 변경은 PR 한 개에 계약+구현+테스트가 같이 온다.
