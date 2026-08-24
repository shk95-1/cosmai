# cosmai

화장품 소비자 니즈 분석 시스템 — 수집 → 원본 보존 → 정규화 → 분석 → 결과.

2026-08-23 `architect/REBUILD.md`의 재구성 사양에 따라 새로 시작한 모노레포다. 기존 레포(`cosmai-old`, `trend-radar`, `yt-scrapper`, `Research_Paper`, `stack`, `data-portal`)는 archive(읽기전용)되었고, 이 저장소가 **코드만** 옮겨 심는다 — 문서·훅·메타테스트·개발 철학은 가져오지 않으며 `playbook/`에 따로 추출한다.

## 구조 (계약 우선)

| 디렉터리 | 역할 | 출처 |
|---|---|---|
| `contracts/` | 기계가 검사할 수 있는 계약만: DDL, 엔트리 규약, run/fetch_log 형태, 사전·평가셋 포맷, 분석 패키지 인터페이스 | 신규 |
| `collectors/commerce/` | 커머스 4사 수집기 (+ `review_low` 보드 일반화) | trend-radar `src/` |
| `collectors/youtube/` | YouTube 수집기 (팬아웃 상한, transcript 복구) | yt-scrapper(tubedepth) `src/` |
| `collectors/naver/` | DataLab · blog 수집기 (config 행 + collect) | cosmai-old `apps/addons/collector.naver.*` + outbound 정책 |
| `analysis/` | linker · extractor · polarity(LLM 삽입점) · aggregate | `architect/slice-*/` 스크립트 통합 |
| `db/` | 스키마별 마이그레이션 + 초기화 한곳 (app.trend_radar, app.tubedepth, cosmai, **app.needs**) | 각 레포 migrations + stack/init |
| `stack/` | compose · 크론 · 환경 — 배선 전부 | stack |
| `eval/` | labeled_set 660 · 회귀 픽스처 (제품 매핑 80쌍 등) | `architect/slice-*/` |
| `playbook/` | 기존 레포에서 추출한 개발 방법론 카탈로그 (채택/변형/제외) | 추출 |

`stack/` 의 이미지는 **빌드가 곧 검사다**: `stack/Dockerfile` 의 마지막 `RUN` 이 이미지 안에서 `cosmai --help` · `db/migrate.sh --help` · `ls contracts/ddl/needs/*.sql` · site-packages 를 통한 `scope_threshold()` 임포트를 실행한다. 그 빌드가 성공했다는 것이 #10 조건 2 의 "이미지 안 동작 확인" 의 근거다 — 따로 돌려 볼 절차가 없다. 스케줄러(supercronic)는 `stack/Dockerfile.cron` 이 그 이미지 위에 얹는다.

## 원칙
1. 슬라이스가 증명한 경로만 정식화한다 (`architect/REBUILD.md` §2 매트릭스).
2. 인터페이스는 계약 우선, 동작은 점진 구현·검증.
3. 사전·평가셋은 파일이 아니라 버전 있는 테이블.
4. LLM은 한 지점(리뷰 극성)에만, 400문장 평가셋을 넘을 때만.
5. secret은 `~/.config/cosmai/env` 경로만 참조, 값은 저장소에 없다.
