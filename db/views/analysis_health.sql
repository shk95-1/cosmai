-- run 한 줄에 시작·끝·상태·행 수. contracts/entrypoints.md §공통 운영 뷰(collector_health)의 분석판이다.
-- Only two tables carry a run_id, so those are the only ones whose row count can be counted here --
-- need_mention/wish_mention do not hang off a run (versioning.md A19), so each stage's own count of
-- what it made rides in `note` as name=value (analysis/pipeline.py).
-- db/migrate.sh re-applies this on every deploy. CREATE OR REPLACE only succeeds when the column
-- names, order and types stay the same, so DROP goes first -- a deploy that widens the view must not
-- stop with exit 1.

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
