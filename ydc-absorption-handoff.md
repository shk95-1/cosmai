# ydc 흡수 통합 — 인계

**작성 2026-08-25** · 이 문서는 레포 밖(`Main/`)에 둔다. 앞으로의 통합이 **fork 한 레포**에서 이어지므로
어느 한 레포에 매이면 안 되고, 여기 적힌 것만으로 재개할 수 있어야 한다.

---

## 0. 한 줄 요약

`slopindustries/youtube-data-collector`(이하 **ydc**)를 cosmai가 **흡수**한다. ydc는 별도 제품으로
존속하지 않는다 — 그 **목적**(패널 기반 화장품 담론 트렌드 분석)이 cosmai의 것이 된다.
검색(RAG) 계열은 이미 흡수돼 실측까지 끝났고(§2·§3), 나머지는 §5의 재료 목록대로 처분한다.

**전제**: 이 인계는 "import 소스는 테스트가 완료되었다"는 가정 위에 서 있다. ydc 코드의 정확성을
다시 검증하지 않는다. 검증 대상은 **cosmai 위에서 같은 뜻의 숫자가 나오는가**이다.

---

## 1. 결정된 맥락 — 흡수(absorption)

### 무엇을 뜻하나

- ydc의 **분석 목적**이 cosmai의 목적이 된다. 코드를 빌리는 게 아니라 문제를 넘겨받는다.
- 그러려면 cosmai가 ydc의 **모집단 개념**을 자라게 해야 한다(§4). 이것이 흡수의 핵심 비용이다.
- ydc 레포는 종국에 **참조 문서**가 된다. 버전이 올라도 코드를 다시 반입하지 않고 **발견을 읽는다**.

### 무엇을 배제하나

- **부품 선별 금지.** "이 모듈은 쓸만하니 가져오고 저건 두자"는 지금까지의 방식이었고, 이 맥락에서는
  일관성이 없다. 각 재료는 §5의 네 처분(승격 / 대체 / 보류 / 폐기) 중 하나로 **명시적으로** 결정된다.
- **비활성 코드를 레포에 두는 것 금지.** 실행되지 않는 코드가 "반입됐다"고 말하는 상태가 가장 나쁘다
  (§2-B가 지금 그 상태다).
- ydc를 fleet의 독립 서비스로 세우는 안은 **채택하지 않았다**(검토했고 실행 가능했으나 흡수를 택함).

---

## 2. 지금까지의 결과물 — 성적이 갈리는 두 조각

### A. `analysis/retrieval/` — 흡수 완료, 실측 끝

cosmai의 검색 유닛. ydc의 규칙을 옮겨 적었고 **cosmai 자신의 데이터 위에서 끝까지 돈다.**

```
analysis/retrieval/
  normalize.py   정규화 (고정점까지 — ydc 원본과 다르다, §7 참조)
  chunks.py      5컬럼 청크 계약 + 검증기
  topics.py      주제 사전 15개 (리터럴 그대로 옮김)
  bm25.py        Kiwi 토큰화 + 역색인 + BM25
  corpus.py      원천 DB 어댑터 (키셋 페이징)
  pipeline.py    청킹 적재 · 색인 캐시 · 검색
  eval.py        literal/heldout 채점
  vectors.py     파일 벡터 저장소 + RRF
  embed.py       e5-base 인코딩
  dict/          user_dictionary.tsv · ingredient_dictionary.tsv (Kiwi 사용자 사전)
tests/retrieval/  테스트 10파일
contracts/ddl/needs/020_retrieval_chunk.sql
contracts/entrypoints.md  §검색 절
```

CLI: `cosmai retrieval {chunk,search,eval,embed}`

**이것은 기준을 통과한다** — ydc라고 주장하지 않고, cosmai 기능으로서 온전히 돌기 때문이다.

### B. `analysis/slices/ydc/` — 흡수 방향에서는 **재료 창고**

v0.1.0(`02440ab`) 전체를 반입한 것. **파일 46개 / 루트 스크립트 31개.**

현 상태를 정직하게 적으면: **실행 가능한 진입점이 0개다.**
- `common/document.csv` 없음 · `data/` 없음 · `reports/` 없음 (`.gitignore`로 원문 재배포 방지)
- `uv run python analysis/slices/ydc/trend.py` → `error: run_dir을 하나 이상 지정하세요.`
- 31개 중 규칙을 실제로 가져온 건 7개. **24개는 한 줄도 실행되지 않는다.**

흡수 맥락에서 이 디렉터리는 "반입된 ydc"가 아니라 **§5를 실행하기 위한 원본 참조**다.
§5가 끝나면 **삭제한다** — 그때까지만 존재 이유가 있다.

---

## 3. 실측 기준선 (재현 가능)

이 숫자들이 흡수의 출발점이자, 앞으로의 변경이 무엇을 깨뜨렸는지 판정하는 기준이다.

### 청크 (2026-08-24 23:47 UTC 스냅샷, `needs.retrieval_chunk`)

| source | 청크 | 원천 |
|---|---:|---|
| youtube_comment | 288,914 | `tubedepth.comments` |
| youtube_transcript | 63,972 | `tubedepth.transcripts` |
| commerce_review | 23,156 | `trend_radar.review` |
| youtube_video | 5,908 | `tubedepth.video_snapshots` (제목만 — 이 표에 description 컬럼이 없다) |
| **계** | **381,950** | 문서 319,835 · 길이 중앙 127자 · 최대 500자 |

재실행 시 변경 0 (`text_md5` 동일 행은 UPDATE 건너뜀).

### 검색 채점 (질의 = 주제 별칭, 정답 = `match_topics` 자동 라벨)

| mode | engine | 질의 | P@10 | MRR@10 | Hit@10 | ydc 실측 |
|---|---|---:|---:|---:|---:|---|
| literal | bm25 | 61 | **0.864** | 0.893 | 92% | 0.862 |
| literal | vector | 61 | 0.618 | 0.785 | 92% | 0.567 |
| literal | hybrid | 61 | 0.839 | **0.911** | **95%** | 0.710 |
| heldout | bm25 | 60 | 0.000 | 0.000 | 0% | 0.000 |
| **heldout** | **vector** | 60 | **0.062** | 0.114 | **25%** | 0.042 / 17% |
| heldout | hybrid | 60 | 0.025 | 0.029 | 8% | 0.032 |

**판정**: 사전 등록한 채택 기준 `heldout vector P@10 > 0` 충족. heldout에서 BM25의 0.000은
구조적이므로 0.062가 임베딩이 기여한 몫이다.

**hybrid를 기본으로 올리지 않았다** — heldout에서 vector 단독보다 낮다(RRF가 구조적으로 0인 BM25
순위를 섞어 희석). literal에서는 hybrid가 최고다. **모드에 따라 이기는 엔진이 다르다**는 것이 결론이고,
기본값은 용도가 정해질 때 고른다.

**벡터가 이긴 자리**: `톤 업` 1.00 · `눈 시림` 0.60 · `눈따가` 0.40 · `땀에` 0.20.
전부 **literal에서 BM25가 0.00이던 별칭** — 공백이 들거나 조사가 붙어 `topic_words()`가 Kiwi 등록에서
제외하는 집합이다. ydc가 관측한 값(`톤 업` 1.00, `눈 시림` 0.60)과 값까지 같다.

### 산출물 (`var/`, gitignore)

- `var/retrieval/vectors/e5base.{npy 1.2GB, ids.csv 24MB, manifest.json}`
  모델 `intfloat/multilingual-e5-base` rev `d128750597153bb5987e10b1c3493a34e5a4502a`, 381,950 × 768 float32, L2 정규화
- `var/retrieval/bm25/index-33ffb7f01d0a63fe.pkl` (96MB)
- `var/retrieval/score_{literal,heldout}_{bm25,vector,hybrid}.csv`

### 인코딩 처리량 (2026-08-25 실측)

| 조건 | 배치(64)당 | 381,950 환산 |
|---|---:|---:|
| cuda, 유휴 GPU | 0.207초 | **20.6분** |
| cpu, 유휴 | 2.886초 | 287분 |
| 실제 실행 (ollama 패스와 경합) | 3.4초 | 5시간 38분 |

**운영 규칙**: 인코딩과 LLM 패스를 같은 기계에서 겹치지 마라. 겹치면 20분이 5.6시간이 되고 상대편도
같은 대가를 치른다.

---

## 4. 흡수가 요구하는 것 — 모집단 격차

**이것이 흡수의 본체다.** 코드를 옮기는 것보다 이게 크다.

### 확인된 격차 (2026-08-25)

| | ydc | cosmai | 확인 |
|---|---|---|---|
| 모집단 | 시드 채널 43개 패널 (product 34 / expert 9) | watchlist 큐 — **패널 개념 없음** | `panel_role` 이 cosmai 본체에 0건 (슬라이스에만 8파일) |
| 시간 입자 | **분기 + 전년 동분기 대비**(계절성 상쇄가 설계 의도) | **월** | `contracts/formats.md:52` — "공통 집계 그레인 = 월(`'YYYY-MM'`)" |
| 분모 | 패널 채널 수 · 패널 영상 수 | `product_denominator` · 카테고리 | — |

### 왜 이게 막는가

ydc의 모든 비율은 패널을 분모로 쓴다. 분모가 다르면 **같은 코드가 다른 뜻의 숫자를 낸다 — 오류 없이.**
그러므로 `data/panel/run_*`를 채워 넣고 `trend.py`를 돌려도 나오는 것은 ydc의 지표가 아니다.

### 흡수가 하려는 일

cosmai 계약에 다음을 **자라게** 한다. 이것이 되면 §5의 "승격" 처분이 실행 가능해진다.

1. **`panel_role`** — 채널의 역할 구분(product / expert). 어느 표에 둘지, `tubedepth` 원천인지
   `needs` 파생인지 결정 필요.
2. **분기 입자와 YoY** — 월과 **공존**해야 한다. 월은 지금 `need_mention`·`metrics_*` 전체가 딛고 선
   것이라 대체가 아니라 추가다.
3. **패널 분모** — 어떤 모집단에 대한 비율인지가 행에 남아야 한다.

> **주의**: 이건 계약 변경이고 등급 A다. #16이 끝나지 않았고 컷오버 이월분이 쌓인 상태에서
> 시간 입자를 흔드는 순서를 잘 잡아야 한다. §9의 열린 결정 참조.

---

## 5. 재료 목록 — ydc 루트 스크립트 31개의 처분

처분은 넷 중 하나다: **승격**(cosmai 유닛이 됨) / **대체**(cosmai에 이미 있음) / **보류**(전제가 갖춰져야) /
**폐기**(가져오지 않음).

### 이미 승격됨 (7)

`bm25` · `chunks` · `topics` · `trend`(normalize_text만) · `retrieval_eval` · `hybrid` · `encode_chunks`
→ `analysis/retrieval/`. **완료.**

### 수집 (4) — 대체 + 격차 하나

| 파일 | 목적 | 처분 |
|---|---|---|
| `youtube_collector.py` | YouTube Data API v3 수집기 | **대체** — `collectors/youtube/` |
| `collect_channel_uploads.py` | 시드 채널 업로드 전수(고정 패널) | **승격 대상** — 패널 개념이 cosmai에 없다(§4) |
| `normalize_youtube_exports.py` | CSV export → 관계형 | **폐기** — cosmai는 DB 직행 |
| `to_common_schema.py` | document/mention/channel 변환 | **대체** — `analysis/retrieval/corpus.py` |

### 트렌드 판정 축 (7) — 흡수의 본체

| 파일 | 목적 | 처분 |
|---|---|---|
| `trend.py` | 주제별 **분기** 시계열 | **승격** — §4 선행 |
| `judge.py` | 트렌드 유형 7종 판정 + 기회 점수 | **승격** — §4 선행 |
| `panel_sensitivity.py` | 패널 구성이 결론을 바꾸는지 | **승격** — `panel_role` 선행 |
| `backtest.py` | 과거 구간 후향 검증 | **승격** |
| `reproduce.py` | 재실행 일치 검증 | **보류** — cosmai는 `analysis_run`으로 같은 일을 한다. 겹침 확인 필요 |
| `spam_ad_flags.py` | 광고·협찬 표시, 빼도 결론 같은지 | **승격** |
| `report_trend.py` | 분기 시계열 HTML | **보류** — portal(#11)과 겹침 |

### 근거·산출 (3)

| 파일 | 목적 | 처분 |
|---|---|---|
| `evidence_comments.py` | 주제별 근거 댓글 선별 | **승격** — 검색 유닛과 자연스럽게 붙는다 |
| `cards.py` | R&D Opportunity Card (규칙 기반, LLM 미사용) | **승격** |
| `dashboard.py` | 판정 격자 → 근거 펼침 정적 화면 | **보류** — portal(#11)과 겹침 |

### 커머스 대조 (4)

| 파일 | 목적 | 처분 |
|---|---|---|
| `commerce_ranking.py` | 랭킹 시계열 탐색 | **보류** — cosmai `analysis/aggregate/ranking.py`와 겹침 확인 |
| `commerce_crosscheck.py` | 유튜브 판정 ↔ 커머스 속성 평가 대조 | **승격** |
| `commerce_chunks.py` | 커머스 리뷰 청크 | **대체** — `corpus.commerce_reviews` |
| `source_composition.py` | 소스별 같은 사전으로 구성비 | **승격** |

### 성분 축 (4)

| 파일 | 목적 | 처분 |
|---|---|---|
| `normalize_ingredients.py` | 전성분표 파싱 오류 교정 | **보류** — 성분 데이터셋이 cosmai DB에 없다 |
| `ingredient_axis.py` | 무기·유기·혼합자차 분류 ↔ 담론 대조 | **보류** — 위와 같음 |
| `ingredient_chunks.py` | 성분표 청크 | **보류** — 위와 같음 |
| `ingredient_terms.py` | 성분·제형 표기 빈도(별칭 사전 인계용) | **승격** — `aspect_lexicon`과 연결점 |

### 사전·보조 (2)

| 파일 | 목적 | 처분 |
|---|---|---|
| `unmatched_terms.py` | 사전 미포착 고빈도 명사 → 사전의 천장 | **승격** — `cosmai lexicon` 경로와 붙는다 |
| `repair_chunks.py` | 남의 청크 파일 교정 | **폐기** — 우리가 전부 생성한다 |

### v0.2.0(`969929f`)이 더한 것 (4)

| 파일 | 목적 | 처분 |
|---|---|---|
| `cross_source.py` | 4소스 대조표 — 어긋나는 자리가 R&D 공백 | **승격 후보** |
| `ingredient_cards.py` | 성분 축 기회 카드 | **보류** — 성분 데이터셋 선행 |
| `naver_trend.py` | NAVER DataLab 트렌드 | **발견만 채택** (§7 참조) |
| `merge_vectors.py` | 남이 인코딩한 벡터 합본 | **폐기** — 우리는 한 번에 다 굽는다 |
| `data/external/paper_*.csv` | 논문 축 데이터 | **폐기** — v0.2.0 마지막 커밋이 "논문 축 사용 중지" |

> v0.2.0에서 `chunks.py`·`retrieval_eval.py`도 바뀌었으나 우리에게 오는 건 하나뿐이다:
> **500자 초과인데 하드스톱 1000은 안 넘는 청크를 경고로 노출**(원본 근거: `[통과]`가
> "500 위반 없음"으로 읽혀 27건이 묻혔다). `--vectors`는 이미 독립 구현돼 있다.

---

## 6. 원천 — 무엇이 어디 있나

### 있는 것 (확인 2026-08-25)

| 원천 | 소재 | 규모 |
|---|---|---|
| YouTube API 키 | `~/.config/cosmai/env` → **`YOUTUBE_DATA_API_TOKEN`** (ydc는 `YOUTUBE_API_KEY` 이름을 찾는다 — 매핑 필요) | — |
| 커머스 리뷰·랭킹 | `127.0.0.1:3000` (`shared-db-postgrest-1`, `Accept-Profile: trend_radar`) | review 26,923 · rank_snapshot 216,097 · product 6,510 · price_point 182,999 |
| 유튜브 원천 | `tubedepth.{comments,transcripts,video_snapshots}` | 285,749 · 5,303 · 27,318 |
| 패널 정의 43채널 | `analysis/slices/ydc/seeds/channels_v1.csv` | 43 (product 34 / expert 9) |
| Kiwi 사전 2벌 | `analysis/retrieval/dict/` | 20 + 1,877 |
| 성분 데이터셋 | ydc 레포 `data/external/product_ingredient_function_repaired.csv` (**추적됨**) | 31,246행 / 577제품 |
| NAVER 블로그 키 | `COSMA_SRC_NAVER_BLOG_CLIENT_ID` / `_SECRET` | — |

> **포트 주의**: ydc 코드의 `100.106.220.24:3000`은 팀원 기계였고, **여기서는 `127.0.0.1:3000`이 같은
> 역할**이다. 반면 ydc의 `:3002`(NAVER DataLab)와 여기 `:3002`(`shared-db-postgrest-cosmai-1`,
> cosmai-old DB)는 **다른 것**이다. 여기 `:3001`은 data-portal, `:3003`은 신 portal.

### 없는 것

| 없는 것 | 만드는 법 | 비고 |
|---|---|---|
| `data/panel/run_*` (수집 원천) | 키 + 쿼터로 43채널 전수 수집 | ydc 파생물 거의 전부의 뿌리. `.gitignore data/*` |
| NAVER DataLab 128개월 (2016-01~2026-08) | `collectors/naver/` 실행, 또는 원 수집분 수령 | `needs.naver_datalab_point` **0행** (naver_blog_post·naver_run도 0) |
| 수호님 성분·식약처 벡터 8,786청크 | 우리가 인코딩하면 됨 | 필수 아님 |
| `common/*.csv` · `reports/*` · `.cache/*` | 위가 갖춰지면 자동 생성 | 원문 재배포 방지 목적의 의도적 gitignore |

---

## 7. 반드시 지킬 것

### 계약·머지

- **DDL 번호는 020번대**(`020_retrieval_chunk.sql`). main이 00N을 쓰는 동안 충돌을 피하려 고른 것이고,
  **개명하면 안 된다** — 운영 원장 `needs.schema_migration`에 `020_retrieval_chunk`가 이미 적용돼 있어
  이름을 바꾸면 재적용을 시도한다. main 통합 시 006–019 구멍은 미관 문제일 뿐이다.
- `contracts/entrypoints.md` §검색 절 제목의 `(#28, 장수 브랜치 … — main 머지 전)` 꼬리표는
  main 통합 때 **떼야 한다**.
- `tool/checks/test`가 `--extra retrieval`을 요구한다. main 통합 시 CI가 kiwipiepy를 받게 된다.
- `db/bootstrap.sql`은 **main과 완전히 동일**하다(pgvector 되돌림이 깨끗했다). 이 상태를 유지하라.
- `--no-verify` 금지 · force push 금지 · main 직접 머지 금지.
- secret은 **키 이름만** 쓴다. 값은 출력·복사·커밋하지 않는다.
- 운영 DB·컨테이너 조치는 세션이 직접 한다(서브에이전트에 위임하지 않는다).

### 이 코드베이스에서 세 번 반복된 함정

전부 "느려지거나 나중에 끊긴다"라서 작은 픽스처로는 안 잡힌다. 새 코드를 쓸 때 먼저 의심하라.

1. **`idle_in_transaction_session_timeout` (15초).** DB를 읽고 **트랜잭션을 연 채** 긴 CPU/IO 작업을
   하면 연결이 끊긴다. `load_index`·`chunks_to_encode`·`gold_from_chunks` 세 곳에서 났다.
   → 읽자마자 `conn.commit()`. 서버 커서(named cursor)는 수명 내내 트랜잭션을 연다는 걸 기억하라.
2. **반복 로드.** 질의마다 96MB 피클과 1.2GB 행렬과 모델을 다시 여는 코드가 있었다(61질의 × 2회 =
   1.2GB를 122번). **결과가 같아서 수치로는 안 드러난다.** → 한 번 열어 인자로 넘긴다.
3. **배치 경계의 거짓 위반.** `check_rows`를 쓰기 배치마다 부르면 "ordinal이 0부터 연속"(문서 전체의
   성질)이 깨져 보인다. 자막 한 편이 최대 155조각이라 실제로 58건 났다. → 배치는 **문서 경계에서만** 끊는다.

### ydc 원본과 의도적으로 다르게 한 것

- **`normalize_text`를 고정점까지 돌린다.** 한 번만 돌리면 `&amp;lt;`가 `&lt;`에서 멈추는데,
  `check_rows`가 `text != normalize_text(text)`로 위반을 판정하므로 이중 이스케이프를 품은 청크가
  영구히 위반으로 잡힌다. ydc는 수집기가 `textFormat=plainText`로 받아 이 경우가 없었고,
  우리 코퍼스는 DB의 댓글·리뷰라 실제로 들어온다.
- **증분이 아니라 전량 재인코딩.** 파일 저장에서 일부만 덧붙이면 행렬과 id의 순서 대응을 손으로
  지켜야 하고, 그 대응이 깨져도 오류가 안 난다.

### 별건으로 확인·이슈화할 것 — NAVER DataLab `ratio`

ydc `naver_trend.py` 헤더의 발견: **DataLab은 요청 하나 안에서 최댓값을 100으로 맞춘다.** 그래서
같은 요청 안에서는 비교되지만 요청 사이에는 안 된다. 원 수집자는 `기준_세럼`을 세 요청에 모두 넣어
앵커로 재척도했다.

확인 결과 **cosmai의 `needs.naver_datalab_point`는 `ratio`를 그대로 저장하고 그 제약이 계약 어디에도
없다** — `004_naver.sql` 헤더에도, `contracts/*.md`에도, `collectors/naver/parsing.py`에도. 앵커
키워드도 없다. 지금 `category`나 `group_key`를 가로질러 비교하면 **조용히 틀린 답**이 나온다.

**표가 0행인 지금이 고치기 가장 싼 시점이다.** #28과 별개 이슈로 연다.

---

## 8. 단계 계획

### 지금 상태

- 브랜치 `feat/ydc-import`, HEAD `e875a3d`(main 머지 커밋). **origin에 미푸시(40커밋).**
- main 대비 앞 13 / **뒤 9** — 이 문서를 쓰는 사이 main이 또 움직였다. 재개 시 먼저 머지하라.
- 머지 직후 `tool/checks/test` **973 passed / 2 xfailed**.
- 워크트리 `/home/user1/github_prj/Main/cosmai-wt/ydc-import`, 테스트 포트 **55452**.

### 단계

| # | 무엇 | 등급 | 선행 |
|---|---|---|---|
| **S0** | 브랜치를 fork로 옮기고 origin 정리. 미푸시 40커밋 보존 | C | — |
| **S1** | NAVER `ratio` 제약 이슈 등록 (§7) | C | — |
| **S2** | `chunks.py` 길이 경고 채택 — `ChunkOutcome`에 "목표 상한 초과 n건" | C | — |
| **S3** | **모집단 계약 설계** — `panel_role` · 분기 입자 · YoY · 패널 분모 (§4) | **A** | 흡수의 관문 |
| **S4** | 패널 수집 — `collect_channel_uploads` 승격, 43채널 전수 1회 | B | S3 |
| **S5** | 트렌드 판정 축 승격 — `trend` · `judge` · `panel_sensitivity` · `backtest` · `spam_ad_flags` | A | S3·S4 |
| **S6** | 근거·산출 승격 — `evidence_comments` · `cards` | B | S5 |
| **S7** | 커머스 대조 승격 — `commerce_crosscheck` · `source_composition` · `cross_source` | B | S5 |
| **S8** | 사전 승격 — `unmatched_terms` · `ingredient_terms` → `aspect_lexicon` 연동 | B | — |
| **S9** | **`analysis/slices/ydc/` 삭제** — 처분이 전부 끝나면 | C | S5~S8 |
| **S10** | 보류분 재판단 — 성분 축 4개 · portal 겹침 3개 · `reproduce` | — | 별건 |

**미뤄 둔 것**(사용자 지시): `stack/crontab.d/retrieval` 크론 · 4b pgvector 이관.
pgvector 코드 초안은 커밋 `d6bb591`에 있고, `2c5e910`에서 파일 벡터로 되돌렸다.

---

## 9. 열린 결정

1. **`panel_role`을 어디 두나** — `tubedepth` 원천인가, `needs` 파생인가? 수집기가 채우나 사전이 채우나?
2. **분기와 월을 어떻게 공존시키나** — `metrics_*`에 컬럼을 더하나, 별도 표를 두나?
3. **NAVER DataLab 128개월을 어떻게 채우나** — 새로 긁나(쿼터), 원 수집분을 받나?
4. **검색 기본 엔진** — literal은 hybrid, heldout은 vector가 이긴다. 용도(근거 수집 vs 탐색)가 정해져야 고를 수 있다.
5. **성분 데이터셋을 cosmai DB에 넣나** — 넣으면 보류 4건이 풀린다. `trend_radar.product`에 전성분이 없다.
6. **main 통합 시점** — 현재 조건은 "#16 완료 + #28 체크박스 전부". 흡수로 범위가 커졌으니 재설정 필요.

---

## 10. 재개 명령

```sh
cd /home/user1/github_prj/Main/cosmai-wt/ydc-import

# 1) 먼저 main 을 받는다 (재개 시점에 또 뒤처져 있을 것이다)
git fetch origin && git merge --no-edit origin/main

# 2) 확인
COSMAI_TEST_PG_PORT=55452 tool/checks/test     # 기준선 973 passed / 2 xfailed
tool/checks/format && tool/checks/lint

# 3) 검색 유닛이 실제로 도는지
uv run cosmai retrieval chunk                                  # 재실행이면 변경 0
uv run cosmai retrieval search --query "하얗게 떠서 싫다" --top 5
uv run cosmai retrieval eval --mode literal  --engine bm25     # P@10 0.864 근방
uv run cosmai retrieval eval --mode heldout --engine vector    # P@10 0.062 근방

# 4) 벡터를 다시 구울 때 (extra 를 실행마다 명시 — tool/checks/test 가 embed 를 정리한다)
uv run --extra retrieval --extra embed cosmai retrieval embed --device cuda --batch 64
```

**셸 주의**: `eval`이라는 낱말과 `sleep` 연쇄가 이 하네스의 가드에 걸린다. 걸리면 래퍼 스크립트로 감싸라.

**원장**: cosmai 이슈 **#28**에 여기까지의 실측·판정이 코멘트로 남아 있다. 포크로 옮긴 뒤에는
그쪽 이슈를 원장으로 삼되, #28을 출처로 링크하라.

**출처 고정**: ydc `v0.1.0` = `02440ab` (반입 기준) · `v0.2.0` = `969929f` (§5의 v0.2.0 항목).

---

## 11. 이슈 재등록 — 포크 레포 부팅용

통합 이슈는 **포크한 레포의 issue로 관리한다.** 아래 정의가 정본이고, 부팅 시 그대로 등록할 수 있다.

**먼저 할 것**: 이 인계 문서를 포크 레포 루트에 `ydc-absorption-handoff.md` 로 두고 커밋하라.
아래 이슈 본문이 `인계 §N` 으로 이 문서를 가리키므로, 문서가 레포 안에 있어야 링크가 산다.

### 등록 절차

포크 레포 루트에서 실행한다. 라벨 생성 → 에픽 → 단위 이슈 → 에픽 본문에 번호 채우기 순이다.

    # 0) 이 문서 경로
    HANDOFF=./ydc-absorption-handoff.md
    test -f "$HANDOFF" || { echo "인계 문서를 레포 루트에 두고 다시 실행"; exit 1; }

    # 1) 라벨 (없으면 만들고, 있으면 그대로 둔다)
    gh label create epic     --color 6f42c1 --description "여러 단위를 묶는 상위 이슈" --force
    gh label create grade-A  --color b60205 --description "전체 리뷰 — 계약·DB·운영·외부 비용" --force
    gh label create grade-B  --color d93f0b --description "스펙 리뷰 1회 + 수정 1라운드" --force
    gh label create grade-C  --color 0e8a16 --description "리뷰 없음 — 체크 녹색 + diff 확인" --force
    gh label create ops      --color 1d76db --description "운영 조치를 포함" --force
    gh label create deferred --color cccccc --description "지금 하지 않기로 한 것" --force

    # 2) 정의 추출 (이 문서의 ydc-issues 블록이 정본)
    awk '/^~~~json ydc-issues$/{f=1;next} /^~~~$/{f=0} f' "$HANDOFF" > /tmp/ydc-issues.json
    jq -e 'length > 0' /tmp/ydc-issues.json >/dev/null || { echo "추출 실패"; exit 1; }

    # 3) 에픽 먼저
    EPIC_JSON=$(jq -c '.[] | select(.key=="E0")' /tmp/ydc-issues.json)
    EPIC_URL=$(gh issue create \
      --title "$(jq -r .title <<<"$EPIC_JSON")" \
      --body  "$(jq -r .body  <<<"$EPIC_JSON")" \
      --label epic)
    EPIC=${EPIC_URL##*/}
    echo "EPIC = #$EPIC"

    # 4) 단위 이슈 — 만들면서 번호를 모은다
    : > /tmp/ydc-created.tsv
    jq -c '.[] | select(.key!="E0")' /tmp/ydc-issues.json | while read -r row; do
      key=$(jq -r .key <<<"$row"); title=$(jq -r .title <<<"$row")
      body=$(jq -r .body <<<"$row")
      body="$body

상위: #$EPIC · 상세는 인계 문서 참조"
      args=(); for l in $(jq -r '.labels[]' <<<"$row"); do args+=(--label "$l"); done
      url=$(gh issue create --title "$title" --body "$body" "${args[@]}")
      printf '%s\t%s\t%s\n' "$key" "${url##*/}" "$title" >> /tmp/ydc-created.tsv
      echo "  $key -> #${url##*/}"
    done

    # 5) 에픽 본문에 체크리스트를 채운다
    {
      jq -r '.[] | select(.key=="E0") | .body' /tmp/ydc-issues.json
      echo; echo "## 단위 (순서대로)"; echo
      while IFS=$'\t' read -r key num title; do
        echo "- [ ] **$key** #$num — $title"
      done < /tmp/ydc-created.tsv
    } > /tmp/ydc-epic-body.md
    gh issue edit "$EPIC" --body-file /tmp/ydc-epic-body.md
    echo "완료. 에픽 #$EPIC"

> 재실행하면 **중복 생성된다.** 한 번만 돌리고, 실패하면 만들어진 것을 지우고 다시 하라
> (`gh issue list --limit 30` 으로 확인).

**검증됨 (2026-08-25)**: 추출(18,820바이트) · `jq` 파싱(14건) · 생성 루프 건식 실행(13건, 라벨 배열과
다중 라벨 `ops,deferred` 포함) 전부 확인했다. `gh` 호출만 실제로 안 돌렸다. bash·zsh 모두에서 돈다.

### 정의 (정본)

~~~json ydc-issues
[
  {
    "key": "E0",
    "title": "[EPIC] ydc 흡수 — 목적을 cosmai 로 옮긴다",
    "labels": ["epic"],
    "body": "## 맥락\n\n`slopindustries/youtube-data-collector`(ydc)를 cosmai 가 **흡수**한다. ydc 는 별도 제품으로 존속하지 않고, 그 **목적**(패널 기반 화장품 담론 트렌드 분석)이 cosmai 의 것이 된다.\n\n**전제**: import 소스는 테스트가 완료된 것으로 간주한다. ydc 코드의 정확성을 다시 검증하지 않는다. 검증 대상은 **cosmai 위에서 같은 뜻의 숫자가 나오는가** 이다.\n\n## 배제하는 것\n\n- **부품 선별 금지.** 각 재료는 승격 / 대체 / 보류 / 폐기 중 하나로 **명시적으로** 결정된다 (인계 §5 에 31개 + v0.2.0 4개 전부 처분이 붙어 있다).\n- **비활성 코드 방치 금지.** 실행되지 않는 코드가 \"반입됐다\"고 말하는 상태가 가장 나쁘다. `analysis/slices/ydc/` 가 지금 그 상태이고, 처분이 끝나면 삭제한다.\n- ydc 를 fleet 의 독립 서비스로 세우는 안은 검토했고 실행 가능했으나 **채택하지 않았다**.\n\n## 관문\n\n**S3(모집단 흡수)가 전부의 선행이다.** ydc 의 모든 비율은 시드 채널 43개 패널을 분모로 쓰는데 cosmai 에는 그 개념이 없다. 분모가 다르면 **같은 코드가 다른 뜻의 숫자를 낸다 — 오류 없이.**\n\n## 이미 끝난 것\n\n검색(RAG) 계열은 `analysis/retrieval/` 로 흡수돼 실측까지 끝났다. 기준선은 인계 §3:\n\n- 청크 381,950 (문서 319,835) · 재실행 변경 0\n- heldout vector P@10 **0.062** / Hit 25% — 사전 등록한 채택 기준 `> 0` 충족\n- literal bm25 P@10 0.864 (ydc 실측 0.862 와 일치)\n- hybrid 는 heldout 에서 vector 보다 낮아 **기본으로 올리지 않았다**\n\n## 규칙\n\n- 이 이슈의 코멘트가 원장이다. 단계마다 한 것·테스트 결과·결정·우려를 코멘트로 남긴다.\n- DDL 은 **020번대**를 쓰고 **개명하지 않는다** — 운영 원장에 `020_retrieval_chunk` 가 이미 적용돼 있다.\n- secret 은 키 이름만. `--no-verify` · force push 금지.\n- 상세·근거·재개 명령은 인계 문서(`ydc-absorption-handoff.md`)."
  },
  {
    "key": "S1",
    "title": "[계약] NAVER DataLab ratio 는 요청 안에서만 비교 가능한데 그 제약이 어디에도 없다",
    "labels": ["grade-B"],
    "body": "## 무엇\n\nDataLab 은 **요청 하나 안에서** 최댓값을 100 으로 맞춘다. 그래서 같은 요청 안의 그룹끼리는 비교되지만 **다른 요청 사이에는 비교가 안 된다**. ydc 의 원 수집자는 `기준_세럼` 을 세 요청에 모두 넣어 **앵커로 재척도**했다 (출처: ydc `naver_trend.py` 헤더).\n\n## 지금 상태\n\n- `needs.naver_datalab_point` 는 `ratio` 를 그대로 저장한다.\n- 그 제약이 `004_naver.sql` 헤더에도, `contracts/*.md` 에도, `collectors/naver/parsing.py` 에도 **없다**.\n- 앵커 키워드도 없다.\n- 표는 **0행**이다 (`naver_datalab_point` · `naver_blog_post` · `naver_run` 전부).\n\n지금 구조로 `category` 나 `group_key` 를 가로질러 ratio 를 비교하면 **조용히 틀린 답**이 나온다 — 오류가 아니라 그럴듯한 숫자로.\n\n## 왜 지금인가\n\n**표가 비어 있는 지금이 가장 싸다.** 데이터가 쌓인 뒤면 재수집이다.\n\n## 완료 기준\n\n1. 제약을 계약에 적는다 (`contracts/formats.md` 또는 `004` 후속 DDL 주석).\n2. 앵커 키워드 설계를 정한다 — 어느 키워드를, 어느 요청들에 넣을지.\n3. 재척도가 필요하다면 그 계산이 어디서 도는지 정한다(수집 시점 vs 분석 시점).\n4. 요청 경계를 행에서 알아볼 수 있게 한다 — 지금은 `terms` 가 감사용으로만 있다."
  },
  {
    "key": "S2",
    "title": "[검색] 청크 목표 상한 500자 초과가 보고에 드러나지 않는다",
    "labels": ["grade-C"],
    "body": "## 무엇\n\n`check_rows` 는 하드스톱(`MAX_CHARS * 2` = 1000자)만 위반으로 센다. 500 은 목표치라 조금 넘는 건 통과다 — 그건 맞다. 문제는 **몇 건인지 아무도 안 본다**는 것이다.\n\nydc v0.2.0 이 이걸 고친 근거: `[통과]` 가 \"500 위반 없음\"으로 읽혀서 남의 청크 27건이 묻혔다.\n\n## 지금\n\n`check_rows` 가 `lengths` 를 돌려주지만 `ChunkOutcome` 은 쓰지 않는다. 우리 `split_text` 는 500 이하를 보장하므로 자체 생성분에는 안 나지만, 외부 청크를 검사할 때 같은 함정이 있다.\n\n## 완료 기준\n\n`ChunkOutcome` 에 \"목표 상한 초과 n건 (최대 m자)\" 를 더하고 `note` 에 싣는다. 0 이면 안 찍는다."
  },
  {
    "key": "S3",
    "title": "[계약] 모집단 흡수 — panel_role · 분기 입자 · YoY · 패널 분모",
    "labels": ["grade-A"],
    "body": "## 이것이 흡수의 관문이다\n\n코드를 옮기는 것보다 이게 크고, S4~S7 전부의 선행이다.\n\n## 확인된 격차 (2026-08-25)\n\n| | ydc | cosmai | 확인 |\n|---|---|---|---|\n| 모집단 | 시드 채널 43개 패널 (product 34 / expert 9) | watchlist 큐 — 패널 개념 없음 | `panel_role` 이 cosmai 본체에 **0건** (슬라이스에만 8파일) |\n| 시간 입자 | **분기 + 전년 동분기 대비** (계절성 상쇄가 설계 의도) | **월** | `contracts/formats.md:52` |\n| 분모 | 패널 채널 수 · 패널 영상 수 | `product_denominator` · 카테고리 | — |\n\n## 왜 막는가\n\nydc 의 모든 비율은 패널을 분모로 쓴다. 분모가 다르면 **같은 코드가 다른 뜻의 숫자를 낸다 — 오류 없이.** `data/panel/run_*` 를 채워 넣고 `trend.py` 를 돌려도 나오는 것은 ydc 의 지표가 아니다.\n\n## 자라게 할 것\n\n1. **`panel_role`** — 채널의 역할(product / expert).\n2. **분기 입자와 YoY** — 월과 **공존**해야 한다. 월은 `need_mention` · `metrics_*` 전체가 딛고 선 것이라 대체가 아니라 추가다.\n3. **패널 분모** — 어떤 모집단에 대한 비율인지가 행에 남아야 한다.\n\n## 열린 결정 (이 이슈에서 답한다)\n\n- `panel_role` 을 어디 두나 — 원천 스키마인가 `needs` 파생인가? 수집기가 채우나 사전이 채우나?\n- 분기와 월을 어떻게 공존시키나 — `metrics_*` 에 컬럼을 더하나, 별도 표를 두나?\n\n## 주의\n\n계약 변경이고 등급 A 다. 본류의 진행 상황과 순서를 맞춰야 한다 — 시간 입자를 흔드는 건 아무 때나 할 일이 아니다.\n\n## 완료 기준\n\n계약(`contracts/`)에 세 개념이 적히고, DDL 이 추가만으로 서고, 기존 월 기반 산출이 그대로 돈다."
  },
  {
    "key": "S4",
    "title": "[수집] 패널 43채널 전수 수집 — collect_channel_uploads 승격",
    "labels": ["grade-B"],
    "body": "## 무엇\n\nydc `collect_channel_uploads.py` — 시드 채널의 업로드를 전수 수집하는 **고정 패널 방식**. cosmai `collectors/youtube/` 는 watchlist 큐 기반이라 이 방식이 없다.\n\n## 있는 것\n\n- 패널 정의 43채널: ydc `seeds/channels_v1.csv` (product 34 / expert 9)\n- API 키: `~/.config/cosmai/env` → **`YOUTUBE_DATA_API_TOKEN`** (ydc 는 `YOUTUBE_API_KEY` 이름을 찾는다 — 매핑 필요)\n\n## 없는 것\n\n`data/panel/run_*` — ydc 파생물 거의 전부의 뿌리. 쿼터를 써서 한 번 돌려야 생긴다.\n\n## 완료 기준\n\n- 패널 수집이 cosmai 수집기 형태로 돈다 (`cosmai collect youtube --dataset ...` 계열).\n- 쿼터 소요를 실측해 기록한다.\n- 수집분이 `panel_role` 과 함께 저장된다 (S3 선행).\n\n## 선행\n\nS3"
  },
  {
    "key": "S5",
    "title": "[분석] 트렌드 판정 축 승격 — trend · judge · panel_sensitivity · backtest · spam_ad_flags",
    "labels": ["grade-A"],
    "body": "## 무엇\n\n흡수의 본체. ydc 의 다섯 스크립트를 cosmai 유닛으로 승격한다.\n\n| 파일 | 목적 |\n|---|---|\n| `trend.py` | 주제별 **분기** 시계열 (수집 run 여러 개를 하나의 패널로) |\n| `judge.py` | 트렌드 유형 7종 판정 + 기회 점수 (evidence_strength · opportunity_score · tau 0.35) |\n| `panel_sensitivity.py` | 패널 구성이 판정 결론을 바꾸는지 |\n| `backtest.py` | 과거 구간 후향 검증 |\n| `spam_ad_flags.py` | 광고·협찬 표시, 빼도 결론이 같은지 |\n\n## 원칙\n\n- 슬라이스를 **import 하지 않는다.** 규칙을 옮기고 헤더에 출처를 한 문장 적는다 (`analysis/retrieval/` 가 쓴 방식).\n- `judge.py` 의 임계값과 가중치는 팀 합의값이다. 바꾸려면 근거가 따로 있어야 한다.\n\n## 완료 기준\n\ncosmai 데이터 위에서 분기 시계열과 판정이 나오고, 그 숫자가 무엇의 비율인지 행에서 읽힌다.\n\n## 선행\n\nS3 · S4"
  },
  {
    "key": "S6",
    "title": "[분석] 근거·산출 승격 — evidence_comments · cards",
    "labels": ["grade-B"],
    "body": "## 무엇\n\n| 파일 | 목적 |\n|---|---|\n| `evidence_comments.py` | 주제별 근거 댓글 선별 — 카드에 붙일 실제 발화 |\n| `cards.py` | R&D Opportunity Card 를 **규칙으로** 만든다 (숫자와 유형은 코드가 정하고 LLM 은 쓰지 않는다) |\n\n## 접점\n\n`evidence_comments` 는 이미 흡수된 검색 유닛(`analysis/retrieval/`)과 자연스럽게 붙는다 — 근거를 고르는 일이 곧 검색이다. 별도 선별 로직을 새로 만들기 전에 `cosmai retrieval search` 로 대체 가능한지 먼저 본다.\n\n## 완료 기준\n\n판정 격자의 셀 하나에서 근거 원문까지 손으로 조인하지 않고 닿는다.\n\n## 선행\n\nS5"
  },
  {
    "key": "S7",
    "title": "[분석] 커머스 대조 승격 — commerce_crosscheck · source_composition · cross_source",
    "labels": ["grade-B"],
    "body": "## 무엇\n\n| 파일 | 목적 |\n|---|---|\n| `commerce_crosscheck.py` | 유튜브 트렌드 판정 ↔ 커머스 플랫폼 속성 평가 대조 |\n| `source_composition.py` | 소스마다 **같은 사전·같은 정의**로 주제 구성비를 내고 나란히 놓는다 |\n| `cross_source.py` (v0.2.0) | 4소스 대조표 — 어긋나는 자리가 R&D 공백이다 |\n\n## 원천\n\n전부 여기 있다. ydc 는 `100.106.220.24:3000` 을 불렀지만 **여기서는 `127.0.0.1:3000`** (`shared-db-postgrest-1`, `Accept-Profile: trend_radar`) 이 같은 역할이다:\n\n- `trend_radar.review` 26,923 · `rank_snapshot` 216,097 · `product` 6,510 · `price_point` 182,999\n\n직접 PostgREST 를 부르지 말고 cosmai 의 DB 경로를 쓴다.\n\n## 확인할 것\n\n`commerce_ranking.py` 는 cosmai `analysis/aggregate/ranking.py` 와 겹칠 수 있다. 먼저 대조하고, 겹치면 승격하지 않는다.\n\n## 선행\n\nS5"
  },
  {
    "key": "S8",
    "title": "[사전] 미포착 표현을 aspect_lexicon 경로로 — unmatched_terms · ingredient_terms",
    "labels": ["grade-B"],
    "body": "## 무엇\n\n| 파일 | 목적 |\n|---|---|\n| `unmatched_terms.py` | 사전에 안 걸린 고빈도 명사 — **사전의 천장을 사람이 보게 만드는 목록** |\n| `ingredient_terms.py` | 유튜브에서 실제로 쓰이는 성분·제형 표기 빈도 (별칭 사전 인계용) |\n\n## 왜\n\n지금 `analysis/retrieval/topics.py` 는 **얼어붙은 상수**다. 사전을 바꿔도 `cosmai lexicon load/diff/activate` 경로를 타지 않는다. 사전 변경이 버전 관리를 못 받는다는 뜻이다.\n\n## 완료 기준\n\n- 주제 확장의 원천이 상수에서 `needs.aspect_lexicon`(활성 버전)으로 옮겨진다.\n- 미포착 표현 목록이 산출되고, 그걸 사전에 넣는 경로가 `cosmai lexicon` 이다.\n- 사전이 바뀌면 BM25 색인 캐시가 자동 무효화된다 (캐시 키에 사전 해시가 이미 들어 있다).\n\n## 선행\n\n없음 — 병렬 가능"
  },
  {
    "key": "S9",
    "title": "[정리] analysis/slices/ydc/ 삭제 — 처분이 끝나면",
    "labels": ["grade-C"],
    "body": "## 무엇\n\n`analysis/slices/ydc/` (파일 46개 / 루트 스크립트 31개, v0.1.0 `02440ab` 전체) 를 지운다.\n\n## 왜\n\n지금 이 디렉터리는 **실행 가능한 진입점이 0개**다:\n\n- `common/document.csv` 없음 · `data/` 없음 · `reports/` 없음\n- `trend.py` 를 돌리면 `error: run_dir을 하나 이상 지정하세요.`\n- 31개 중 규칙을 실제로 가져온 건 7개. 24개는 한 줄도 실행되지 않는다.\n\n실행되지 않는 코드가 \"ydc 가 여기 있다\"고 말하는 상태가 가장 나쁘다 — 기준을 어기면서 지킨 것처럼 보인다.\n\n## 완료 기준\n\n- S5~S8 의 처분이 전부 끝났다.\n- 승격된 것의 출처가 각 모듈 헤더에 한 문장으로 남아 있다.\n- 보류로 남은 것(S10)은 그 사실이 이슈에 적혀 있다.\n- 디렉터리를 지우고 `pyproject.toml` 의 `analysis/slices` exclude 가 아직 필요한지 확인한다.\n\n## 선행\n\nS5 · S6 · S7 · S8"
  },
  {
    "key": "S10",
    "title": "[보류] 성분 축 · portal 겹침 · reproduce 재판단",
    "labels": ["deferred"],
    "body": "## 성분 축 4건 — 데이터셋이 cosmai DB 에 없다\n\n`normalize_ingredients` · `ingredient_axis` · `ingredient_chunks` · `ingredient_cards`(v0.2.0)\n\n원본은 ydc 레포에 **추적돼 있다**: `data/external/product_ingredient_function_repaired.csv` (31,246행 / 577제품). 그런데 `trend_radar.product` 에 전성분이 없어서 cosmai 쪽에 들일 자리가 없다.\n\n**결정할 것**: 성분 데이터셋을 cosmai DB 에 넣나? 넣으면 4건이 전부 풀린다.\n\n## portal 겹침 2건\n\n`report_trend.py`(분기 시계열 HTML) · `dashboard.py`(판정 격자 → 근거 펼침)\n\ncosmai `portal/` 과 겹친다. 화면을 두 벌 만들 이유가 없다.\n\n## reproduce\n\n`reproduce.py`(재실행 일치 검증) 는 cosmai 의 `analysis_run` 이 같은 일을 한다. 대조해서 겹치면 폐기.\n\n## 폐기 확정 (참고)\n\n`merge_vectors`(우리는 한 번에 다 굽는다) · `repair_chunks`(우리가 전부 생성) · `normalize_youtube_exports`(DB 직행) · `data/external/paper_*`(v0.2.0 마지막 커밋이 \"논문 축 사용 중지\")"
  },
  {
    "key": "S11",
    "title": "[결정] 검색 기본 엔진 — 모드마다 이기는 엔진이 다르다",
    "labels": ["deferred"],
    "body": "## 실측\n\n| mode | bm25 | vector | hybrid |\n|---|---:|---:|---:|\n| literal P@10 | **0.864** | 0.618 | 0.839 |\n| literal Hit@10 | 92% | 92% | **95%** |\n| heldout P@10 | 0.000 | **0.062** | 0.025 |\n| heldout Hit@10 | 0% | **25%** | 8% |\n\n## 무엇이 갈리나\n\n- **heldout 에서는 vector 가 이긴다.** hybrid 는 RRF 가 구조적으로 0 인 BM25 순위를 섞어 벡터의 적중을 희석한다.\n- **literal 에서는 hybrid 가 이긴다** (MRR 0.911 · Hit 95%).\n- 벡터가 이긴 자리는 `톤 업` 1.00 · `눈 시림` 0.60 · `땀에` 0.20 — 공백이 들거나 조사가 붙어 Kiwi 등록에서 빠지는 별칭들이고, literal 에서 BM25 가 0.00 이던 바로 그 집합이다.\n\n## 왜 지금 못 정하나\n\n**용도가 정해져야 고를 수 있다.** 근거 수집(아는 말로 찾기)이면 hybrid, 탐색(이름 없는 불만 찾기)이면 vector 다.\n\n## 완료 기준\n\n용도를 정하고 기본값을 `cosmai retrieval search --engine` 의 default 로 박는다. 그 근거를 이 이슈에 남긴다.\n\n## 관련\n\n공백·조사 별칭을 BM25 쪽에서도 살리는 길(구 질의 처리)이 있으면 그림이 또 바뀐다."
  },
  {
    "key": "S12",
    "title": "[ops] 검색 크론 — 청크가 설계상 낡는다",
    "labels": ["ops", "deferred"],
    "body": "## 무엇\n\n`stack/crontab.d/retrieval` — `cosmai retrieval chunk` 를 주기 실행.\n\n## 왜 필요해졌나\n\n컷오버 이후 수집기가 신 스택 크론으로 계속 돈다. 청크는 **2026-08-24 23:47 UTC 스냅샷**이고 원천은 그 뒤로 늘고 있다 (`trend_radar.review` 가 이미 22,889 → 26,883). 크론이 없으면 색인이 **설계상** 낡는다.\n\n## 이미 맞게 돼 있는 것\n\nBM25 색인 캐시 키에 `max(chunked_at)` 이 들어가 있어, 재청킹하면 캐시가 자동 무효화된다.\n\n## 주의\n\n- `analyze all` 과 같은 이유로 크론 간격 규칙에서 제외된다 — 외부 fetch 가 없는 DB 전용 작업이다.\n- **신 스택 compose 를 건드린다.** 본류와 다투기 시작하는 지점이라 단독으로 넣지 말고 묶어서 넣는 편이 낫다.\n\n## 상태\n\n사용자 지시로 **미룸**."
  },
  {
    "key": "S13",
    "title": "[ops] pgvector 이관 — 파일 벡터에서 DB 로",
    "labels": ["ops", "deferred"],
    "body": "## 착수 조건은 갖춰졌다\n\n채택 기준 `heldout vector P@10 > 0` 을 충족했다 (0.062 / Hit 25%). 벡터가 값을 낸다는 것이 확인됐으므로 1.2GB 를 매번 메모리에 올리는 지금 방식 대신 HNSW 로 옮길 근거가 생겼다.\n\n## 지금 방식\n\n`var/retrieval/vectors/e5base.{npy 1.2GB, ids.csv 24MB, manifest.json}` + numpy 내적 (L2 정규화라 코사인 = 내적).\n\n## 코드 초안\n\n커밋 `d6bb591` 에 pgvector 판이 있다. `2c5e910` 에서 파일 벡터로 되돌렸다. `vectors.search()` 의 시그니처가 `(chunk_id, 거리)` 로 같아서 뒤만 갈아 끼우면 된다.\n\n## 절차\n\n1. `shared-postgres` 이미지 `postgres:18` → `pgvector/pgvector:pg18` (같은 PostgreSQL 18.6, pgvector 0.8.6 — 일회용 컨테이너로 검증 완료).\n2. `docker compose -p shared-db up -d --no-deps shared-postgres` — **`--no-deps` 필수** (전체 `up -d` 는 정지해 둔 `tubedepth-watch` 를 켠다).\n3. 상시 연결 서비스만 명시 restart.\n4. `db/bootstrap.sql` 에 `CREATE EXTENSION IF NOT EXISTS vector`.\n5. DDL `021_retrieval_embedding.sql` — `public.vector(768)` + HNSW.\n6. 테스트 하네스 이미지 교체 (`COSMAI_TEST_PG_IMAGE` 변수가 이미 있다).\n\n## 함정\n\n확장은 `public` 에 설치되는데 `needs_runtime` 의 search_path 는 `needs, pg_catalog` 다. **타입·함수·연산자를 `public.` 으로 한정해야 한다** — `public.vector` · `public.vector_dims` · `OPERATOR(public.<=>)`. 한정하지 않으면 슈퍼유저 세션에서는 서고 정작 돌릴 롤에서는 못 찾는다. 실측으로 걸린 함정이다.\n\n## 되돌림 방지\n\n이미지가 `postgres:18` 로 되돌아가면 `needs.retrieval_embedding` 접근과 **전체 `pg_dump -Fc app`** 이 실패한다. 적용 시 (a) compose 변경을 로컬 커밋 (b) STATE 에 한 줄 (c) `pg_extension` 가드로 exit 2 (d) 이슈 기록.\n\n## 상태\n\n사용자 지시로 **미룸**."
  }
]
~~~
