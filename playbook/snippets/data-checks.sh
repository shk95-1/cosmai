#!/bin/sh
# origin: service/trend-radar/tool/checks/data (skeleton; the queries are per-schema and live in db/<schema>/checks.sql)
# reuse: a person runs it after a change to what gets collected. hygiene = malformed, fails; placeholder = a shape that
#        has been wrong before, reported only. Exit 69 = could not check (no DB), never confused with "checked and found".
set -e
. tool/checks/prerequisite
if [ -z "${DATA_CHECK_URL:-}" ]; then
  printf '· DATA_CHECK_URL is not set, skipping the data checks (libpq URL, reader role)\n'; exit 69
fi
require_command psql
schema=${1:?"usage: tool/checks/data <schema>"}
sql="db/$schema/checks.sql"; [ -f "$sql" ] || { printf '✗ %s missing\n' "$sql" >&2; exit 1; }

# checks.sql must define:  with checks(family, name, n) as ( ... )   -- nothing after the CTE.
# family 'hygiene'     : a malformed value (rating outside 0..5, future timestamp, markup in a body, run without scope)
# family 'placeholder' : well-formed but a shape that has lied before (one distinct value across a board,
#                        a signed column never negative, a column entirely null, every product stopping on a round number)
checks=$(cat "$sql")
printf '→ data %s\n' "$schema"
psql "$DATA_CHECK_URL" -v ON_ERROR_STOP=1 -X -q -c "
$checks
select family, name, n, case when n = 0 then 'ok' else 'LOOK' end as verdict from checks order by family, name;"

malformed=$(psql "$DATA_CHECK_URL" -v ON_ERROR_STOP=1 -X -q -tAc "
$checks
select coalesce(sum(n), 0) from checks where family = 'hygiene';")
if [ "$malformed" != "0" ]; then
  printf '\033[31m✗ %s malformed rows. These are not judgement calls.\033[0m\n' "$malformed" >&2; exit 1
fi
printf '\033[32m✓ nothing malformed.\033[0m Any LOOK above is a shape that has been wrong before — go and look.\n'
