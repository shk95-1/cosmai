-- run 한 줄에 시작·끝·상태·행 수. contracts/entrypoints.md §공통 운영 뷰(collector_health)의 분석판이다.
-- 행 수를 셀 수 있는 것은 run_id 를 가진 두 표뿐이다 — need_mention·wish_mention 은 run 에 매달리지
-- 않으므로(versioning.md A19) 각 단계가 만든 수는 note 가 이름=값으로 나른다 (analysis/pipeline.py).
-- db/migrate.sh 가 배포마다 다시 적용한다. CREATE OR REPLACE 는 컬럼 이름·순서·타입이 그대로일 때만
-- 성공하므로 DROP 을 앞세운다 — 뷰를 넓히는 배포가 exit 1 로 멈추지 않게.

DROP VIEW IF EXISTS needs.analysis_health;
CREATE VIEW needs.analysis_health AS
SELECT
    r.run_id,
    split_part(r.note, ' ', 1)                                  AS stage,
    r.started_at,
    r.finished_at,
    r.status,
    extract(epoch FROM r.finished_at - r.started_at)::int       AS seconds,
    r.versions ->> 'linker'                                     AS linker_version,
    r.versions ->> 'extractor'                                  AS extractor_version,
    r.versions ->> 'polarity'                                   AS polarity_version,
    r.versions ->> 'aggregate'                                  AS aggregate_version,
    r.versions -> 'lexicon'                                     AS lexicon,
    coalesce(n.rows, 0)                                         AS metrics_need,
    coalesce(w.rows, 0)                                         AS metrics_wish,
    r.note
FROM needs.analysis_run r
LEFT JOIN (SELECT run_id, count(*) AS rows FROM needs.metrics_need GROUP BY run_id) n
       ON n.run_id = r.run_id
LEFT JOIN (SELECT run_id, count(*) AS rows FROM needs.metrics_wish GROUP BY run_id) w
       ON w.run_id = r.run_id;

GRANT SELECT ON needs.analysis_health TO needs_runtime;
