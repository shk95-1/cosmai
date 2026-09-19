"""A collected comment reaches `tubedepth.comments` with its author hashed and its name dropped (#267).

The oracle is `db/views/author_identifier_violation.sql` itself, applied to the throwaway database and
selected from -- not its predicate re-spelled in Python. Re-spelling it is the failure the rule is about:
the view asks what the value is *not*, and a Python paraphrase that asks whether it looks like a channel
id passes a handle, a legacy `/user/` path and every other raw form (fork #92, `db/corpus/author.py`).

Two paths reach a row, and both are proved here because they can disagree. A harvest normalized after this
change never puts a raw identifier in the payload at all; a payload stored *before* it, and flattened
weeks later, still carries one -- `flatten` re-reads stored payloads and is the last place that can stop
it. The pass without the trap is `test_the_window_is_real…`: the view only watches rows first seen after
`2026-08-24`, so a row written outside that window makes every assertion below pass while proving nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from collectors.youtube import flatten, sources
from collectors.youtube.payload_store import PayloadStore
from collectors.youtube.storage.tables import comments
from db.corpus.author import AUTHOR_HASH, author_hash

ROOT = Path(__file__).resolve().parents[3]
VIEW = ROOT / "db" / "views" / "author_identifier_violation.sql"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DUMP = json.loads((FIXTURES / "ytdlp" / "comments-author-shapes.json").read_text(encoding="utf-8"))

VIDEO = "dQw4w9WgXcQ"
# After the view's 2026-08-24 cutoff, on purpose: a row first seen before it is invisible to the oracle.
AT = datetime(2026, 9, 19, 3, tzinfo=UTC)
RAW_CHANNEL = "UC0000000000000000000000"
HANDLE = "@some-channel"
LEGACY = "/user/legacy-name"


# --- the payload the collector produces ---------------------------------------------------------


def test_the_normalized_payload_keeps_no_name_and_no_raw_identifier():
    """The payload is production data too: a job stores it on disk and `flatten` reads it back weeks
    later, so a raw identifier that reaches it is a raw identifier at rest."""
    payload = sources.normalize_comments(DUMP)
    assert len(payload["comments"]) == len(DUMP["comments"])
    for comment in payload["comments"]:
        assert "author" not in comment
        if comment["author_id"] is not None:
            assert AUTHOR_HASH.fullmatch(comment["author_id"])
    assert [c["author_id"] for c in payload["comments"]] == [
        author_hash(RAW_CHANNEL),
        author_hash(HANDLE),
        author_hash(LEGACY),
        None,
    ]


def test_the_hash_is_the_one_function_and_not_a_second_spelling():
    """`sha256(channel_id)` is the same shape and matches no creator comment. Only the prefixed form
    can be compared against what the archive already stores, so the value is pinned, not the shape."""
    payload = sources.normalize_comments(DUMP)
    assert payload["comments"][0]["author_id"] == author_hash(RAW_CHANNEL)
    assert sources.hashed_author_id is flatten.hashed_author_id


def test_a_value_that_is_already_a_hash_is_not_hashed_again():
    assert sources.hashed_author_id(author_hash(RAW_CHANNEL)) == author_hash(RAW_CHANNEL)
    assert sources.hashed_author_id(None) is None


# --- what the oracle says about the stored rows --------------------------------------------------


@pytest.fixture
def watched(
    needs_schema: str, needs_runtime_url: str, tubedepth_side_schema: str, _schema_name: str
) -> Iterator[tuple[str, str]]:
    """`(the url the view is read through, the url the collector writes tubedepth through)`.

    The view is what a deploy lays on (`db/migrate.sh`), so it is laid on here the same way, as
    needs_owner and with the two grants `db/grants/needs_runtime_reader.sql` gives it -- in production
    the view runs with needs_owner's privilege and reads `tubedepth.comments` through exactly those.
    """
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{tubedepth_side_schema}" TO needs_owner')
            conn.exec_driver_sql(
                f'GRANT SELECT ON ALL TABLES IN SCHEMA "{tubedepth_side_schema}" TO needs_owner'
            )
            conn.exec_driver_sql("SET ROLE needs_owner")
            # Through the DBAPI cursor with no parameter list, the way psql hands db/migrate.sh's
            # file to the server: SQLAlchemy's exec_driver_sql passes an empty parameter tuple, and
            # psycopg then rewrites every `%s` -- including the ones inside the view's own
            # `format('%s/%s', ...)` string literals -- into `$1`, `$2`, so the view that lands
            # reports `$5/$6` as its row_key instead of the row it found.
            cursor = conn.connection.cursor()
            try:
                cursor.execute(
                    VIEW.read_text(encoding="utf-8")
                    .replace("needs.", f'"{_schema_name}".')
                    .replace("tubedepth.", f'"{tubedepth_side_schema}".')
                )
            finally:
                cursor.close()
    finally:
        engine.dispose()
    collector_url = (
        make_url(needs_schema)
        .update_query_dict({"options": f"-csearch_path={tubedepth_side_schema},pg_catalog"})
        .render_as_string(hide_password=False)
    )
    yield needs_runtime_url, collector_url


def _violations(url: str) -> list[tuple[Any, ...]]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return [
                tuple(row)
                for row in conn.execute(
                    sa.text(
                        "SELECT violation, row_key FROM author_identifier_violation"
                        " ORDER BY violation, row_key"
                    )
                ).all()
            ]
    finally:
        engine.dispose()


def _flatten(collector_url: str, payload: dict[str, Any], tmp_path: Path) -> None:
    """The real route from a payload to rows: stored, read back as JSON, and flattened -- `flatten`
    never sees the dump again, so a test that handed it the dict in memory would skip the store."""
    payloads = PayloadStore(tmp_path / "payloads")
    stored = payloads.put("video.comments", payload)
    engine = create_engine(collector_url)
    try:
        with engine.begin() as conn:
            flatten.flatten_one(
                conn,
                payloads,
                artifact_id="artifact-267",
                kind="video.comments",
                target=VIDEO,
                fetched_at=AT,
                digest=stored.digest,
            )
    finally:
        engine.dispose()


def _stored_rows(collector_url: str) -> list[tuple[Any, ...]]:
    engine = create_engine(collector_url)
    try:
        with engine.connect() as conn:
            return [
                tuple(row)
                for row in conn.execute(
                    sa.select(comments.c.comment_id, comments.c.author, comments.c.author_id).order_by(
                        comments.c.comment_id
                    )
                ).all()
            ]
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_window_is_real_a_raw_row_written_beside_it_is_listed(watched: tuple[str, str], tmp_path: Path):
    """The trap this file exists to avoid: outside the view's window every assertion below is green on
    a table full of raw identifiers. A raw row written at the same instant as the collector's own must
    be listed, or the oracle is not watching."""
    read_url, collector_url = watched
    engine = create_engine(collector_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.insert(comments).values(
                    video_id=VIDEO,
                    comment_id="CONTROL",
                    text="raw",
                    author="author 0",
                    author_id=RAW_CHANNEL,
                    is_hearted_by_uploader=False,
                    is_pinned=False,
                    first_seen_at=AT,
                    last_seen_at=AT,
                )
            )
    finally:
        engine.dispose()
    assert _violations(read_url) == [
        ("comment_author_name", f"{VIDEO}/CONTROL"),
        ("comment_raw_author_id", f"{VIDEO}/CONTROL"),
    ]


@pytest.mark.postgres
def test_a_harvest_collected_today_leaves_the_oracle_empty(watched: tuple[str, str], tmp_path: Path):
    read_url, collector_url = watched
    _flatten(collector_url, sources.normalize_comments(DUMP), tmp_path)

    assert _violations(read_url) == []
    rows = _stored_rows(collector_url)
    assert len(rows) == len(DUMP["comments"])
    assert [row[1] for row in rows] == [None] * len(rows)
    assert rows[0] == ("Ug0000000000000010", None, author_hash(RAW_CHANNEL))


@pytest.mark.postgres
def test_a_payload_stored_before_this_change_is_hashed_on_its_way_into_the_row(
    watched: tuple[str, str], tmp_path: Path
):
    """A payload written by the old normalizer is on disk already and is flattened after the change.
    `flatten` is the last gate it passes, so it may not trust what the payload carries."""
    read_url, collector_url = watched
    pre_change = {
        "comments": [
            {
                "comment_id": "Ug0000000000000020",
                "parent_id": None,
                "text": "comment 0",
                "author": "author 0",
                "author_id": RAW_CHANNEL,
                "like_count": 0,
                "is_hearted_by_uploader": False,
                "is_pinned": False,
                "published_at": AT,
            },
            {
                "comment_id": "Ug0000000000000021",
                "parent_id": None,
                "text": "comment 1",
                "author": "author 1",
                "author_id": HANDLE,
                "like_count": 1,
                "is_hearted_by_uploader": False,
                "is_pinned": False,
                "published_at": AT,
            },
        ]
    }
    _flatten(collector_url, pre_change, tmp_path)

    assert _violations(read_url) == []
    assert _stored_rows(collector_url) == [
        ("Ug0000000000000020", None, author_hash(RAW_CHANNEL)),
        ("Ug0000000000000021", None, author_hash(HANDLE)),
    ]
