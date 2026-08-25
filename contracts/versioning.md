# 버전 규칙

- `linker_version`, `extractor_version`, `polarity_version`, `aggregate` 는 패키지 상수 문자열. 형식 `rule-vX.Y` 또는 `llm-<model>-<yyyymmdd>`.
- 출력 행의 의미가 바뀌는 변경(사전 버전 업 포함)은 버전을 올리고, 같은 입력에 대해 **새 버전 행을 추가**한다 (자연키에 버전 포함된 테이블) 또는 재계산한다 (버전이 키에 없는 테이블은 `analyze` 가 해당 src 범위를 재생성).
- `need_mention` 은 2026-08-24(005)부터 **버전이 키에 있는 쪽**이다: `UNIQUE INDEX (src, ref, need_key, extractor_version, md5(sentence))`. 서로 다른 `extractor_version` 의 같은 문장은 두 행으로 공존하므로, 시드(`slice-*`)와 분석(`rule-v*`)이 같은 자리를 다투지 않는다. 오래된 자기 버전 행을 치우는 것은 여전히 `analyze` 의 DELETE(`extractor_version LIKE 'rule-v%'`) 몫이다.
- `sentence` 는 키에 원문이 아니라 `md5(sentence)` 로 들어간다: 길이 제한 없는 `text` 를 btree 키에 두면 긴 리뷰가 행 상한 2704B 를 넘겨 run 전체를 멈춘다(#5 운영 실증).
- `needs.analysis_run.versions` 가 그 run 의 모든 버전을 기록한다. 화면은 최신 run 만 본다.
- 예외 (A19): `metrics_need`·`metrics_wish`·`metrics_topic_quarter` 는 `*_version` 컬럼을 갖지 않는다 — 자연키의 `run_id` 가 `analysis_run.versions` 를 가리키고 그것이 그 행을 만든 모든 버전이다. run 을 거치지 않는 `product_denominator`·`rank_daily`·`price_event` 는 `aggregate_version` 을 가진다(002).
- `needs.panel_channel.version` 과 그것을 가리키는 `metrics_topic_quarter.panel_version` 은 **정수**다 — 위 문자열 규칙(`rule-vX.Y`)의 예외이고, 사전(`entity_lexicon`·`aspect_lexicon`)의 `version`·`active` 와 같은 모양이다. 패널은 코드가 아니라 시드가 바뀌는 것이라 판본이 적재 단위로 붙는다 (포크 #3, 값은 #31).
- 평가셋(`labeled_set`)은 버전이 없다. 라벨이 바뀌면 `labeled_at` 과 `labeler` 가 바뀐 새 행으로 대체한다.
- DDL 파일 번호 블록: upstream 은 `contracts/ddl/needs/006~019`, 포크 `cosmai-import-ydc` 는 `020~`. 원장(`needs.schema_migration`)에 남의 번호가 있어도 `db/migrate.sh` 는 체크아웃에 있는 파일만 훑으므로 배포는 무해하다 — 대신 그 객체는 `tool/checks/ddl-drift` 의 제외 목록(#75)에 선언한다.
