"""`video_snapshots.description` (#264, DDL 006): the field we already fetch reaches a column, and
the projection joins it to the title the archive's way.

Before this issue `collectors/youtube/flatten.py` held no reference to `description` at all, while
`transport.video_metadata` had been reading `snippet.description` into the dump all along. Nothing
failed: the row was written, the analysis ran, and the corpus was a twentieth of the archive's text
(average 45 characters of title against 856 of title+description) carrying about a quarter of its
topic mentions. So the assertions here are about a value being *present*, which is the only shape a
test of this defect can take.

The join is ydc `to_common_schema.py:66` `video_text()` -- `normalize_text(f"{title} {description}")`
-- and is not re-derived here: title first, one space, the whole string normalised after joining,
tags excluded. It is the corpus manifest's own `text_rule` (db/corpus/contract.py TEXT_RULE).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from analysis.retrieval import corpus
from analysis.retrieval.normalize import _once, normalize_text
from collectors.youtube import flatten, sources
from collectors.youtube.storage.tables import video_snapshots

pytestmark = pytest.mark.postgres

AT = datetime(2026, 9, 13, 3, tzinfo=UTC)
VIDEO = "dQw4w9WgXcQ"


def _dump(**overrides: Any) -> dict[str, Any]:
    """The shape both routes hand the normalizer: a yt-dlp dump and `transport.video_metadata`'s
    `videos.list` translation carry the same keys, `description` among them."""
    dump: dict[str, Any] = {
        "id": VIDEO,
        "title": "long upload",
        "description": "sunscreen review, no white cast",
        "duration": 253,
        "view_count": 1523,
    }
    dump.update(overrides)
    return dump


def _engine(url: str) -> sa.Engine:
    return sa.create_engine(url)


def _store(url: str, payload: dict[str, Any], *, artifact_id: str = "a1") -> None:
    engine = _engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.insert(video_snapshots).values(flatten.video_snapshot_row(artifact_id, VIDEO, AT, payload))
            )
    finally:
        engine.dispose()


def _read(url: str, artifact_id: str = "a1") -> str | None:
    engine = _engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                sa.select(video_snapshots.c.description).where(video_snapshots.c.artifact_id == artifact_id)
            ).scalar_one()
    finally:
        engine.dispose()


# --- it reaches the row ---------------------------------------------------------------------------


def test_the_normalizer_carries_the_description_the_fetch_already_paid_for():
    assert sources.normalize_video_metadata(_dump())["description"] == "sunscreen review, no white cast"


def test_a_stored_row_carries_its_description(tubedepth_schema: str):
    _store(tubedepth_schema, sources.normalize_video_metadata(_dump()))
    assert _read(tubedepth_schema) == "sunscreen review, no white cast"


def test_an_empty_description_is_not_the_same_as_one_that_was_never_persisted(tubedepth_schema: str):
    """'' is the uploader having written none; NULL is a row flattened before DDL 006 existed, which
    nobody ever asked for a description. Collapsing the two makes the second read as the first."""
    _store(tubedepth_schema, sources.normalize_video_metadata(_dump(description="")), artifact_id="empty")
    # A payload stored before this issue has no `description` key at all -- not an empty one.
    legacy = sources.normalize_video_metadata(_dump())
    del legacy["description"]
    _store(tubedepth_schema, legacy, artifact_id="legacy")

    assert _read(tubedepth_schema, "empty") == ""
    assert _read(tubedepth_schema, "legacy") is None


# --- it reaches the projected text ------------------------------------------------------------------


def _connect(url: str, schema: str) -> psycopg.Connection:
    parsed = make_url(url)
    conn = psycopg.connect(
        host=parsed.host,
        port=parsed.port,
        user=parsed.username,
        password=parsed.password,
        dbname=parsed.database,
        options=f"-csearch_path={schema},pg_catalog",
    )
    conn.autocommit = True
    return conn


def _project(url: str, schema: str, title: str, description: str | None) -> str:
    conn = _connect(url, schema)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO video_snapshots (artifact_id, video_id, fetched_at, title, description) "
                "VALUES ('a1', 'v1', now(), %s, %s)",
                (title, description),
            )
        return next(iter(corpus.youtube_videos(conn, schema))).text
    finally:
        conn.close()


def test_the_projected_text_is_title_then_one_space_then_description(tubedepth_schema, _schema_name):
    assert _project(tubedepth_schema, _schema_name, "a title", "a description") == "a title a description"


def test_a_video_with_no_description_projects_to_the_title_alone(tubedepth_schema, _schema_name):
    """A row from before DDL 006 must still be one document, not the string 'None' glued to a title
    and not a title with a trailing space."""
    assert _project(tubedepth_schema, _schema_name, "a title", None) == "a title"


def test_a_description_carrying_an_html_entity_normalises_to_a_fixed_point(tubedepth_schema, _schema_name):
    """Measured for #264 over the archive's 14,467 raw video rows: **0 titles** carry an HTML entity
    and **1 description** does (three `&amp;`), with **0** carrying a double escape. So the fork's
    "0 of 27,318 titles" holds on descriptions too by a margin of one round, and the entity axis is
    real on descriptions where it was empty on titles.

    A single normalising pass leaves a double escape half-resolved, and `chunks.check_rows` judges
    the contract by `text != normalize_text(text)` -- so a chunk stored after one pass is flagged
    un-normalised for as long as it exists. The fixed-point loop is what stops that, and this asserts
    the loop rather than the result of running it twice."""
    description = "Coupang &amp;amp; iHerb &lt;link&gt;"
    projected = _project(tubedepth_schema, _schema_name, "a title", description)
    assert projected == normalize_text(projected), "the projected text has to be a fixed point"
    assert "&amp;" not in projected and "&lt;" not in projected
    # One pass is not enough, which is the whole reason cosmai's normalizer loops where ydc's does not.
    assert _once(f"a title {description}") != projected
