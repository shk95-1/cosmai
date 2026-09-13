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

import httpx
import pytest
import sqlalchemy as sa

from analysis.sensitivity.pipeline import DECLARED
from collectors.youtube import flatten, sources, transport
from collectors.youtube.cli import run
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
    flattened_later = datetime(2026, 9, 8, 3, tzinfo=UTC)
    document = flatten.source_metadata(fetched, _payload())
    assert document["collected_at"] == "2026-09-01T12:00:00Z"
    assert document["collected_at"] != flatten.source_metadata(flattened_later, _payload())["collected_at"]


# --- the round trip through the column, from Google's own shape -----------------------------------


def _api_fetcher(body: dict[str, Any]) -> transport.LiveFetcher:
    """A `LiveFetcher` whose Data API arm is served the saved `videos.list` body. Nothing between
    Google's JSON and the column is stubbed: the transport parses the real shape, `sources` and
    `flatten` spell it, and Postgres stores it."""
    budget = transport.RequestBudget(
        {transport.Route.DATA_API: 20}, {transport.Route.DATA_API: 0.0}, sleep=lambda _s: None
    )
    client = transport.DataApiClient(
        "dummy-data-api-key",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=body)),
        budget=budget,
    )
    return transport.LiveFetcher(data_api=client, budget=budget, metadata_route=transport.Route.DATA_API)


def _collect(url: str, tmp_path: Path, video_id: str, body: dict[str, Any]) -> None:
    watchlist = tmp_path / "watch.txt"
    watchlist.write_text(f"video {video_id}\n")
    assert run("watch", read_roster=False, database_url=url, watchlist_path=watchlist, captured_at=AT) == 0
    assert (
        run(
            "work",
            database_url=url,
            fetcher=_api_fetcher(body),
            payload_root=tmp_path / "payloads",
            captured_at=AT,
        )
        == 0
    )
    assert run("flatten", database_url=url, payload_root=tmp_path / "payloads", captured_at=AT) == 0


@pytest.mark.parametrize(
    ("fixture", "video_id", "declared", "caption"),
    [
        ("videos-list.json", VIDEO, "True", "True"),
        ("videos-list-undeclared.json", "9bZkp7q19f0", "False", "False"),
    ],
)
def test_googles_own_shape_reaches_the_column_as_the_string_the_reader_compares(
    tubedepth_schema: str, tmp_path: Path, fixture: str, video_id: str, declared: str, caption: str
):
    """The path this whole part list exists for, end to end and from the fixture rather than from a
    literal: `paidProductPlacementDetails.hasPaidProductPlacement` is a JSON **boolean** in Google's
    response, `contentDetails.caption` is the **string** "true"/"false", and both have to come back
    out of `->>` as "True"/"False" or `analysis/sensitivity/pipeline.py` marks nothing and says
    nothing.

    Before this, the fixture carried no such key and the round trip was asserted from a dict a test
    had typed by hand. That is #90 one level down: the shape nobody fed in is the shape nobody
    proved."""
    body = json.loads((FIXTURES / "data_api" / fixture).read_text(encoding="utf-8"))
    placement = body["items"][0]["paidProductPlacementDetails"]["hasPaidProductPlacement"]
    assert isinstance(placement, bool), "the fixture has to carry Google's boolean, not our string"
    assert isinstance(body["items"][0]["contentDetails"]["caption"], str)

    _collect(tubedepth_schema, tmp_path, video_id, body)

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        stored_declared, stored_caption, duration, views = conn.execute(
            sa.select(
                video_snapshots.c.source_metadata["has_paid_product_placement"].astext,
                video_snapshots.c.source_metadata["caption_available"].astext,
                video_snapshots.c.source_metadata["duration_seconds"].astext,
                video_snapshots.c.source_metadata["view_count"].astext,
            )
        ).one()
    engine.dispose()

    assert stored_declared == declared
    assert (stored_declared == DECLARED) is (declared == "True")
    assert stored_caption == caption
    assert duration == "253"  # PT4M13S, parsed by the transport and spelled by flatten
    assert views == "1523"


def test_the_whole_document_survives_the_jsonb_column(tubedepth_schema: str, tmp_path: Path):
    """The key set as stored, through a real jsonb round trip rather than through a Python dict."""
    body = json.loads((FIXTURES / "data_api" / "videos-list.json").read_text(encoding="utf-8"))
    _collect(tubedepth_schema, tmp_path, VIDEO, body)

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        stored = conn.execute(sa.select(video_snapshots.c.source_metadata)).scalar_one()
    engine.dispose()

    # A set, not the tuple: jsonb stores an object by its own key order, so the archive's order is
    # documented by the builder and the column can only be held to the key *set*.
    assert set(stored) == set(flatten.SOURCE_METADATA_KEYS)
    assert stored["tags"] == ["tag"]


# --- the archive's own document -------------------------------------------------------------------

ARCHIVE_DOCUMENT = Path(__file__).resolve().parents[2] / "fixtures" / "yt_handoff" / "document.csv"
ARCHIVE_INSTANT = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


def _archive_source_metadata() -> dict[str, Any]:
    import csv

    with ARCHIVE_DOCUMENT.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["source_metadata"]:
                return json.loads(row["source_metadata"])
    raise AssertionError("the archive handoff fixture carries no source_metadata to compare against")


def test_the_key_set_is_the_archive_documents_own():
    """Held against the archive's handoff fixture rather than against this module's own tuple: the
    claim DDL 004 makes is about that document, so that document is what it has to be held to."""
    assert set(flatten.SOURCE_METADATA_KEYS) == set(_archive_source_metadata())


def test_collected_at_is_spelled_the_way_the_archive_spells_an_instant():
    """`2026-08-19T05:30:57Z`, not `2026-09-01T12:00:00.123456+00:00`. Both parse and nothing breaks
    on the difference -- but DDL 004 says this column is the archive's spelling verbatim, and a
    comment asserting something the code beside it contradicts is worse than no comment."""
    import re

    archive = _archive_source_metadata()["collected_at"]
    assert re.match(ARCHIVE_INSTANT, archive), f"the archive fixture changed shape: {archive!r}"

    ours = flatten.source_metadata(datetime(2026, 9, 1, 12, 0, 0, 123456, tzinfo=UTC), _payload())
    assert re.match(ARCHIVE_INSTANT, ours["collected_at"])
    assert ours["collected_at"] == "2026-09-01T12:00:00Z"


def test_a_fetch_instant_in_another_zone_is_still_written_in_utc():
    import re
    from datetime import timedelta, timezone

    seoul = timezone(timedelta(hours=9))
    document = flatten.source_metadata(datetime(2026, 9, 1, 21, 0, 0, tzinfo=seoul), _payload())
    assert re.match(ARCHIVE_INSTANT, document["collected_at"])
    assert document["collected_at"] == "2026-09-01T12:00:00Z"
