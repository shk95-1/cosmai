"""A comment row keeps the author's channel identifier as a hash and nothing else (#91 decision 3, #92).

Three questions, in the order they can fail. Is the hash the one the archive already carries -- because a
formula that drifts by one character matches no operator comment and says so nowhere. Does the loader refuse
a document that arrived with the raw identifier still on it. And do the stored rows answer the same question
back, in both stores, through `needs.author_identifier_violation`.

The equality with the archive is measured against `tests/fixtures/trend_sample/corpus/`, the sample the
goldens use: its `author_channel_hash` values were written by ydc's collector before the 2026-08-19
handover, so recomputing them here compares our function against the archive rather than against itself.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine

from analysis import evidence, sensitivity
from db import corpus, seed
from db.corpus import contract
from db.corpus.author import AUTHOR_HASH, HASH_LENGTH, HASH_PREFIX, RAW_CHANNEL_ID, author_hash
from db.seed._common import connect

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "yt_handoff"
ARCHIVE_SAMPLE = Path(__file__).resolve().parent / "fixtures" / "trend_sample" / "corpus" / "document.csv"
VIEW = ROOT / "db" / "views" / "author_identifier_violation.sql"

# A channel id out of the archive sample, so the pinned digest below is a value that exists.
CHANNEL = "UCabc123"
DIGEST = "224427a5eb83274bdf825b8a"

# The 40 comments of the archive sample whose author is the parent video's own channel. It is a measured
# number, not a target: with a different formula it is 0, which is exactly the silent failure this pins.
CREATOR_COMMENTS = 40


# ---------- the function ----------
def test_the_hash_is_the_rule_the_collector_used():
    assert author_hash(CHANNEL) == DIGEST
    assert author_hash(CHANNEL) == hashlib.sha256(f"youtube:{CHANNEL}".encode()).hexdigest()[:24]
    assert len(author_hash(CHANNEL)) == HASH_LENGTH
    assert HASH_PREFIX == "youtube:"


def test_the_hash_is_named_once_and_every_caller_reaches_that_one():
    """Two implementations is the failure #92 is about -- a drift between them is invisible in the output."""
    assert evidence.author_hash is author_hash
    assert sensitivity.creator_hash(CHANNEL) == author_hash(CHANNEL)
    assert (sensitivity.CREATOR_HASH_PREFIX, sensitivity.CREATOR_HASH_LENGTH) == (HASH_PREFIX, HASH_LENGTH)


def _archive_comments() -> list[dict[str, Any]]:
    with ARCHIVE_SAMPLE.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["source"] == "youtube_comment"]
    return [dict(row, metadata=json.loads(row["source_metadata"] or "{}")) for row in rows]


def test_the_hash_reproduces_the_value_the_archive_already_stores():
    """The proof the verification sample exists for, run on the one archive sample that is in the repo."""
    comments = _archive_comments()
    assert comments, "the golden sample carries no comment rows"
    matched = [c for c in comments if c["metadata"]["author_channel_hash"] == author_hash(c["channel_id"])]
    assert len(matched) == CREATOR_COMMENTS
    # The formula the issue body wrote. It matches nothing, which is the whole reason the form is pinned.
    bare = [
        c
        for c in comments
        if c["metadata"]["author_channel_hash"]
        == hashlib.sha256(c["channel_id"].encode()).hexdigest()[:HASH_LENGTH]
    ]
    assert bare == []


def test_the_stored_hashes_are_hashes_rather_than_channel_ids():
    for comment in _archive_comments():
        stored = comment["metadata"]["author_channel_hash"]
        assert re.fullmatch(r"[0-9a-f]{24}", stored)
        assert not RAW_CHANNEL_ID.match(stored)


# ---------- the loader ----------
def _fixture_comment() -> dict[str, str]:
    rows = list(corpus.read_csv(FIXTURE / "document.csv"))
    return next(row for row in rows if row["source"] == "youtube_comment")


def _with_metadata(row: dict[str, str], **extra: Any) -> dict[str, str]:
    metadata = json.loads(row["source_metadata"]) | extra
    return dict(row, source_metadata=json.dumps(metadata, ensure_ascii=False))


def _runs() -> dict[str, str]:
    return corpus.runs_by_collected_at(json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8")))


def test_the_loader_takes_the_fixture_document_as_it_stands():
    assert corpus.document_row(_fixture_comment(), 1, _runs())


@pytest.mark.parametrize(
    "extra",
    [
        {"author": "Some Person"},
        {"author_id": "UCqrNqg3UgVoD3Sa-F_TxuSA"},
        {"author_channel_hash": "UCqrNqg3UgVoD3Sa-F_TxuSA"},
        # The right kind of value, the wrong shape: untruncated, upper-case, and truncated too far.
        {"author_channel_hash": hashlib.sha256(b"youtube:UCabc123").hexdigest()},
        {"author_channel_hash": DIGEST.upper()},
        {"author_channel_hash": DIGEST[:6]},
    ],
)
def test_the_loader_refuses_a_document_carrying_the_raw_identifier(extra: dict[str, str]):
    """Refused before the insert: once a raw identifier is in the table it is on disk and in the backups."""
    row = _with_metadata(_fixture_comment(), **extra)
    with pytest.raises(contract.AuthorIdentifierRefused) as raised:
        corpus.document_row(row, 1, _runs())
    assert row["doc_id"] in str(raised.value)
    # The refusal names the column, never the value it found.
    assert "Some Person" not in str(raised.value)


# ---------- the view ----------
def test_the_view_and_the_loader_recognise_one_shape_of_raw_identifier():
    """The view carries no pattern of its own: every regex in it is one of the two `db/corpus/author.py`
    exports, spelled the same way, so the loader cannot accept a value the view goes on to list.

    The counts are the pin. `RAW_CHANNEL_ID` twice -- the `corpus_document` metadata arm and the
    `author_channel_hash` arm; `AUTHOR_HASH` twice -- the same `author_channel_hash` arm and the
    `tubedepth.comments.author_id` arm, which asks "is this not a hash" rather than naming one raw
    shape, so that a handle or a legacy id does not walk past a `UC`-shaped predicate.
    """
    body = VIEW.read_text(encoding="utf-8")
    assert body.count(RAW_CHANNEL_ID.pattern) == 2
    assert body.count(AUTHOR_HASH.pattern) == 2
    assert "GRANT SELECT ON needs.author_identifier_violation TO needs_runtime" in body
    assert "first_seen_at >" not in body


@pytest.fixture
def loaded(
    needs_schema: str, needs_runtime_url: str, tubedepth_side_schema: str, _schema_name: str
) -> tuple[str, str]:
    """The view is what a deploy lays on (`db/migrate.sh`), so a per-test schema lays it on here.

    The two grants mirror `db/grants/needs_runtime_reader.sql`: in production the view runs with
    needs_owner's privilege and reads `tubedepth.comments` through exactly these.
    """
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{tubedepth_side_schema}" TO needs_owner')
            conn.exec_driver_sql(
                f'GRANT SELECT ON ALL TABLES IN SCHEMA "{tubedepth_side_schema}" TO needs_owner'
            )
            conn.exec_driver_sql("SET ROLE needs_owner")
            conn.exec_driver_sql(
                VIEW.read_text(encoding="utf-8")
                .replace("needs.", f'"{_schema_name}".')
                .replace("tubedepth.", f'"{tubedepth_side_schema}".')
            )
    finally:
        engine.dispose()
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn:
        corpus.load(conn, FIXTURE)
    return needs_runtime_url, tubedepth_side_schema


def _violations(url: str) -> list[tuple[Any, ...]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute("SELECT violation, row_key FROM author_identifier_violation ORDER BY violation, row_key")
        return cur.fetchall()


DOCUMENT = (
    "INSERT INTO corpus_document (snapshot_id, source, source_item_id, content_type, parent_item_id,"
    " channel_id, published_at, text, source_metadata, collected_at, source_run)"
    " VALUES (1, 'youtube_comment', %s, 'comment', 'VID_LONG_A1', 'UCqrNqg3UgVoD3Sa-F_TxuSA',"
    " '2025-04-02T00:00:00Z', 'raw', %s::jsonb, '2026-08-19T00:00:00Z', %s)"
)
COMMENT = (
    'INSERT INTO "{schema}".comments (video_id, comment_id, text, author, author_id,'
    " is_hearted_by_uploader, is_pinned, first_seen_at, last_seen_at)"
    " VALUES (%s, %s, 'raw', %s, %s, false, false, %s, %s)"
)


@pytest.mark.postgres
def test_the_view_is_empty_on_the_rows_a_load_puts_there(loaded: tuple[str, str]):
    url, _ = loaded
    assert _violations(url) == []


# The allow-list branch (fork #95) fires alongside the two name branches, because `author` and
# `author_id` are not comment metadata keys either -- so the expectation is the whole list a row
# raises, not one name. `author_name` is the case that only the allow-list can see: it is the shape
# that walked past both the loader and this view while branch (1) named two keys.
@pytest.mark.postgres
@pytest.mark.parametrize(
    ("item_id", "metadata", "violations"),
    [
        (
            "RAW_NAME",
            '{"collected_at": "%s", "author": "Some Person"}',
            ["corpus_author_key", "corpus_comment_metadata_key"],
        ),
        (
            "RAW_ID",
            '{"collected_at": "%s", "author_id": "UCqrNqg3UgVoD3Sa-F_TxuSA"}',
            ["corpus_author_key", "corpus_comment_metadata_key"],
        ),
        (
            "OTHER_NAME",
            '{"collected_at": "%s", "author_name": "Some Person"}',
            ["corpus_comment_metadata_key"],
        ),
        (
            "NESTED",
            '{"collected_at": "%s", "snippet": {"authorDisplayName": "Some Person"}}',
            ["corpus_comment_metadata_key"],
        ),
        (
            "RAW_HASH",
            '{"collected_at": "%s", "author_channel_hash": "UCqrNqg3UgVoD3Sa-F_TxuSA"}',
            ["corpus_raw_channel_hash"],
        ),
        (
            "LONG_HASH",
            '{"collected_at": "%s", "author_channel_hash":'
            ' "9b0a0a4e0e0d4c1f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f"}',
            ["corpus_hash_shape"],
        ),
    ],
)
def test_the_view_lists_a_corpus_row_that_went_round_the_loader(
    loaded: tuple[str, str], item_id: str, metadata: str, violations: list[str]
):
    """A direct INSERT is the bypass the Python refusal cannot see -- the view is what stands under it."""
    url, _ = loaded
    run = _runs()
    collected_at = next(iter(run))
    with connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(DOCUMENT, (item_id, metadata % collected_at, run[collected_at]))
        conn.commit()
    assert _violations(url) == [(name, f"youtube_comment:{item_id}") for name in violations]


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("comment_id", "author", "author_id", "first_seen", "expected"),
    [
        # The one-off upstream pass repaired the pre-rule population, so the invariant now watches
        # history too: a raw historical row is a regression, while its hashed counterpart is clean.
        (
            "OLD_RAW",
            "Some Person",
            "UCqrNqg3UgVoD3Sa-F_TxuSA",
            "2026-08-23T22:55:44Z",
            ["comment_author_name", "comment_raw_author_id"],
        ),
        ("OLD_HASHED", None, "224427a5eb83274bdf825b8a", "2026-08-23T22:55:44Z", []),
        ("NAME", "Some Person", None, "2026-09-01T00:00:00Z", ["comment_author_name"]),
        ("ID", None, "UCqrNqg3UgVoD3Sa-F_TxuSA", "2026-09-01T00:00:00Z", ["comment_raw_author_id"]),
        ("HASHED", None, "224427a5eb83274bdf825b8a", "2026-09-01T00:00:00Z", []),
        # Raw without being `UC`-shaped: a handle is an identifier too, and a predicate that names one
        # shape would wave it through.
        ("HANDLE", None, "@some-channel", "2026-09-01T00:00:00Z", ["comment_raw_author_id"]),
    ],
)
def test_the_view_watches_the_collectors_whole_history(
    loaded: tuple[str, str],
    comment_id: str,
    author: str | None,
    author_id: str | None,
    first_seen: str,
    expected: list[str],
):
    url, td_schema = loaded
    # Written as the role that owns that schema: needs_runtime never writes the collector's tables, and
    # in production it cannot read them either -- it only reads this view.
    engine = create_engine(url.replace("needs_runtime:check-runtime", "needs_migrator:check"))
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                COMMENT.format(schema=td_schema),
                ("VID_LONG_A1", comment_id, author, author_id, first_seen, first_seen),
            )
    finally:
        engine.dispose()
    assert [row[0] for row in _violations(url)] == expected


# ---------- the verification sample (DDL 029) ----------
# What `contracts/ddl/needs/029_author_verify.sql` adds, and only that. The schema itself is
# upstream's (shk95-1/cosmai#258): `tests/test_needs_verify_closure.py` already proves that
# db/bootstrap_needs_verify.sql runs above the DDL loop, that a table the loop creates in
# needs_verify is reachable by needs_verify_reader and by no role that reads `needs`, and that the
# schema is not open to PUBLIC -- all of it against a probe table of its own. These ask the
# questions that are 029's: the real table's shape, the roles `contracts/anon_exposure.md` names,
# the proof query the sample exists for, and that a second deploy leaves the rows where they are.

DDL_029 = ROOT / "contracts" / "ddl" / "needs" / "029_author_verify.sql"
SAMPLE = "needs_verify.author_sample"
# The proof fork issue #92 names, and the reason the sample is kept at all: the SQL side of
# db/corpus/author.py, recomputed in the database over the raw identifier the row still carries.
PROOF = "substr(encode(sha256(('youtube:' || channel_id)::bytea), 'hex'), 1, 24) = author_hash"
# Every role of db/bootstrap.sql that reads `needs`, plus the screen's anonymous one and the role
# the deploy itself connects as. None of them may reach the sample -- that refusal is what the
# schema is worth (`contracts/anon_exposure.md`).
CLOSED_TO = ("needs_runtime", "postgrest_anon", "needs_migrator")
# A row that is what the sample is for, and a row that is the formula #92's body first wrote down.
# The wrong one is not a typo: `sha256(channel_id)` without the prefix matched 0 of 200,000 archive
# comments, so it is exactly the mistake the proof query has to answer 'no' to.
WRONG_DIGEST = hashlib.sha256(CHANNEL.encode()).hexdigest()[:24]
PROBE_DATABASE = "author_verify_probe"


def _verify_psql(container: str, sql: str, *, role: str | None = None, database: str = "fleet"):
    """One statement as the harness superuser, optionally under SET ROLE.

    SET ROLE and not a connection, for the reason `tests/test_needs_verify_closure.py` gives:
    needs_verify_reader is NOLOGIN by design, and `has_table_privilege()` alone would measure the
    catalogue rather than what the server does when the role actually selects."""
    prelude = f"SET ROLE {role}; " if role else ""
    return subprocess.run(
        ["docker", "exec", container, "psql", "-U", "fleet", "-d", database]
        + ["-X", "-Atq", "-v", "ON_ERROR_STOP=1", "-c", prelude + sql],
        capture_output=True,
        text=True,
        check=False,
    )


def _verify_value(container: str, sql: str, *, database: str = "fleet") -> str:
    done = _verify_psql(container, sql, database=database)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.mark.postgres
def test_the_sample_is_the_three_columns_029_declares_and_the_schema_holds_nothing_else(
    harness_container: str,
):
    """The shape, read off the database a deploy built rather than off the file that asked for it."""
    columns = _verify_value(
        harness_container,
        "SELECT a.attname || ' ' || format_type(a.atttypid, a.atttypmod)"
        " || ' notnull=' || a.attnotnull"
        " || ' default=' || coalesce(pg_get_expr(d.adbin, d.adrelid), '-')"
        " FROM pg_attribute a"
        " JOIN pg_class c ON c.oid = a.attrelid"
        " JOIN pg_namespace n ON n.oid = c.relnamespace"
        " LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum"
        " WHERE n.nspname = 'needs_verify' AND c.relname = 'author_sample'"
        " AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
    ).splitlines()
    assert columns == [
        "author_hash text notnull=true default=-",
        "channel_id text notnull=true default=-",
        "captured_at timestamp with time zone notnull=true default=now()",
    ]
    assert (
        _verify_value(
            harness_container,
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            f" WHERE conrelid = '{SAMPLE}'::regclass AND contype = 'p'",
        )
        == "PRIMARY KEY (author_hash)"
    )
    # One table and nothing beside it. The schema is destroyed whole (`DROP SCHEMA ... CASCADE`), so
    # a second object arriving here would be destroyed with it by a step that never mentioned it.
    assert (
        _verify_value(
            harness_container,
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'needs_verify' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')",
        )
        == "1"
    )


@pytest.mark.postgres
def test_needs_verify_reader_reads_the_sample_and_the_roles_that_read_needs_cannot(harness_container: str):
    """The privilege matrix `contracts/anon_exposure.md` states, measured on the real table.

    Both halves of "visible" are asked of every closed role, because either one alone would open
    it: schema USAGE and the table privilege. `has_table_privilege` answers `t` regardless of the
    schema, which is exactly the hole that section of the exposure contract was written about."""
    read = _verify_psql(harness_container, f"SELECT count(*) FROM {SAMPLE}", role="needs_verify_reader")
    assert read.returncode == 0, f"the one role that must read the sample cannot: {read.stderr}"
    for role in CLOSED_TO:
        denied = _verify_psql(harness_container, f"SELECT count(*) FROM {SAMPLE}", role=role)
        assert denied.returncode != 0, f"{role} can read the re-identification sample"
        assert "permission denied for schema needs_verify" in denied.stderr, denied.stderr
        usage = _verify_value(
            harness_container, f"SELECT has_schema_privilege('{role}', 'needs_verify', 'USAGE')"
        )
        select = _verify_value(
            harness_container, f"SELECT has_table_privilege('{role}', '{SAMPLE}', 'SELECT')"
        )
        assert (usage, select) == ("f", "f"), f"{role}: usage={usage} select={select}"
    # PUBLIC is every role on the server, including the ones added after this deploy ran.
    assert (
        _verify_value(harness_container, "SELECT has_schema_privilege('public', 'needs_verify', 'USAGE')")
        == "f"
    )
    assert (
        _verify_value(
            harness_container,
            f"SELECT count(*) FROM pg_class c, unnest(c.relacl) acl"
            f" WHERE c.oid = '{SAMPLE}'::regclass AND acl::text LIKE '=%'",
        )
        == "0"
    ), "the sample is granted to PUBLIC"
    # needs_migrator is refused on its own privileges above and reaches the sample anyway: it is a
    # NOINHERIT member of needs_owner, which owns the table -- the DDL loop's own SET ROLE. Asked of
    # the catalogue because this helper's session is a superuser, for whom any SET ROLE succeeds.
    # Pinned so contracts/anon_exposure.md's "not against the deploy" and the server cannot drift apart.
    assert (
        _verify_value(harness_container, "SELECT pg_has_role('needs_migrator', 'needs_owner', 'SET')") == "t"
    )
    assert (
        _verify_value(
            harness_container, f"SELECT relowner::regrole FROM pg_class WHERE oid = '{SAMPLE}'::regclass"
        )
        == "needs_owner"
    )
    # `needs_runtime_reader` is a grants file (db/grants/needs_runtime_reader.sql) and not a role
    # here, so it is absent from the loop above rather than passing it silently. The day it becomes
    # a role, this says so and the list is short by one.
    assert (
        _verify_value(
            harness_container, "SELECT count(*) FROM pg_roles WHERE rolname = 'needs_runtime_reader'"
        )
        == "0"
    )


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("digest", "expected"),
    [(DIGEST, "1/1"), (WRONG_DIGEST, "0/1")],
    ids=["the-hash-the-archive-carries", "the-bare-sha256-the-body-first-wrote"],
)
def test_the_proof_query_passes_on_a_correct_row_and_fails_on_a_wrong_one(
    harness_container: str, digest: str, expected: str
):
    """Planted and rolled back in one statement: the sample is a production table with a retention
    limit on it, and a test that leaves a channel id behind is the one thing it must not do."""
    holds = _verify_value(
        harness_container,
        "BEGIN; SET ROLE needs_owner;"
        f" INSERT INTO {SAMPLE} (author_hash, channel_id) VALUES ('{digest}', '{CHANNEL}');"
        f" SELECT count(*) FILTER (WHERE {PROOF}) || '/' || count(*)"
        f" FROM {SAMPLE} WHERE author_hash = '{digest}';"
        " ROLLBACK;",
    )
    assert holds == expected
    assert _verify_value(harness_container, f"SELECT count(*) FROM {SAMPLE}") == "0", "a planted row survived"


@pytest.fixture
def probe_database(harness_container: str) -> Iterator[str]:
    """A database of its own for the deploy below, dropped afterwards: the questions are about what
    a deploy does to a table that is already there, and `fleet` is the database every other test in
    this run is holding."""
    _verify_psql(harness_container, f"DROP DATABASE IF EXISTS {PROBE_DATABASE} WITH (FORCE)")
    assert _verify_psql(harness_container, f"CREATE DATABASE {PROBE_DATABASE}").returncode == 0
    try:
        yield PROBE_DATABASE
    finally:
        _verify_psql(harness_container, f"DROP DATABASE IF EXISTS {PROBE_DATABASE} WITH (FORCE)")


@pytest.mark.postgres
@pytest.mark.serial
def test_a_second_deploy_leaves_the_sample_and_its_rows_where_they_are(
    harness_container: str, probe_database: str, deploy: Callable[..., subprocess.CompletedProcess[str]]
):
    """Re-running db/migrate.sh is a no-op for 029, twice over.

    Once through the ledger, which is how every file in this directory is applied exactly once. And
    once through the file itself, with its ledger row taken away so the loop has to apply it again
    -- the case `tests/conftest.py`'s `needs_schema` fixture is in on every single test, because it
    re-applies every contracts/ddl/needs/*.sql into a per-test schema and the substitution that
    renames `needs.` does not touch a table in needs_verify."""
    first = deploy(probe_database)
    assert first.returncode == 0, first.stderr
    planted = _verify_psql(
        harness_container,
        f"SET ROLE needs_owner;"
        f" INSERT INTO {SAMPLE} (author_hash, channel_id) VALUES ('{DIGEST}', '{CHANNEL}')",
        database=probe_database,
    )
    assert planted.returncode == 0, planted.stderr

    second = deploy(probe_database)
    assert second.returncode == 0, second.stderr
    assert "needs: 0 migration(s) applied" in second.stdout, second.stdout

    # The ledger row removed, so the loop applies the file a second time for real.
    _verify_psql(
        harness_container,
        "SET ROLE needs_owner; DELETE FROM needs.schema_migration WHERE version = '029_author_verify'",
        database=probe_database,
    )
    third = deploy(probe_database)
    assert third.returncode == 0, f"029 cannot be applied twice: {third.stderr}"

    assert (
        _verify_value(harness_container, f"SELECT count(*) FROM {SAMPLE}", database=probe_database) == "1"
    ), "a deploy emptied the verification sample"
    assert (
        _verify_value(
            harness_container,
            "SELECT count(*) FROM needs.schema_migration WHERE version = '029_author_verify'",
            database=probe_database,
        )
        == "1"
    )
    still_reads = _verify_psql(
        harness_container,
        f"SELECT count(*) FROM {SAMPLE}",
        role="needs_verify_reader",
        database=probe_database,
    )
    assert still_reads.returncode == 0, still_reads.stderr
    assert still_reads.stdout.strip() == "1"


def test_029_grants_nothing_and_says_where_the_rows_go():
    """The file's two silences, asked of the file because no database can show an absence of intent.

    A GRANT here would be a second path to the same privilege as db/bootstrap_needs_verify.sql's
    ALTER DEFAULT PRIVILEGES, and a GRANT to one of the closed roles would undo the boundary with
    nothing failing. The retention sentence is the other half of fork #91 decision 3: the sample is
    allowed to exist because it is destroyed."""
    body = DDL_029.read_text(encoding="utf-8")
    statements = re.sub(r"--[^\n]*", "", body)
    assert "GRANT" not in statements.upper(), "029 grants a privilege of its own"
    # needs_verify_reader is left out: 029 names it in a COMMENT, which is where the next person
    # to find this schema reads who may open it. What must not appear is a role that is closed out.
    for role in (*CLOSED_TO, "needs_runtime_reader"):
        assert role not in statements, f"{role} is named in a statement of 029"
    assert "DROP SCHEMA needs_verify CASCADE; DROP ROLE needs_verify_reader;" in body
    assert "30 days" in body
