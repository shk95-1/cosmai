# contracts — 기계가 검사할 수 있는 것만

이 디렉터리는 설계서가 아니다. 여기 있는 것은 전부 **테스트나 스크립트가 대조할 수 있는 형태**여야 한다.

| 파일 | 내용 | 검사 방법 |
|---|---|---|
| `ddl/current/*.sql` | 2026-08-23 현재 DB의 실제 스키마 덤프 (app.trend_radar 13 · app.tubedepth 13 · cosmai.cosmai 12 테이블). 수집기 이식 후에도 **이 테이블 모양을 유지**해야 한다 | `pg_dump --schema-only` 재덤프 → diff |
| `ddl/needs/001_needs.sql` | 신규 `app.needs` 스키마 (17 테이블). 주석 3곳은 낡았다 — 아래 각주 | 마이그레이션 적용 후 `pg_catalog` 대조 테스트 |
| `ddl/needs/002_audit_additive.sql` | 2026-08-23 계약 감사(이슈 #17)가 요구한 **추가만** — 테이블 3(`need_key`, `category_map`, `product_ref_candidate`) + 컬럼 31. 합계 20 테이블 | 같은 테스트 + `tests/test_ddl_additive_only.py` |
| `ddl/needs/005_need_mention_natural_key.sql` | `need_mention` 자연키 교체 — btree 2704B 상한(#5 운영 실패)과 시드·분석 충돌(#12 안 A)을 한 번에 푼다. `UNIQUE (src, ref, need_key, sentence)` → `UNIQUE INDEX (src, ref, need_key, extractor_version, md5(sentence))`. **추가만의 유일한 예외**(사용자 승인 2026-08-24) | `tests/test_ddl_additive_only.py` 의 `SANCTIONED_DESTRUCTIVE` 화이트리스트 + `tests/test_need_mention_natural_key.py` |
| `ddl/needs/020_retrieval_chunk.sql` | 검색 유닛(#28)의 청크 저장소 `needs.retrieval_chunk` — 5칸 청크 계약(`analysis/retrieval/chunks.py` FIELDS) + 파생 2칸(`text_md5`·`chunked_at`). 번호 020 은 장수 브랜치 `feat/ydc-import` 의 블록이라 main 의 00N 과 파일명이 겹치지 않는다 | `tests/retrieval/test_contract.py` (컬럼·NOT NULL·GRANT·번호대) + `tests/test_ddl_additive_only.py` |
| `ddl/needs/*.sql` | (위와 같은 디렉터리) | 적용 경로는 `db/migrate.sh` 하나 (운영·테스트 공용); 적용 여부는 원장 `needs.schema_migration`에 기록 |
| `entrypoints.md` | CLI 진입점·종료 코드·run/fetch_log 공통 뷰 | CLI `--help` 스냅샷 + 뷰 존재 테스트 |
| `interfaces.md` | 분석 패키지 4개의 입출력 타입·수식, 평가 하네스가 넘어야 할 기준선 | `tests/test_contract_types.py` 가 `analysis/types.py` 와 대조 · `eval <task>` 가 기준선과 비교 |
| `formats.md` | 사전·평가셋 CSV/테이블 포맷 | 적재 스크립트가 검증 |
| `secrets.md` | secret 파일 경로와 키 이름 (값 없음) | 기동 시 키 존재 검사 |
| `versioning.md` | `*_version` 컬럼 규칙 | 코드 리뷰 |
| `../db/grants/needs_runtime_reader.sql` | 분석이 읽는 원천 테이블 12개의 SELECT 권한 | `tests/test_grants_reader.py` 가 `ddl/current` 와 대조 |

원칙: 인터페이스는 여기서 먼저 고정하고, 동작은 패키지 안에서 점진 구현·검증한다. 계약 변경은 PR 한 개에 계약+구현+테스트가 같이 온다.

## 001 주석 정정 (001 은 수정하지 않는다 — 추가만이 사전 승인 범위다)
- `001:8` "needs_reader: SELECT" — 그런 롤은 없다(T12). 읽기는 PostgREST `postgrest_anon` 화이트리스트(`db/grants/postgrest_anon_needs.sql`)와 분석용 `needs_runtime`(`db/grants/needs_runtime_reader.sql`) 둘뿐이다.
- `001:74` `labeled_set.task` 주석의 `aspect` — 평가셋도 기준선도 없어 뺐다(B11). 현행 목록은 `entrypoints.md`.
- `001:156` `low_complete` 주석 "RATING_ASC 표본에 3★이 섞임" — 실제 규칙은 `(low_collected < 150) or has_3star` 다(T5). `formats.md §표본 상수`.
- `001:105` `UNIQUE (src, ref, need_key, sentence)` — 005 가 갈아엎어 이제 거짓이다. 현행 자연키는 `UNIQUE INDEX (src, ref, need_key, extractor_version, md5(sentence))`. `ddl/needs/005_need_mention_natural_key.sql`.
