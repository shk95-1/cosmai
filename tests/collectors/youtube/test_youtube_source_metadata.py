"""`video_snapshots.source_metadata`: nine keys, in the archive's spelling (#183, DDL 004).

The spelling is not JSON-natural and that is the point of this file. The archive stored a Python
`repr`: the booleans are `"True"`/`"False"` capitalised and the numbers are `"1234"` rather than
1234, a value nobody could supply is jsonb null, and every one of the nine keys is present on all
13,979 archive rows (like_count null on 876 of them, duration_seconds on 6, comment_count on 5).

Only one of those bites today, and it bites silently: `analysis/sensitivity/pipeline.py` reads
`source_metadata ->> 'has_paid_product_placement'` and compares it against its `DECLARED` constant.
Write JSON's `true` and that comparison is false for every live row -- no error, rows still
returned, and the declared half of ad marking becomes zero. contracts/interfaces.md puts the cost at
254 reported of 465 in union, so about 12% of ad videos would go unmarked.

So the assertions below are made against `analysis.sensitivity.pipeline.DECLARED` itself rather than
against the literal `"True"`. The day somebody normalises either side, a test fails instead of a
measurement.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from analysis.sensitivity.pipeline import DECLARED
from collectors.youtube import flatten, sources
from collectors.youtube.cli import FetchSpec, run
from collectors.youtube.storage.tables import video_snapshots

pytestmark = pytest.mark.postgres

AT = datetime(2026, 9, 13, 3, tzinfo=UTC)
VIDEO = "dQw4w9WgXcQ"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _payload(**overrides: Any) -> dict[str, Any]:
    dump = {
        "id": VIDEO,
        "title": "long upload",
        "duration": 253,
        "view_count": 1523,
        "like_count": 88,
        "comment_count": 12,
        "tags": ["tag"],
        "category_id": "26",
        "caption_available": "true",
        "has_paid_product_placement": True,
    }
    dump.update(overrides)
    return sources.normalize_video_metadata(dump)


# --- the spelling -----------------------------------------------------------------------------


def test_the_document_carries_exactly_the_nine_keys():
    assert tuple(flatten.source_metadata(AT, _payload())) == flatten.SOURCE_METADATA_KEYS
    assert len(flatten.SOURCE_METADATA_KEYS) == 9


def test_a_value_nobody_supplied_is_jsonb_null_and_not_the_string_None():
    """The natural mistake, given that the rest of the column is a Python repr: `str(None)` is
    `"None"`, which reads back through `->>` as the four characters N-o-n-e."""
    document = flatten.source_metadata(AT, _payload(like_count=None, duration=None, comment_count=None))
    for key in ("like_count", "duration_seconds", "comment_count"):
        assert document[key] is None
        assert document[key] != "None"
    # Present-with-null, not absent: the archive's key set is complete on every one of its rows.
    assert tuple(document) == flatten.SOURCE_METADATA_KEYS


def test_the_declared_flag_is_spelled_the_way_the_reader_of_it_compares():
    declared = flatten.source_metadata(AT, _payload(has_paid_product_placement=True))
    assert declared["has_paid_product_placement"] == DECLARED
    not_declared = flatten.source_metadata(AT, _payload(has_paid_product_placement=False))
    assert not_declared["has_paid_product_placement"] != DECLARED
    assert not_declared["has_paid_product_placement"] == "False"


def test_a_route_that_cannot_report_the_flag_leaves_it_null_rather_than_false():
    """yt-dlp has no paid-placement field of any kind. "This route could not tell us" and "the
    uploader said no" are different facts, and only one of them is evidence."""
    unknown = flatten.source_metadata(AT, _payload(has_paid_product_placement=None))
    assert unknown["has_paid_product_placement"] is None
    assert unknown["has_paid_product_placement"] != "False"


def test_caption_available_is_the_same_capitalised_string_as_the_declared_flag():
    """The Data API sends `contentDetails.caption` as the *string* "true"/"false", so the value
    arrives lowercase and has to be re-spelled rather than passed through."""
    document = flatten.source_metadata(AT, _payload(caption_available="true"))
    assert document["caption_available"] == "True"
    assert flatten.source_metadata(AT, _payload(caption_available="false"))["caption_available"] == "False"


def test_the_numbers_are_strings_and_the_tags_are_an_array():
    document = flatten.source_metadata(AT, _payload())
    assert document["view_count"] == "1523"
    assert document["duration_seconds"] == "253"
    assert document["like_count"] == "88"
    assert document["comment_count"] == "12"
    assert document["category_id"] == "26"
    assert document["tags"] == ["tag"]


def test_collected_at_is_the_fetch_instant_and_not_the_flatten_pass():
    """flatten runs on its own cadence and can be days behind the fetch; `collected_at` means when
    the video was observed."""
    fetched = datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert flatten.source_metadata(fetched, _payload())["collected_at"] == fetched.isoformat()


# --- the round trip through the column ----------------------------------------------------------


class _MetadataFetcher:
    def __init__(self, dump: dict[str, Any]) -> None:
        self._dump = dump

    def fetch(self, spec: FetchSpec) -> dict[str, Any]:
        return self._dump


def test_the_document_survives_the_jsonb_column_and_reads_back_through_the_arrow_operator(
    tubedepth_schema: str, tmp_path: Path
):
    """The assertion the unit tests above cannot make: that `->>` on the stored column returns
    exactly what `analysis/sensitivity/pipeline.py` compares against, through a real jsonb round
    trip rather than through a Python dict."""
    dump = json.loads((FIXTURES / "data_api" / "videos-list.json").read_text(encoding="utf-8"))
    item = dump["items"][0]
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text(f"video {VIDEO}\n")
    assert (
        run(
            "watch",
            read_roster=False,
            database_url=tubedepth_schema,
            watchlist_path=watchlist,
            captured_at=AT,
        )
        == 0
    )
    fetched = {
        "id": item["id"],
        "title": item["snippet"]["title"],
        "duration": 253,
        "view_count": int(item["statistics"]["viewCount"]),
        "like_count": int(item["statistics"]["likeCount"]),
        "comment_count": int(item["statistics"]["commentCount"]),
        "tags": item["snippet"]["tags"],
        "category_id": item["snippet"]["categoryId"],
        "caption_available": item["contentDetails"]["caption"],
        "has_paid_product_placement": True,
        "fetch_route": "data_api",
    }
    assert (
        run(
            "work",
            database_url=tubedepth_schema,
            fetcher=_MetadataFetcher(fetched),
            payload_root=tmp_path / "payloads",
            captured_at=AT,
        )
        == 0
    )
    assert (
        run("flatten", database_url=tubedepth_schema, payload_root=tmp_path / "payloads", captured_at=AT) == 0
    )

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        declared, caption, views, stored = conn.execute(
            sa.select(
                video_snapshots.c.source_metadata["has_paid_product_placement"].astext,
                video_snapshots.c.source_metadata["caption_available"].astext,
                video_snapshots.c.source_metadata["view_count"].astext,
                video_snapshots.c.source_metadata,
            )
        ).one()
    engine.dispose()

    assert declared == DECLARED
    assert caption == "True"
    # `->>` reads a jsonb number and a jsonb string the same way, which is why the numbers being
    # strings is safe -- and why a consumer moving to `->` has to revisit the column (DDL 004).
    assert views == "1523"
    # A set, not the tuple: jsonb stores an object by its own key order, so the archive's order is
    # documented by the builder above and the column can only be held to the key *set*.
    assert set(stored) == set(flatten.SOURCE_METADATA_KEYS)
    assert stored["tags"] == ["tag"]
