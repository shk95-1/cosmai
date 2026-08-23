# 버전 규칙

- `linker_version`, `extractor_version`, `polarity_version`, `aggregate` 는 패키지 상수 문자열. 형식 `rule-vX.Y` 또는 `llm-<model>-<yyyymmdd>`.
- 출력 행의 의미가 바뀌는 변경(사전 버전 업 포함)은 버전을 올리고, 같은 입력에 대해 **새 버전 행을 추가**한다 (자연키에 버전 포함된 테이블) 또는 재계산한다 (`need_mention` 처럼 버전이 키에 없는 테이블은 `analyze` 가 해당 src 범위를 재생성).
- `needs.analysis_run.versions` 가 그 run 의 모든 버전을 기록한다. 화면은 최신 run 만 본다.
- 평가셋(`labeled_set`)은 버전이 없다. 라벨이 바뀌면 `labeled_at` 과 `labeler` 가 바뀐 새 행으로 대체한다.
