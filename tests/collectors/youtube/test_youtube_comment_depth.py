"""The comment instrument: top 100 threads, no replies (#183 fix round).

The branch first shipped 200 comments with up to 8 replies a thread, copied faithfully from the
archived yt-scrapper's `CommentsSource.DEFAULT_LIMIT`. Faithful to the wrong ancestor:
yt-scrapper/tubedepth never produced the archive corpus this lineage is compared against. ydc's
`youtube_collector.py` did, over the Data API's `commentThreads` at `limit = 100` with
`include_replies = False`, and the archive's rows say so -- comments on any one video top out at
exactly 100 with 1,257 videos on that ceiling and none above, and 63,857 threads declare a reply
while zero reply rows exist.

Depth is a one-way door. The live lineage is collected incrementally over years, so deepening it
later never re-collects what was already stored: early quarters stay shallow, late quarters go deep,
one snapshot carries one instrument value and nothing repairs the seam.

**These tests read the stored rows, not the argument list.** yt-dlp's `max_comments` is
`[total, max_parents, max_replies, max_replies_per_thread]` and its first element counts replies
too, so the arguments are exactly the thing that was got wrong once. One test does pin the argument
list -- because zeroing the reply arguments is a real requirement and a fixture cannot show a
request that was never made -- but the tests that carry this file go from a dump that **has** replies
through to what lands in `tubedepth.comments`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from collectors.youtube import models, sources, transport
from collectors.youtube.cli import run
from collectors.youtube.storage.tables import comments as comment_rows

pytestmark = pytest.mark.postgres

AT = datetime(2026, 9, 13, 3, tzinfo=UTC)
VIDEO = "dQw4w9WgXcQ"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
WITH_REPLIES = json.loads((FIXTURES / "ytdlp" / "comments-with-replies.json").read_text(encoding="utf-8"))

TOP_LEVEL_IN_FIXTURE = 3
REPLIES_IN_FIXTURE = 4


class _ReplayingRuntime:
    def __init__(self, dump: dict[str, Any]) -> None:
        self._dump = dump
        self.calls: list[dict[str, Any]] = []

    def extract(self, target: str, *, options: dict[str, Any] | None = None) -> Any:
        self.calls.append(dict(options or {}))
        return self._dump


def _route(dump: dict[str, Any]) -> tuple[transport.YtdlpRoute, _ReplayingRuntime]:
    runtime = _ReplayingRuntime(dump)
    budget = transport.RequestBudget(
        {transport.Route.YTDLP: 20}, {transport.Route.YTDLP: 0.0}, sleep=lambda _s: None
    )
    return transport.YtdlpRoute(runtime=runtime, budget=budget), runtime


# --- the instrument ---------------------------------------------------------------------------


def test_the_depth_is_the_archives_hundred_threads():
    assert models.MAX_COMMENTS_PER_VIDEO == 100


def test_replies_are_not_part_of_the_instrument():
    assert models.COMMENT_INCLUDE_REPLIES is False


def test_the_note_names_ydc_as_the_ancestor_and_not_yt_scrapper():
    """The note was the evidence that found this bug: it said, correctly, that the value was copied
    from yt-scrapper -- which is how the wrong ancestor was spotted. It must not go back to citing
    the system that never produced the corpus."""
    scope = json.loads(
        (Path(__file__).resolve().parents[3] / "collectors" / "youtube" / "scope.json").read_text(
            encoding="utf-8"
        )
    )
    note = scope["MAX_COMMENTS_PER_VIDEO_note"]
    assert "youtube_collector.py" in note
    assert "WRONG ANCESTOR" in note
    assert "ONE-WAY DOOR" in note


# --- what is asked for ------------------------------------------------------------------------


def test_the_reply_arguments_are_zeroed_and_not_left_behind_a_smaller_first_number():
    """`max_comments` is [total, max_parents, max_replies, max_replies_per_thread] and the first
    element counts replies too. `["100", "all", "all", "8"]` asks for 100 comments of which an
    unknown share are replies -- neither 100 threads nor zero replies, which is the shape this fix
    round found in the branch."""
    route, runtime = _route(WITH_REPLIES)
    route.comments(VIDEO, limit=100)

    max_comments = runtime.calls[0]["extractor_args"]["youtube"]["max_comments"]
    assert max_comments == ["100", "100", "0", "0"]
    assert max_comments[2] == "0" and max_comments[3] == "0"


def test_the_limit_reaching_the_extractor_is_the_scope_json_one_by_default():
    route, runtime = _route(WITH_REPLIES)
    route.comments(VIDEO)
    sent = runtime.calls[0]["extractor_args"]["youtube"]["max_comments"]
    assert sent[0] == str(models.MAX_COMMENTS_PER_VIDEO)


# --- what is kept, read from a dump that has replies in it --------------------------------------


def test_a_reply_that_arrives_anyway_is_dropped_rather_than_stored():
    """The arguments are not trusted, because the arguments are what was wrong. A dump carrying
    replies is the only thing that can show the difference."""
    route, _ = _route(WITH_REPLIES)
    dump = route.comments(VIDEO, limit=100)

    assert len(WITH_REPLIES["comments"]) == TOP_LEVEL_IN_FIXTURE + REPLIES_IN_FIXTURE
    assert dump["returned_count"] == TOP_LEVEL_IN_FIXTURE
    assert dump["replies_dropped"] == REPLIES_IN_FIXTURE
    assert dump["include_replies"] is False


def test_the_normalized_payload_carries_no_parent_id_at_all():
    """The archive's shape: `parent_comment_id` null on all 247,338 rows."""
    route, _ = _route(WITH_REPLIES)
    payload = sources.normalize_comments(route.comments(VIDEO, limit=100))
    assert len(payload["comments"]) == TOP_LEVEL_IN_FIXTURE
    assert all(comment["parent_id"] is None for comment in payload["comments"])


def test_truncation_counts_threads_and_not_comments():
    """Three threads and four replies against a limit of three is a full harvest of threads. Counted
    the old way it is seven against three, and every video would be reported truncated."""
    route, _ = _route(WITH_REPLIES)
    assert route.comments(VIDEO, limit=3)["truncated"] is True
    assert route.comments(VIDEO, limit=4)["truncated"] is False


# --- what lands in the table --------------------------------------------------------------------


class _CommentFetcher:
    def __init__(self, route: transport.YtdlpRoute) -> None:
        self._route = route

    def fetch(self, spec: Any) -> dict[str, Any]:
        return self._route.comments(spec.target, limit=models.MAX_COMMENTS_PER_VIDEO)


def test_the_stored_rows_match_the_archives_shape(tubedepth_schema: str, tmp_path: Path):
    """End to end, from a yt-dlp dump that has replies in it to `tubedepth.comments`: top-level
    only, no parent id, and a row count that is the thread count. Asserted here rather than inferred
    from the request, which is the instruction this fix round came with."""
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

    engine = sa.create_engine(tubedepth_schema)
    with engine.begin() as conn:
        from collectors.youtube.storage.tables import jobs

        conn.execute(sa.update(jobs).values(kind="video.comments"))
    engine.dispose()

    route, _ = _route(WITH_REPLIES)
    assert (
        run(
            "work",
            database_url=tubedepth_schema,
            fetcher=_CommentFetcher(route),
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
        rows = conn.execute(sa.select(comment_rows.c.comment_id, comment_rows.c.parent_id)).all()
    engine.dispose()

    assert len(rows) == TOP_LEVEL_IN_FIXTURE
    assert all(row.parent_id is None for row in rows)
    reply_ids = {raw["id"] for raw in WITH_REPLIES["comments"] if raw["parent"] != "root"}
    assert not reply_ids & {row.comment_id for row in rows}
