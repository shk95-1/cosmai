# HANDOFF — 2026-08-23 → 구현 세션

이 파일은 설계 세션(architect)이 구현 세션에 넘기는 지시다. 구현 세션은 이 저장소(`Main/cosmai`)를 cwd로 연다.

## 0. 먼저 읽을 것 (이 순서, 이것만)
1. 메모리 (자동 로드): `rebuild-plan-state`, `working-style-preferences`, `cosmai-goal-ladder`
2. `contracts/README.md` → `contracts/{entrypoints,interfaces,formats,secrets,versioning}.md` → `contracts/ddl/needs/001_needs.sql`
3. `/home/user1/github_prj/Main/architect/REBUILD.md` §2(요구사항 매트릭스)·§3(모듈 결정)·§4(순서)
4. `playbook/README.md` 최소 세트 표 (#1–#4 설치됨, #5–#10 남음), `eval/README.md`
슬라이스 상세(`architect/slice-*/README.md`)는 해당 패키지를 이식할 때 그 슬라이스만 읽는다.

## 1. 시작 전 확인
```
tool/checks/test          # 29 passed 여야 함 (일회용 postgres:18 → db/migrate.sh 2회 → 테스트)
git config core.hooksPath # .githooks
```

## 2. 현재 상태 (사실)
- 새 레포 = `slopindustries/cosmai` (private). 구 레포 6개 archive, 구 cosmai = `cosmai-old`.
- 운영 중인 스택(`Main/service/stack`, 구 레포 로컬 디렉터리에서 빌드)은 **계속 돈다**. 전환 전까지 깨뜨리지 않는다.
  - trend-radar: 로컬 master에 review_low 머지·크론 복구(03:30 review_low, 04:15 review, 05:00 stats, 05:30 new_product UTC). 첫 review_low ok(3,600행).
  - tubedepth: `watch` 컨테이너 **정지 상태**(팬아웃 상한 전까지), 중복 job 224k 취소, 자막 1,638 enqueue. worker·flatten·api는 Up.
  - cosmai-old: trendradar.rest 스케줄 10초(무의미 재수집) 아직 켜짐, tubedepth.rest 스케줄은 실패로 꺼짐.
- DB: `shared-postgres` 127.0.0.1:5434, DB `app`(trend_radar·tubedepth·**needs**), `cosmai`(cosmai). 슈퍼유저 `platform`. 읽기는 `docker exec shared-postgres psql -U platform -d app`. `needs`는 2026-08-23 `db/migrate.sh`로 적용(원장 `needs.schema_migration`)·`python -m db.seed`로 슬라이스 산출물 적재됨(15 테이블, 행 수는 `tests/test_seed.py` EXPECTED).
- secret: `~/.config/cosmai/env` (키 이름은 `contracts/secrets.md`; `NEEDS_DB_MIGRATOR`·`NEEDS_DB_RUNTIME` 추가됨). 값은 어디에도 쓰지 않는다.

## 3. 원칙 (짧게)
- 계약 우선: 인터페이스·DDL은 `contracts/`가 정본. 바꾸려면 계약+구현+테스트가 한 PR.
- 슬라이스가 증명한 경로만. 새 추상화는 두 번째 사용처가 생길 때.
- 워크트리 단위 병렬 + 서브에이전트. 조정 세션은 보고서만 읽는다. 워크트리: `git worktree add ../cosmai-wt/<unit> -b feat/<unit>`.
- 구 레포의 문서·훅·메타테스트·독스트링은 가져오지 않는다. 코드만. 필요한 관행은 `playbook/snippets/`에서.
- 커밋은 훅 통과로만(Conventional Commits, 제목 72자). `--no-verify`·force push 금지.
- 테스트는 실 Postgres·오프라인. 새 수집은 하지 않는다(기존 수집기가 돌고 있음). 분석은 DB를 reader로 읽는다.

## 4. 순서 (REBUILD §4; D3 승인됨 2026-08-23)
### 1단계 — `needs` 스키마 + 노출 (1일) — **완료 2026-08-23** (PostgREST 재시작·data-portal 재빌드만 승인 대기)
- `db/`: bootstrap(`db/bootstrap.sql`, schema=needs)을 `shared-postgres`에 적용하는 스크립트 + needs 롤 비밀번호를 env에 추가(`NEEDS_DB_MIGRATOR`, `NEEDS_DB_RUNTIME`, `.env`는 stack). `001_needs.sql` 적용 (migrator, SET ROLE owner).
- 적재기: `eval/`·`architect/slice-*/` CSV → `needs.*` (formats.md 매핑). 검증 = 행 수가 슬라이스 README와 일치.
- 노출: PostgREST `postgrest.env` `PGRST_DB_SCHEMAS`에 `needs` 추가 + anon SELECT는 `metrics_*`, `*_lexicon`, `product_ref`만; data-portal `SCHEMAS += needs`.
- 완료 기준: `curl 127.0.0.1:3000/metrics_need` 가 행을 돌려주고, `tool/checks/test` 녹색.
### 2단계 — 운영 수리 잔여 (병렬 가능)
- cosmai-old `cosmai.schedule` trendradar 행 비활성(10s 재수집 중단) — DB 행 변경.
- youtube 팬아웃 상한 설계는 3단계 이식과 함께(`collectors/youtube`). 그 전까지 watch는 정지 유지.
### 3단계 — `needs-analysis` 패키지 (2–3일) — 워크트리 4개 병렬
| 유닛 | 재료 | 계약 | 평가셋 |
|---|---|---|---|
| `analysis/linker` | slices/p2 (product_ref), slices/p3 (brand link) | `Linker` | product_match 80쌍, brand_link 120 |
| `analysis/extractor` + `polarity` | slices/suncare, slices/p1 (규칙 v2.2), slices/p9 (wish) | `Extractor`, `Polarity` | polarity 400, wish 160 |
| `analysis/aggregate` | slices/suncare metrics, slices/p1 metrics, slices/p2 rank_daily | `Aggregator` | 슬라이스 metrics.csv 재현(골든) |
| `cosmai` CLI (`collect`/`analyze`/`eval`/`lexicon`) | entrypoints.md | — | `--help` 스냅샷 |
- 각 유닛 완료 기준: 계약 시그니처 구현 + 해당 평가셋에서 `interfaces.md` 기준선 이상 + `analyze <stage>` 멱등 + 보고서 ≤30줄.
### 4단계 — LLM 극성 실험 (1일): `Polarity` 구현체 하나 더, 같은 400문장으로 비교. 넘으면 `polarity_version` 교체.
### 5단계 — 수집기 이식 (`collectors/commerce`·`youtube`·`naver`), review_low 보드 일반화(ASC는 3★까지), youtube 팬아웃 상한 → watch 재가동. 전환 시 `contracts/ddl/current` diff = 0, 행 수 일치, 직전 pg_dump.
### 6단계 — 결과 화면: data-portal 설정 1줄부터.

## 5. 하지 말 것
- archive된 구 레포 수정(로컬 trend-radar 크론은 이미 끝났고 예외). 구 cosmai 플랫폼(잡 큐·snapshot) 확장.
- 스택 컨테이너 재빌드·정지는 사용자 승인 후 (tubedepth watch 재가동 포함).
- secret 값 출력, `.env` 커밋.

## 6. 미결
- ~~D3~~ 승인됨(2026-08-23). 메모리 갱신 완료.
- 브링그린 알로에처럼 ≤2★가 300건을 넘는 제품은 review_low 6페이지로 부족 → 5단계에서 "3★ 등장까지" 규칙으로.
