#!/bin/sh
# Applies db/bootstrap.sql then contracts/ddl/needs/*.sql to $container/$db -- the one path
# production and the test harness both use to create the needs schema.
set -eu

container=shared-postgres
db=app
superuser=platform

while [ $# -gt 0 ]; do
    case "$1" in
        --container) container=$2; shift 2 ;;
        --db) db=$2; shift 2 ;;
        --superuser) superuser=$2; shift 2 ;;
        *) echo "needs: unknown argument: $1" >&2; exit 1 ;;
    esac
done

secret_file=${COSMAI_SECRET_FILE:-$HOME/.config/cosmai/env}
[ -f "$secret_file" ] || {
    echo "needs: missing secret file $secret_file (need NEEDS_DB_MIGRATOR, NEEDS_DB_RUNTIME)" >&2
    exit 1
}

read_secret() {
    # Only the key name ever reaches a message; the value is never echoed or logged.
    value=$(grep "^$1=" "$secret_file" | tail -1 | cut -d= -f2-)
    [ -n "$value" ] || { echo "needs: missing key in $secret_file: $1" >&2; exit 1; }
    printf '%s' "$value"
}

migrator_password=$(read_secret NEEDS_DB_MIGRATOR)
runtime_password=$(read_secret NEEDS_DB_RUNTIME)

# a. roles + schema + runtime grants (idempotent; passwords are not rewritten for existing roles).
docker exec -i "$container" psql -U "$superuser" -d "$db" -X -q -v ON_ERROR_STOP=1 \
    -v schema=needs -v database="$db" \
    -v migrator_password="$migrator_password" -v runtime_password="$runtime_password" \
    < db/bootstrap.sql

migrator_psql() {
    docker exec -i -e PGPASSWORD="$migrator_password" "$container" \
        psql -U needs_migrator -d "$db" -X -q -v ON_ERROR_STOP=1 "$@"
}

# b. migration ledger, owner-owned.
migrator_psql <<'SQL'
SET ROLE needs_owner;
CREATE TABLE IF NOT EXISTS needs.schema_migration (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
SQL

# c. apply each not-yet-recorded version, in filename order, one transaction per file.
applied=0
present=0
for file in contracts/ddl/needs/*.sql; do
    [ -e "$file" ] || continue
    version=$(basename "$file" .sql)
    # -c disables psql's :'var' interpolation; pipe via stdin like everything else here.
    recorded=$(printf "SET ROLE needs_owner;\nselect 1 from needs.schema_migration where version = :'version';\n" \
        | migrator_psql -v version="$version" -A -t)
    if [ "$recorded" = "1" ]; then
        present=$((present + 1))
        continue
    fi
    {
        # needs_migrator has no lock_timeout of its own (db/bootstrap.sql only sets it on
        # needs_runtime), so a DDL migration would wait forever behind a long reader; 5s matches
        # needs_runtime's lock_timeout and just fails+rolls back the transaction for a plain retry.
        printf 'BEGIN;\nSET ROLE needs_owner;\nSET lock_timeout = '"'"'5s'"'"';\n'
        cat "$file"
        printf "\nINSERT INTO needs.schema_migration(version) VALUES (:'version');\nCOMMIT;\n"
    } | migrator_psql -v version="$version" \
        || { echo "needs: migration failed: $file" >&2; exit 1; }
    applied=$((applied + 1))
done

# d. postgrest_anon direct SELECT whitelist (stage 1 has no needs_reader role).
docker exec -i "$container" psql -U "$superuser" -d "$db" -X -q -v ON_ERROR_STOP=1 \
    < db/grants/postgrest_anon_needs.sql

# e. analysis reader: SELECT on the source schemas. Superuser, not migrator -- needs_migrator owns
# neither trend_radar nor tubedepth, and the file no-ops where those schemas are absent.
docker exec -i "$container" psql -U "$superuser" -d "$db" -X -q -v ON_ERROR_STOP=1 \
    < db/grants/needs_runtime_reader.sql

# f. operational views, owner-owned. Each file drops and recreates its own view, so re-applying a
# deploy is a no-op and a view whose columns changed still deploys (CREATE OR REPLACE would not).
for file in db/views/*.sql; do
    [ -e "$file" ] || continue
    { printf 'BEGIN;\nSET ROLE needs_owner;\n'; cat "$file"; printf '\nCOMMIT;\n'; } | migrator_psql \
        || { echo "needs: view failed: $file" >&2; exit 1; }
done

echo "needs: $applied migration(s) applied, $present already present"
