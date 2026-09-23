-- One current failure per collector and dataset. An unfinished run cannot supersede a completed
-- pass; a later completed ok or partial pass can. collector_health is a history, not a state table.
SELECT collector || ':' || dataset || ' failed (run ' ||
       coalesce(run_id, 'at ' || started_at) || ')'
FROM (
    SELECT collector, dataset, run_id, started_at, status,
           row_number() OVER (
               PARTITION BY collector, dataset
               ORDER BY finished_at DESC, started_at DESC, run_id DESC
           ) AS position
    FROM needs.collector_health
    WHERE finished_at IS NOT NULL AND dataset IS NOT NULL AND dataset <> ''
) latest
WHERE position = 1 AND status = 'failed'
ORDER BY collector, dataset
LIMIT 20;
