#!/bin/sh
# Applies db/bootstrap_source.sql + the two collector dumps, then db/bootstrap.sql,
# db/bootstrap_needs_verify.sql and contracts/ddl/needs/*.sql, to $container/$db -- the one path
# production, the test harness and tool/checks/ddl-drift all use to create the four schemas.
set -eu

container=cosmai-postgres
container_given=0
db=app
superuser=platform

usage() {
    cat <<'EOF'
usage: db/migrate.sh [--container NAME] [--db NAME] [--superuser NAME]

Stands up trend_radar and tubedepth when they are absent and brings each one's
contracts/ddl/<schema>/NNN_*.sql up to date when it is already there, then applies db/bootstrap.sql,
db/bootstrap_needs_verify.sql, contracts/ddl/needs/*.sql, the two named grants files
(db/grants/postgrest_anon_needs.sql, db/grants/needs_runtime_reader.sql) and db/views/*.sql to
$container/$db through `docker exec`.
Every path is repo-relative: run it from the repo root (the image's WORKDIR is that root --
stack/Dockerfile).

Reads NEEDS_DB_MIGRATOR and NEEDS_DB_RUNTIME from $COSMAI_SECRET_FILE (default ~/.config/cosmai/env).
TREND_RADAR_DB_RUNTIME, TREND_RADAR_DB_READER and TUBEDEPTH_DB_RUNTIME are read from the same file
by step (0) alone, so a database that already has both schemas -- production -- never asks for them.

  --container NAME   postgres container to `docker exec` into (default: cosmai-postgres)
  --db NAME          database to apply to (default: app)
  --superuser NAME   role that owns the bootstrap step (default: platform)
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --container) container=$2; container_given=1; shift 2 ;;
        --db) db=$2; shift 2 ;;
        --superuser) superuser=$2; shift 2 ;;
        *) echo "needs: unknown argument: $1" >&2; exit 1 ;;
    esac
done

# #233 (#228 D5'): no --container means the production default, cosmai-postgres -- the harness
# and tool/checks/ddl-drift always name a throwaway one, and a throwaway tree has nothing to
# certify, so only the production path ever asks the gate.
[ "$container_given" = 1 ] || tool/checks/deploy-gate

# What every message below is about. Step (0) sets it to the schema it is working on, so a missing
# TREND_RADAR_DB_RUNTIME reports itself as a trend_radar problem rather than as a `needs` one.
prefix=needs

secret_file=${COSMAI_SECRET_FILE:-$HOME/.config/cosmai/env}
[ -f "$secret_file" ] || {
    echo "needs: missing secret file $secret_file (need NEEDS_DB_MIGRATOR, NEEDS_DB_RUNTIME)" >&2
    exit 1
}

read_secret() {
    # Only the key name ever reaches a message; the value is never echoed or logged.
    #
    # The parsing rule is db/secrets.py's load(), line for line, because the two readers must not
    # disagree about the same file (#20, #42 M3): blank, comment and "="-less lines are skipped, the
    # key is what stands before the first "=" with whitespace stripped, the last match wins, and the
    # value is stripped of surrounding whitespace and then of surrounding quotes.
    # tests/test_secret_file_rule.py holds the two implementations against one table of lines.
    value=$(awk -v want="$1" '
        {
            line = $0
            gsub(/^[ \t\r\v\f]+|[ \t\r\v\f]+$/, "", line)
            if (line == "" || line ~ /^#/ || index(line, "=") == 0) next
            key = substr(line, 1, index(line, "=") - 1)
            value = substr(line, index(line, "=") + 1)
            gsub(/^[ \t\r\v\f]+|[ \t\r\v\f]+$/, "", key)
            if (key != want) next
            gsub(/^[ \t\r\v\f]+|[ \t\r\v\f]+$/, "", value)
            sub(/^[\047"]+/, "", value)
            sub(/[\047"]+$/, "", value)
            found = value
            seen = 1
        }
        END { if (seen) print found }
    ' "$secret_file")
    [ -n "$value" ] || { echo "$prefix: missing key in $secret_file: $1" >&2; exit 1; }
    printf '%s' "$value"
}

psql_set() { # $1 = psql variable name, $2 = its value -- emitted as one \set line for psql's stdin
    # A password given as `psql -v name=value` stands in the host's `ps` for the length of the call
    # (#20). psql's own \set does the same job from the stream the SQL is already travelling on.
    # Inside single quotes psql's lexer honours backslash escapes, so the two metacharacters are
    # escaped here -- backslash first, or the escape of the quote would be escaped again.
    psql_set_value=$(printf '%s' "$2" | sed -e 's/\\/\\\\/g' -e "s/'/\\\\'/g")
    printf "\\\\set %s '%s'\n" "$1" "$psql_set_value"
}

psql_as() { # $1 = role, $2 = its password, rest = psql arguments; the SQL arrives on stdin
    # Same reason as psql_set, one layer out: `docker exec -e PGPASSWORD=...` is argv too, so the
    # value travels as stdin's first line and the shell inside the container moves it into the
    # environment there. Every caller pipes SQL in, so the `cat` below always has a stream.
    psql_as_role=$1
    psql_as_password=$2
    shift 2
    { printf '%s\n' "$psql_as_password"; cat; } | docker exec -i "$container" sh -c '
        IFS= read -r password || exit 1
        PGPASSWORD=$password
        export PGPASSWORD
        role=$1
        database=$2
        shift 2
        exec psql -U "$role" -d "$database" -X -q -v ON_ERROR_STOP=1 "$@"
    ' sh "$psql_as_role" "$db" "$@"
}

superuser_psql() { # psql as the database owner, no password; the SQL arrives on stdin
    docker exec -i "$container" psql -U "$superuser" -d "$db" -X -q -v ON_ERROR_STOP=1 "$@"
}

# 0. trend_radar (collectors/commerce) and tubedepth (collectors/youtube). The archived old repos'
# init scripts made these until #178; on an empty database this step is what makes them.
#
# Absence is the whole question, and pg_namespace answers it per schema. Production has both, so
# production always takes the skip -- this step cannot touch a live schema, and it reads none of
# the three secrets on that path either. The order on the other path is roles before objects:
# db/bootstrap_source.sql sets the DEFAULT PRIVILEGES the tables must be born under, then the
# pg_dump baseline contracts/ddl/current/app.<schema>.sql, then every
# contracts/ddl/<schema>/NNN_*.sql -- the same composition tests/conftest.py builds a throwaway
# schema from and tool/checks/ddl-drift calls production's expected state.
#
# alembic_version comes with the baseline and stays as the dump left it: the table exists and holds
# no row, because the dump is --schema-only and the old repos' alembic never runs again. Nothing
# here writes a version into it; from now on this schema changes by a numbered file alone.
#
# The question is the baseline table, not the namespace. CREATE SCHEMA is in the roles step, which
# autocommits, while only the objects travel inside one transaction -- so a build whose objects
# rolled back leaves an empty schema behind, and a namespace probe would call that "present" and
# skip it for every run after, with a hand-approved DROP SCHEMA on production as the only way back
# (#178 review 1). One word comes back:
#
#   built    the baseline's alembic_version is there -- production, and every re-run after the first
#   empty    a schema with nothing in it
#   partial  objects but no baseline table: a build that died between the two
#   absent   no schema at all
schema_state() { # $1 = schema name (a literal from the loop below, never input)
    superuser_psql -Atq -c "SELECT CASE
            WHEN to_regclass('$1.alembic_version') IS NOT NULL THEN 'built'
            WHEN NOT EXISTS (SELECT FROM pg_namespace WHERE nspname = '$1') THEN 'absent'
            WHEN EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                          WHERE n.nspname = '$1') THEN 'partial'
            ELSE 'empty'
        END" < /dev/null
}

# 0b. The ledger these two schemas did not have until #223, and why they need one.
#
# Step (0) above composes a source schema out of the baseline plus every additive file, but only on
# the `absent`/`empty` path. Production has had both schemas since before #178, so production always
# takes the `built` skip -- and a file added to contracts/ddl/<schema>/ after that schema was built
# was therefore applied by the test harness, expected by tool/checks/ddl-drift, and reached
# production by no path at all. Measured 2026-09-12: tubedepth/004 and /005 had been on main for a
# week, every deploy printed `tubedepth: present, left alone`, and neither column existed.
#
# The shape is needs.schema_migration's (step (b) below), one per source schema. A flag on this
# script was the other option offered and was refused: a step that runs only when somebody remembers
# to pass it is the same failure one layer up.
source_ledger_ddl() { # $1 = schema -- run under SET ROLE <schema>_owner, like everything step (0) writes
    printf 'CREATE TABLE IF NOT EXISTS %s.schema_migration (\n' "$1"
    printf '    version text PRIMARY KEY,\n'
    printf '    applied_at timestamptz NOT NULL DEFAULT now()\n'
    printf ');\n'
}

# What could not be computed: which versions a database that predates the ledger already carries.
# A ledger created on such a database starts empty, so every file would look unapplied and the
# ALTER TABLEs that already ran would run again and fail. contracts/ddl/<schema>/
# applied_before_the_ledger.txt is that one-time record, read only where the schema is present AND
# has no ledger yet -- which happens once per database, ever. A schema built from absent never
# reads it: step (0) applies every file and records every file.
source_ledger_adopted() { # $1 = the adoption file; prints one version per line, nothing if absent
    [ -f "$1" ] || return 0
    sed 's/#.*//' "$1" | awk 'NF { print $1 }'
}

for schema in trend_radar tubedepth; do
    prefix=$schema
    # The exit status decides, never the stdout alone: a psql that cannot connect prints nothing,
    # and "nothing" read as absent would run the unguarded half of db/bootstrap_source.sql -- the
    # REVOKE, the GRANTs, the DEFAULT PRIVILEGES, the ALTER ROLEs -- against a live database
    # (#178 review 2). An answer that is not one of the four words is the same kind of unknown.
    state=$(schema_state "$schema") \
        || { echo "$prefix: could not ask $container/$db what state the schema is in" >&2; exit 1; }
    case "$state" in
        built)
            # Production's path. The schema itself is left exactly as it was -- nothing here rebuilds
            # it, and the one thing it may still be owed is an additive file it predates (#223).
            echo "$schema: present, left alone"
            had_ledger=$(superuser_psql -Atq \
                -c "SELECT to_regclass('$schema.schema_migration') IS NOT NULL" < /dev/null) \
                || { echo "$prefix: could not ask $container/$db whether the ledger is there" >&2; exit 1; }
            if [ "$had_ledger" != t ]; then
                adoption_file=contracts/ddl/"$schema"/applied_before_the_ledger.txt
                # Validated before any of it is written: these versions go into a quoted SQL literal,
                # and a line that is not a bare version has no business seeding a ledger.
                for version in $(source_ledger_adopted "$adoption_file"); do
                    case "$version" in
                        *[!A-Za-z0-9_]*)
                            echo "$prefix: $adoption_file names '$version', which is not a version" >&2
                            exit 1
                            ;;
                    esac
                done
                # The table and its seed go in one transaction. Split, a seed that failed would leave
                # an empty ledger behind, the next run would find the table and skip the seeding, and
                # every file the adoption list names would be re-applied -- an ALTER TABLE that
                # already ran, against production.
                { printf 'BEGIN;\nSET ROLE %s_owner;\n' "$schema"
                  source_ledger_ddl "$schema"
                  for version in $(source_ledger_adopted "$adoption_file"); do
                      printf "INSERT INTO %s.schema_migration(version) VALUES ('%s');\n" "$schema" "$version"
                  done
                  printf 'COMMIT;\n'
                } | superuser_psql \
                    || { echo "$prefix: could not record what this schema already carried" >&2; exit 1; }
            fi
            # Same loop as step (c) below, one transaction per file, so a file that fails takes only
            # itself and leaves no ledger row behind.
            applied=0
            present=0
            for file in contracts/ddl/"$schema"/*.sql; do
                [ -e "$file" ] || continue
                version=$(basename "$file" .sql)
                recorded=$(printf "SET ROLE %s_owner;\nselect 1 from %s.schema_migration where version = :'version';\n" \
                    "$schema" "$schema" | superuser_psql -v version="$version" -A -t)
                if [ "$recorded" = "1" ]; then
                    present=$((present + 1))
                    continue
                fi
                {
                    # 5s, the same as step (c): an ALTER TABLE behind a long reader would otherwise
                    # wait forever, and a plain retry is the right answer to losing that race.
                    printf 'BEGIN;\nSET ROLE %s_owner;\nSET lock_timeout = '"'"'5s'"'"';\n' "$schema"
                    cat "$file"
                    printf "\nINSERT INTO %s.schema_migration(version) VALUES (:'version');\nCOMMIT;\n" "$schema"
                } | superuser_psql -v version="$version" \
                    || { echo "$prefix: migration failed: $file" >&2; exit 1; }
                applied=$((applied + 1))
            done
            echo "$schema: $applied migration(s) applied, $present already present"
            continue
            ;;
        absent | empty) ;;
        partial)
            echo "$prefix: the schema holds objects but no alembic_version -- a build died part-way," >&2
            echo "$prefix: and nothing here can tell that from a schema someone else made. Drop or finish it by hand." >&2
            exit 1
            ;;
        *) echo "$prefix: the schema probe answered '$state', which is not a state" >&2; exit 1 ;;
    esac
    case "$schema" in
        # trend_radar's third role is trend_radar_reader: what trend-radar-dashboard logs in with and
        # the beneficiary of the schema's DEFAULT PRIVILEGES (contracts/anon_exposure.md). 8 is
        # trend_radar_runtime's measured CONNECTION LIMIT, the number
        # collectors/commerce/storage/db.py sizes its pool against.
        trend_radar)
            runtime_key=TREND_RADAR_DB_RUNTIME
            reader=trend_radar_reader
            reader_key=TREND_RADAR_DB_READER
            runtime_limit=8
            ;;
        tubedepth)
            runtime_key=TUBEDEPTH_DB_RUNTIME
            reader=
            reader_key=
            runtime_limit=
            ;;
    esac

    runtime_password=$(read_secret "$runtime_key")
    reader_password=
    [ -z "$reader_key" ] || reader_password=$(read_secret "$reader_key")

    { psql_set runtime_password "$runtime_password"
      psql_set reader_password "$reader_password"
      cat db/bootstrap_source.sql
    } | superuser_psql -v schema="$schema" -v database="$db" -v reader="$reader" \
        -v runtime_limit="$runtime_limit" \
        || { echo "$prefix: could not create the roles" >&2; exit 1; }

    # One transaction for the objects, and the schema goes with them when they fail: this branch is
    # only reached for a schema that was absent or empty when the run started, so dropping it takes
    # nothing that was not this run's own, and the next run reads `absent` again instead of finding
    # something half-built.
    #
    # The baseline is named for production's database (app.<schema>.sql) whatever --db says, and its
    # own CREATE SCHEMA goes: db/bootstrap_source.sql has already made the schema, owned by
    # <schema>_owner, which is the point of doing the roles first.
    added=0
    { printf 'BEGIN;\nSET ROLE %s_owner;\n' "$schema"
      grep -v '^\\restrict' "contracts/ddl/current/app.$schema.sql" \
          | grep -v '^\\unrestrict' | grep -v "^CREATE SCHEMA $schema;\$"
      for file in contracts/ddl/"$schema"/*.sql; do
          [ -e "$file" ] || continue
          printf '\n'
          cat "$file"
      done
      # The ledger travels with them, seeded from the same list: a schema built here is current by
      # construction, so step (0b) has nothing left to do the next time this runs, and no database
      # this script made ever needs the adoption file (#223).
      printf '\n'
      source_ledger_ddl "$schema"
      for file in contracts/ddl/"$schema"/*.sql; do
          [ -e "$file" ] || continue
          printf "INSERT INTO %s.schema_migration(version) VALUES ('%s');\n" \
              "$schema" "$(basename "$file" .sql)"
      done
      printf '\nCOMMIT;\n'
      # -o /dev/null: the dump opens with `SELECT pg_catalog.set_config('search_path', ...)` and its
      # one-row result is not something a deploy log should carry. Errors are on stderr and stay.
    } | superuser_psql -o /dev/null || {
        # RESTRICT, not CASCADE: the transaction rolled back, so the schema is empty and RESTRICT
        # succeeds. If it does not, something is in there that this run did not put there, and the
        # right answer is to refuse rather than to sweep it away.
        superuser_psql -o /dev/null -c "DROP SCHEMA IF EXISTS $schema RESTRICT" < /dev/null \
            || echo "$prefix: and the schema it had made could not be dropped either" >&2
        echo "$prefix: could not stand up the schema; it was dropped, so re-running starts over" >&2
        exit 1
    }
    for file in contracts/ddl/"$schema"/*.sql; do
        [ -e "$file" ] || continue
        added=$((added + 1))
    done
    echo "$schema: created from the baseline dump + $added additive file(s)"
done
prefix=needs

migrator_password=$(read_secret NEEDS_DB_MIGRATOR)
runtime_password=$(read_secret NEEDS_DB_RUNTIME)

# a. roles + schema + runtime grants (idempotent; passwords are not rewritten for existing roles).
{ psql_set migrator_password "$migrator_password"
  psql_set runtime_password "$runtime_password"
  cat db/bootstrap.sql
} | superuser_psql -v schema=needs -v database="$db"

# a2. needs_verify: the closed schema for the re-identification sample, and the one role that reads
# it (#258, for the fork's DDL 029). Not renumbered into (b)(c)(d): the later letters are quoted by
# name elsewhere (db/views/collector_health.sql, tests/test_lineage_reader_grants.py), and moving
# them would make those references point at something else without a word.
#
# Its position carries two independent requirements, not one:
#   - the schema and the role must exist before the DDL loop at (c), because that loop runs as
#     needs_migrator under SET ROLE needs_owner, and needs_owner has neither CREATE on the database
#     nor CREATEROLE -- a migration creating them fails with "permission denied"; and
#   - ALTER DEFAULT PRIVILEGES reaches only objects created *after* it runs, so the same file
#     applied down with the grants files at (d)/(e) would leave needs_verify_reader with no SELECT
#     on a table (c) had already created. Measured 2026-09-19: the deploy exits 0, every "cannot
#     read" assertion still passes, and the one role that must read gets "permission denied for
#     table". (Found by the fork's reviewer-deep round on fork #92;
#     tests/test_needs_verify_closure.py is where it stays found.)
superuser_psql < db/bootstrap_needs_verify.sql

migrator_psql() { psql_as needs_migrator "$migrator_password" "$@"; }

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
superuser_psql < db/grants/postgrest_anon_needs.sql

# e. analysis reader: SELECT on the source schemas. Superuser, not migrator -- needs_migrator owns
# neither trend_radar nor tubedepth, and the file no-ops where those schemas are absent.
superuser_psql < db/grants/needs_runtime_reader.sql

# f. operational views, owner-owned. Each file drops and recreates its own view, so re-applying a
# deploy is a no-op and a view whose columns changed still deploys (CREATE OR REPLACE would not).
#
# The schema is not read out of the file: a basename becomes needs.<name>, so db/views/ *is* the
# needs schema by construction. A view of needs_verify's (#258) cannot be dropped here by any
# filename -- the DROP would name needs.<name>, miss, and the second deploy would fail on "already
# exists" -- so it would need a step of its own the day one exists. None does today.
#
# The sweep first: a view that reads another view (needs.pipeline_health reads collector_health and
# analysis_health, #138) makes the per-file DROP fail on the *second* deploy -- "cannot drop view
# analysis_health because other objects depend on it". Ordering the files around that would encode
# the dependency graph in filenames, silently, and the next such view would break the deploy again.
# Dropping them up front makes the file order irrelevant: the loop below recreates all of them, so
# a partial state cannot outlive this step.
#
# The list comes from the *files*, never from pg_views (#150). The needs schema also holds views this
# checkout does not own -- the fork's metrics_topic_quarter_violation and topic_quarter_judgement_
# violation (fork DDL 022/024) -- and a schema-wide sweep deletes them for good, because the loop
# below only recreates what is in db/views/. Owning the drop means owning the recreate.
#
# CASCADE stays: our own three depend on each other. A *foreign* view that depends on one of ours
# would still be dropped silently by it -- there is no ledger that would let this script know, which
# is what #107 is open about.
{ printf 'SET ROLE needs_owner;\n'
  for file in db/views/*.sql; do
      [ -e "$file" ] || continue
      printf 'DROP VIEW IF EXISTS needs.%s CASCADE;\n' "$(basename "$file" .sql)"
  done
} | migrator_psql || { echo "needs: could not clear the operational views" >&2; exit 1; }
for file in db/views/*.sql; do
    [ -e "$file" ] || continue
    { printf 'BEGIN;\nSET ROLE needs_owner;\n'; cat "$file"; printf '\nCOMMIT;\n'; } | migrator_psql \
        || { echo "needs: view failed: $file" >&2; exit 1; }
done

echo "needs: $applied migration(s) applied, $present already present"
