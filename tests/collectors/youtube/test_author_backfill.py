"""The one-off pre-rule comment-author pass (#288).

The stored value, not merely its 24-hex shape, is the contract: omitting the ``youtube:`` prefix
produces a plausible hash that matches no archive author.  The operation is deliberately boring at
the row boundary: two author columns move and every content, count and timestamp column stays put.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from collectors.youtube import author_backfill
from collectors.youtube.storage.tables import comments
from db.corpus.author import author_hash

AT = datetime(2026, 8, 20, 1, 2, 3, tzinfo=UTC)
RAW_A = "UC0000000000000000000000"
RAW_B = "UC1111111111111111111111"
HASHED = author_hash("UC2222222222222222222222")


@pytest.fixture
def backfill_url(tubedepth_schema: str, _schema_name: str) -> str:
    return (
        make_url(tubedepth_schema)
        .update_query_dict({"options": f"-csearch_path={_schema_name},pg_catalog"})
        .render_as_string(hide_password=False)
    )


def _row(comment_id: str, author_id: str | None, author: str | None) -> dict[str, object]:
    return {
        "video_id": "video-1",
        "comment_id": comment_id,
        "text": f"body-{comment_id}",
        "author": author,
        "author_id": author_id,
        "like_count": 7,
        "is_hearted_by_uploader": True,
        "is_pinned": False,
        "first_seen_at": AT,
        "last_seen_at": AT,
    }


def _insert(url: str, rows: list[dict[str, object]]) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.insert(comments), rows)
    finally:
        engine.dispose()


def _stored(url: str) -> list[tuple[object, ...]]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return [
                tuple(row)
                for row in conn.execute(
                    sa.select(
                        comments.c.comment_id,
                        comments.c.author_id,
                        comments.c.author,
                        comments.c.text,
                        comments.c.like_count,
                        comments.c.first_seen_at,
                        comments.c.last_seen_at,
                    ).order_by(comments.c.comment_id)
                )
            ]
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_dry_run_counts_only_the_rows_the_apply_will_change(backfill_url: str):
    _insert(
        backfill_url,
        [
            _row("raw", RAW_A, "display"),
            _row("hash", HASHED, None),
            _row("name-only", HASHED, "stale display"),
            _row("anonymous", None, None),
        ],
    )

    engine = create_engine(backfill_url)
    try:
        with engine.connect() as conn:
            report = author_backfill.inspect(conn)
    finally:
        engine.dispose()

    assert report.total == 4
    assert report.raw_ids == 1
    assert report.display_names == 2
    assert report.invalid_ids == 0
    assert report.candidates == 2


@pytest.mark.postgres
def test_apply_uses_the_prefixed_hash_preserves_every_other_column_and_is_resumable(
    backfill_url: str,
):
    _insert(
        backfill_url,
        [
            _row("a", RAW_A, "display A"),
            _row("b", RAW_B, "display B"),
            _row("c", HASHED, "stale display"),
            _row("d", HASHED, None),
        ],
    )
    before = _stored(backfill_url)

    engine = create_engine(backfill_url)
    try:
        assert author_backfill.apply(engine, batch_size=2) == 3
        assert author_backfill.apply(engine, batch_size=2) == 0
    finally:
        engine.dispose()

    after = _stored(backfill_url)
    assert [(row[0], row[1], row[2]) for row in after] == [
        ("a", author_hash(RAW_A), None),
        ("b", author_hash(RAW_B), None),
        ("c", HASHED, None),
        ("d", HASHED, None),
    ]
    assert [row[3:] for row in after] == [row[3:] for row in before]


@pytest.mark.postgres
def test_an_unknown_identifier_shape_refuses_the_whole_pass_before_one_row_moves(backfill_url: str):
    _insert(
        backfill_url,
        [
            _row("raw", RAW_A, "display"),
            _row("unknown", "@handle", "display"),
        ],
    )
    before = _stored(backfill_url)

    engine = create_engine(backfill_url)
    try:
        with pytest.raises(author_backfill.UnknownIdentifierShape, match="1 row"):
            author_backfill.apply(engine, batch_size=1)
    finally:
        engine.dispose()

    assert _stored(backfill_url) == before
