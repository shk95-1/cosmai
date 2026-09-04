# 버전 규칙

- `linker_version`, `extractor_version`, `polarity_version`, `aggregate` 는 패키지 상수 문자열. 형식 `rule-vX.Y` 또는 `llm-<model>-<yyyymmdd>`.
- 출력 행의 의미가 바뀌는 변경(사전 버전 업 포함)은 버전을 올리고, 같은 입력에 대해 **새 버전 행을 추가**한다 (자연키에 버전 포함된 테이블) 또는 재계산한다 (버전이 키에 없는 테이블은 `analyze` 가 해당 src 범위를 재생성).
- `need_mention` 은 2026-08-24(005)부터 **버전이 키에 있는 쪽**이다: `UNIQUE INDEX (src, ref, need_key, extractor_version, md5(sentence))`. 서로 다른 `extractor_version` 의 같은 문장은 두 행으로 공존하므로, 시드(`slice-*`)와 분석(`rule-v*`)이 같은 자리를 다투지 않는다. 오래된 자기 버전 행을 치우는 것은 여전히 `analyze` 의 DELETE(`extractor_version LIKE 'rule-v%'`) 몫이다.
- `sentence` 는 키에 원문이 아니라 `md5(sentence)` 로 들어간다: 길이 제한 없는 `text` 를 btree 키에 두면 긴 리뷰가 행 상한 2704B 를 넘겨 run 전체를 멈춘다(#5 운영 실증).
- `needs.analysis_run.versions` 가 그 run 의 모든 버전을 기록한다. 화면은 최신 run 만 본다.
- `needs.analysis_run.versions` 의 키는 `{linker, extractor, polarity, aggregate, lexicon, metric, judgement, evidence}` 이다. `metric` 은 **분기 입자의 정의 판본**이고 값은 ydc `trend.py` 의 `METRIC_VERSION` 을 그대로 든 `v0.2` 다 — 위 두 형식(`rule-vX.Y`·`llm-…`)의 예외인 것은 이것이 구현의 버전이 아니라 다섯 수식이 온 합의 문서(TEAM_DECISIONS_v0.2)의 이름이기 때문이다. `metrics_topic_quarter` 는 A19 로 `*_version` 컬럼을 갖지 않으므로, 그 행이 어느 정의로 만들어졌는지 답하는 자리는 `run_id` 가 가리키는 이 키 하나뿐이다. 001 의 주석은 이 목록보다 앞선 글이라 다섯만 적고 있고, DDL 은 추가만이므로 고치지 않는다 — `versions` 는 jsonb 라 키를 더하는 데 마이그레이션이 필요 없다 (포크 #5).
- `judgement` 는 **판정의 정의 판본**이고 값은 `metric` 과 같은 이유로 `v0.2` 다 — 유형 7종의 이름과 판정 순서와 다섯 상수(`TAU`·`DIFFUSION_TAU`·`EVIDENCE_FLOOR`·`W_EVIDENCE`·`W_SCORE`)가 온 합의 문서(TEAM_DECISIONS_v0.2 §3)의 이름이라 두 형식의 예외다. `metric` 과 **따로** 있는 것이 뜻이다: 지표를 다시 계산하지 않고 판정 기준만 바꾸는 것이 이 단계를 갈라 둔 이유이고, 그때 움직이는 것은 이 키 하나다. 판정 행은 지표 행과 같은 `run_id` 를 쓰므로 한 run 의 `versions` 가 두 키를 다 든다. ydc `judge.py` 는 같은 사실을 행마다 `tau`·`diffusion_tau`·`metric_version` 컬럼으로 적는데, A19 아래에서 그 자리는 run 이다 (포크 #40).
- `evidence` 는 **근거 선별의 정의 판본**이고 값은 `rule-v0.1` 이다 — `metric`·`judgement` 와 달리 두 형식의
  예외가 아닌 것이 뜻이다: 저 둘은 합의 문서(TEAM_DECISIONS_v0.2)의 이름이고 이것은 코드가 정한 규칙 넷
  (품질 플래그·제작자 제외·주제 출처·동점 2차 키, `interfaces.md` §근거)의 판본이라 팀 합의가 아니라 구현이
  진다. 근거 행은 판정 행과 같은 `run_id` 를 쓰므로 한 run 의 `versions` 가 세 키를 든다. 카드는 행을 만들지
  않아 키가 없다 (포크 #6).
- 예외 (A19): `metrics_need`·`metrics_wish`·`metrics_topic_quarter` 는 `*_version` 컬럼을 갖지 않는다 — 자연키의 `run_id` 가 `analysis_run.versions` 를 가리키고 그것이 그 행을 만든 모든 버전이다. run 을 거치지 않는 `product_denominator`·`rank_daily`·`price_event` 는 `aggregate_version` 을 가진다(002).
- 재현 (A19 의 따름, #144): `metrics_*` 한 행을 만든 언급 집합은 `analysis_run.versions.extractor` 의 판본 목록(`;` 분리)과 그 행의 축(`scope`·`need_key`·`month`·`product_ref`)으로만 되짚는다 — 다만 `scope='all'` 롤업 행의 `need_key` 는 이미 `needs.need_key.canonical` 로 접힌 이름이므로(A17) 그 칸만은 raw `need_mention.need_key` 가 아니라 그 canonical 로 걸어야 한다, 접힌 동의어 언급들이 통째로 빠지지 않게 — `versions.polarity` 를 같이 걸지 않는다, 한 `extractor_version` 이 polarity 두 판본을 담아 모집단이 아니라 그 run 의 polarity 단계가 쓴 판본이기 때문이다. 되짚기가 성립하는 것은 **그 run 뒤에 끝난 `analyze` run 이 없을 때뿐이다**: `analyze polarity` 가 `(src, month)` 단위로 지우고 다시 넣어(`analysis/polarity/pipeline.py`) 시간창도 워터마크도 남기지 않으므로, 그 뒤의 지표 run 은 모집단을 복원할 수 없다. 화면은 그런 칸에 언급 목록 대신 "이 run 뒤에 언급을 다시 쓴 실행이 있다"고 적는다 — 조용히 틀린 목록을 보이지 않는다.
- `needs.panel_roster.version`(판본 한 줄)과 그것을 FK 로 가리키는 `panel_channel.version`·`metrics_topic_quarter.panel_version` 은 **정수**다 — 위 문자열 규칙(`rule-vX.Y`)의 예외이고, 사전(`entity_lexicon`·`aspect_lexicon`)의 `version`·`active` 와 같은 모양이다. 패널은 코드가 아니라 시드가 바뀌는 것이라 판본이 적재 단위로 붙는다 (포크 #3, 값은 #31).
- 평가셋(`labeled_set`)은 버전이 없다. 라벨이 바뀌면 `labeled_at` 과 `labeler` 가 바뀐 새 행으로 대체한다.
- DDL 파일 번호 블록: upstream 은 `contracts/ddl/needs/006~019`, 포크 `cosmai-import-ydc` 는 `020~`. 원장(`needs.schema_migration`)에 남의 번호가 있어도 `db/migrate.sh` 는 체크아웃에 있는 파일만 훑으므로 배포는 무해하다 — 대신 그 객체는 `tool/checks/ddl-drift` 의 제외 목록(#75)에 선언한다.
