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

# The cutoff in the view, and why it is that date. Live collection starts under upstream #183, and a cutoff
# later than the archive's last row would leave its first days unwatched.
CUTOFF = "2026-08-24"


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
    assert body.count(f"'{CUTOFF}'") == 2
    assert "A later cutoff would leave the first days of live collection unwatched." in body


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


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("item_id", "metadata", "violation"),
    [
        ("RAW_NAME", '{"collected_at": "%s", "author": "Some Person"}', "corpus_author_key"),
        ("RAW_ID", '{"collected_at": "%s", "author_id": "UCqrNqg3UgVoD3Sa-F_TxuSA"}', "corpus_author_key"),
        (
            "RAW_HASH",
            '{"collected_at": "%s", "author_channel_hash": "UCqrNqg3UgVoD3Sa-F_TxuSA"}',
            "corpus_raw_channel_hash",
        ),
        (
            "LONG_HASH",
            '{"collected_at": "%s", "author_channel_hash":'
            ' "9b0a0a4e0e0d4c1f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c3d2e1f0a9b8c7d6e5f"}',
            "corpus_hash_shape",
        ),
    ],
)
def test_the_view_lists_a_corpus_row_that_went_round_the_loader(
    loaded: tuple[str, str], item_id: str, metadata: str, violation: str
):
    """A direct INSERT is the bypass the Python refusal cannot see -- the view is what stands under it."""
    url, _ = loaded
    run = _runs()
    collected_at = next(iter(run))
    with connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(DOCUMENT, (item_id, metadata % collected_at, run[collected_at]))
        conn.commit()
    assert _violations(url) == [(violation, f"youtube_comment:{item_id}")]


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("comment_id", "author", "author_id", "first_seen", "expected"),
    [
        # The archive's own rows: raw on both columns, and first seen before the rule. They are the
        # retroactive pass's work, not a live collector writing raw identifiers today.
        ("OLD", "Some Person", "UCqrNqg3UgVoD3Sa-F_TxuSA", "2026-08-23T22:55:44Z", []),
        ("NAME", "Some Person", None, "2026-09-01T00:00:00Z", ["comment_author_name"]),
        ("ID", None, "UCqrNqg3UgVoD3Sa-F_TxuSA", "2026-09-01T00:00:00Z", ["comment_raw_author_id"]),
        ("HASHED", None, "224427a5eb83274bdf825b8a", "2026-09-01T00:00:00Z", []),
        # Raw without being `UC`-shaped: a handle is an identifier too, and a predicate that names one
        # shape would wave it through.
        ("HANDLE", None, "@some-channel", "2026-09-01T00:00:00Z", ["comment_raw_author_id"]),
    ],
)
def test_the_view_watches_what_the_collector_writes_after_the_cutoff(
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
