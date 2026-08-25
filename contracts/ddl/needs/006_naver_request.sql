-- 이슈 #44: DataLab ratio 는 요청 하나 안에서만 0~100 으로 재척도된다 (formats.md §NAVER DataLab).
-- 지금까지 그 요청 경계가 행에서 안 읽혔다 -- `terms` 는 그룹의 검색어 감사용일 뿐, startDate/endDate/
-- timeUnit 을 담지 않아 같은 그룹이라도 다른 실행(다른 창)에서 나온 값인지 구별하지 못했다.
-- request_key = 실제로 보낸 요청 바디(keywordGroups·startDate·endDate·timeUnit)의 canonical JSON
-- (json.dumps(sort_keys=True)) 을 sha256 한 hex digest (collectors/naver/parsing.py:datalab_request_key).
-- 같은 파라미터 -> 같은 키, endDate 가 하루라도 움직이면(그 창이 재척도되므로) 다른 키.
-- 표가 0행이라(#44 조정자 확인 2026-08-26) 백필이 필요 없고, 그래서 DEFAULT 없이 바로 NOT NULL 로 간다.
ALTER TABLE needs.naver_datalab_point ADD COLUMN request_key text NOT NULL;
