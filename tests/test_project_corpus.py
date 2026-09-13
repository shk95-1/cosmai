"""`project:corpus` -- the live lineage projected out of tubedepth (fork #95, from #93 D1·D2).

Every failure this stage can have returns rows and raises nothing, so the tests are arranged by the
way each one would be wrong rather than by the function it lives in.

* **The long/short rule.** Recovered from ydc `v0.4.0` `76db718` and worth nothing unless it
  reproduces the archive's own split -- 611 of the archive's videos sit on exactly 60 seconds, so an
  exclusive boundary moves all of them and every `composition` with them.
* **The three ways a rebuilt `source_metadata` diverges quietly** -- a Python-repr boolean written as
  a JSON boolean, a missing value written as the string `"None"` instead of jsonb `null`, and a key
  dropped because it is always null. One arm each, and the same three asked again of the stored rows
  after a projection pass has run beside the archive.
* **The author.** One hash, imported, never re-spelled -- and never hashed twice.
* **What is never touched.** Snapshot 1, and the `collected_at` of a row already written.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, LiteralString

import pytest
from psycopg import sql as pgsql
from sqlalchemy import create_engine

from analysis.sensitivity.pipeline import DECLARED
from collectors.youtube.flatten import SOURCE_METADATA_KEYS
from collectors.youtube.models import (
    COMMENT_INCLUDE_REPLIES,
    COMMENT_REFETCH_WINDOW_DAYS,
    MAX_COMMENTS_PER_VIDEO,
)
from db import corpus, seed
from db.corpus import contract, project
from db.corpus.author import author_hash
from db.seed._common import connect

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "yt_handoff"
ARCHIVE_SAMPLE = ROOT / "tests" / "fixtures" / "trend_sample" / "corpus" / "document.csv"
VIOLATION_VIEW = ROOT / "db" / "views" / "author_identifier_violation.sql"
ROUTE_VIEW = ROOT / "db" / "views" / "live_listing_route.sql"

PANEL_CHANNEL = "UCqrNqg3UgVoD3Sa-F_TxuSA"
RAW_AUTHOR = "UCzzzzzzzzzzzzzzzzzzzzzz"
ROUTE = "data_api"
# The instant every fixture row is fetched at, and the one an hour later that proves the projection
# keeps the earlier of two observations of the same video.
FIRST = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
LATER = FIRST + timedelta(hours=1)
PUBLISHED = datetime(2026, 8, 20, 9, 0, tzinfo=UTC)


# ---------- the long/short rule ----------
@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (None, project.VIDEO_UNKNOWN),
        ("", project.VIDEO_UNKNOWN),
        ("P0D", project.VIDEO_UNKNOWN),  # ydc read an ISO-8601 duration as text; a live stream is P0D
        (0, project.VIDEO_SHORT),
        (59, project.VIDEO_SHORT),
        (60, project.VIDEO_SHORT),  # the boundary is inclusive -- 611 archive videos sit on it
        (61, project.VIDEO_LONG),
        ("620", project.VIDEO_LONG),
    ],
)
def test_the_long_short_rule_is_ydcs_and_the_sixty_second_boundary_is_inclusive(duration, expected):
    assert project.content_type_of(duration) == expected


def _archive_rows(source: str) -> list[dict[str, Any]]:
    csv.field_size_limit(10**7)
    with ARCHIVE_SAMPLE.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["source"] == source]
    return [dict(row, metadata=json.loads(row["source_metadata"] or "{}")) for row in rows]


def test_the_rule_reproduces_the_split_the_archive_already_stores():
    """The verification the issue asks for before the rule is used, run on the archive sample that is
    in the repo. Production's own 13,979 videos agree 13,979/13,979 (the measurement on fork #95);
    what a test can carry is the same question asked of the rows this checkout has."""
    videos = _archive_rows("youtube_video")
    assert videos, "the golden sample carries no video rows"
    derived = [project.content_type_of(v["metadata"]["duration_seconds"]) for v in videos]
    assert derived == [v["content_type"] for v in videos]
    # The rule is exercised on more than one branch here, or agreement says nothing.
    assert len(set(derived)) > 1


def test_an_exclusive_boundary_differs_from_this_rule_at_exactly_one_value():
    """Why the inclusive comparison is pinned: the wrong one raises nothing and moves a denominator.

    611 of production's archive videos have a duration of exactly 60 (measured on fork #95), and the
    two rules differ on that value and on no other -- which is why the mistake is invisible in every
    test that does not name the boundary. This checkout's own sample happens to hold no 60-second
    video, so the counterfactual is asserted against the rule rather than against those rows.
    """

    def exclusive(seconds: int) -> str:
        return project.VIDEO_SHORT if seconds < project.SHORTS_MAX_SECONDS else project.VIDEO_LONG

    differ = [n for n in range(0, 121) if exclusive(n) != project.content_type_of(n)]
    assert differ == [project.SHORTS_MAX_SECONDS]
    assert project.content_type_of(60) == project.VIDEO_SHORT


# ---------- the author hash ----------
def test_the_hash_is_the_one_db_corpus_author_names_and_is_never_re_implemented():
    assert project.author_hash is author_hash
    assert project.channel_hash(RAW_AUTHOR) == author_hash(RAW_AUTHOR)


def test_a_value_that_is_already_a_hash_is_not_hashed_again():
    """`tubedepth.comments.author_id` holds the raw id today and a hash once the collector stops it at
    the source (contracts/formats.md). Hashing a hash matches no creator comment and raises nothing."""
    already = author_hash(RAW_AUTHOR)
    assert project.channel_hash(already) == already
    assert project.channel_hash(None) is None


# ---------- the three ways a rebuilt source_metadata diverges ----------
def _comment_metadata(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "comment_id": "CMT_LIVE_1",
        "parent_id": None,
        "author_id": RAW_AUTHOR,
        "like_count": 3,
        "seen": FIRST,
    }
    return project.comment_metadata(**(fields | over))


def test_the_comment_side_carries_exactly_the_archives_seven_keys():
    archive = {tuple(sorted(row["metadata"])) for row in _archive_rows("youtube_comment")}
    assert archive == {tuple(sorted(_comment_metadata()))}
    assert archive == {tuple(sorted(contract.COMMENT_METADATA_KEYS))}


def test_arm_one_a_python_repr_boolean_is_not_written_as_a_json_boolean():
    """`analysis/sensitivity/pipeline.py` compares the raw string. A JSON `true` makes that comparison
    false for every live row and the declared half of ad marking becomes zero, silently."""
    assert _comment_metadata(parent_id="CMT_PARENT")["is_reply"] == DECLARED
    assert _comment_metadata()["is_reply"] == "False"
    body = json.dumps(_comment_metadata(), ensure_ascii=False)
    assert '"is_reply": "False"' in body
    assert "true" not in body and "false" not in body
    # The archive's own rows are the standard the assertion above is measured against.
    assert {row["metadata"]["is_reply"] for row in _archive_rows("youtube_comment")} <= {"True", "False"}


def test_arm_two_a_missing_value_is_jsonb_null_and_never_the_string_none():
    built = _comment_metadata(like_count=None)
    assert built["like_count"] is None
    body = json.dumps(built, ensure_ascii=False)
    assert '"like_count": null' in body
    assert "None" not in body


def test_arm_three_a_key_that_is_always_null_is_still_present():
    """`parent_comment_id` is jsonb null on all 247,338 archive comment rows while the *column*
    `parent_item_id` carries the relationship. A key dropped for being empty is a document that no
    longer compares against the archive's, and nothing raises."""
    built = _comment_metadata()
    assert "parent_comment_id" in built
    assert built["parent_comment_id"] is None
    assert built["total_reply_count"] is None  # no live source; the key stays all the same
    assert all(row["metadata"]["parent_comment_id"] is None for row in _archive_rows("youtube_comment"))


def test_the_video_side_is_copied_rather_than_rebuilt():
    """There is no per-key builder for a video, and that is the point -- the nine keys are read out of
    `tubedepth.video_snapshots.source_metadata` as the text they were stored as and handed back to
    `%s::jsonb` untouched. Re-assembly is the step the archive's spelling is lost in, so the absence of
    it is asserted rather than assumed: none of the nine names appears as a key this module writes."""
    body = (ROOT / "db" / "corpus" / "project.py").read_text(encoding="utf-8")
    assert "v.source_metadata::text" in project.VIDEO_PAGE.as_string(None)
    # `like_count` and `collected_at` are named by both sides, and the comment side really does write
    # them, so the guard is the seven keys only a video can have.
    video_only = set(SOURCE_METADATA_KEYS) - set(contract.COMMENT_METADATA_KEYS)
    assert len(video_only) == 7
    written = sorted(key for key in video_only if f'"{key}":' in body)
    assert written == [], written


# ---------- the allow-list, and the view that mirrors it ----------
def test_the_loader_refuses_a_comment_key_the_archive_never_carried():
    row = next(r for r in corpus.read_csv(FIXTURE / "document.csv") if r["source"] == project.COMMENT)
    metadata = json.loads(row["source_metadata"]) | {"author_name": "Some Person"}
    with pytest.raises(contract.AuthorIdentifierRefused) as raised:
        contract.check_author(row["doc_id"], metadata, source=project.COMMENT)
    assert "author_name" in str(raised.value)
    assert "Some Person" not in str(raised.value)


def test_a_nested_object_cannot_hide_an_author_from_the_allow_list():
    with pytest.raises(contract.AuthorIdentifierRefused):
        contract.check_author(
            "youtube_comment:X", {"snippet": {"authorDisplayName": "Some Person"}}, source=project.COMMENT
        )


def test_the_view_asks_the_allow_list_the_loader_asks():
    body = VIOLATION_VIEW.read_text(encoding="utf-8")
    for key in contract.COMMENT_METADATA_KEYS:
        assert f"'{key}'" in body, key
    assert "jsonb_object_keys" in body and "EXCEPT" in body
    assert "corpus_comment_metadata_key" in body


# ---------- the quality flags ----------
def _comment_row(comment_id: str, body: str, seen: datetime = FIRST) -> tuple[Any, ...]:
    return ("VID_LIVE_1", comment_id, None, body, RAW_AUTHOR, 1, PUBLISHED, seen)


def test_a_duplicate_inside_one_video_is_flagged_and_the_first_copy_is_not():
    rows = [
        _comment_row("C1", "the same words"),
        _comment_row("C2", "the same words"),
        _comment_row("C3", "other words"),
    ]
    built, undated = project.comment_documents(rows, {"VID_LIVE_1": PANEL_CHANNEL}, 2)
    assert [row[9] for row in built] == [project.CLEAN, project.DUPLICATE_IN_PARENT, project.CLEAN]
    assert undated == 0


def test_a_row_carries_one_flag_and_never_two_joined():
    """Every consumer matches `quality_flags` exactly (`analysis/trend/pipeline.py`'s COUNTED_FLAGS is
    a value list), so a row carrying both joined would be counted by neither."""
    rows = [_comment_row("C1", "   "), _comment_row("C2", "")]
    built, _ = project.comment_documents(rows, {"VID_LIVE_1": PANEL_CHANNEL}, 2)
    assert [row[9] for row in built] == [project.EMPTY_TEXT, project.EMPTY_TEXT]


def test_a_comment_with_no_time_of_its_own_is_counted_rather_than_given_one():
    rows = [("VID_LIVE_1", "C1", None, "a body", RAW_AUTHOR, 1, None, FIRST)]
    built, undated = project.comment_documents(rows, {"VID_LIVE_1": PANEL_CHANNEL}, 2)
    assert built == [] and undated == 1


# ---------- the stage against a database ----------
def _video_metadata(**over: Any) -> str:
    """The nine keys as `collectors/youtube/flatten.source_metadata` writes them: every value a string
    but `tags`, jsonb null for what nobody could tell us."""
    nine: dict[str, Any] = {
        "tags": ["suncare"],
        "has_paid_product_placement": "True",
        "category_id": "26",
        "caption_available": "False",
        "duration_seconds": "620",
        "view_count": "12000",
        "like_count": "310",
        "comment_count": "44",
        "collected_at": "2026-09-01T06:00:00Z",
    }
    return json.dumps(nine | over, ensure_ascii=False)


ARTIFACT = pgsql.SQL(
    "INSERT INTO {s}.artifacts (identifier, kind, target, fingerprint, digest, byte_count,"
    " fetched_at, fresh_until, fetch_route)"
    " VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s)"
)
SNAPSHOT = pgsql.SQL(
    "INSERT INTO {s}.video_snapshots (artifact_id, video_id, fetched_at, title, channel_id,"
    " duration_seconds, published_at, source_metadata)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)"
)
LISTING = pgsql.SQL(
    "INSERT INTO {s}.listing_entries (artifact_id, position, kind, target, fetched_at, video_id,"
    " title, channel_id) VALUES (%s, %s, 'channel.videos', %s, %s, %s, %s, %s)"
)
CMT = pgsql.SQL(
    "INSERT INTO {s}.comments (video_id, comment_id, parent_id, text, author, author_id, like_count,"
    " is_hearted_by_uploader, is_pinned, published_at, first_seen_at, last_seen_at)"
    " VALUES (%s, %s, NULL, %s, %s, %s, %s, false, false, %s, %s, %s)"
)


def _stand_up_tubedepth(url: str, schema: str) -> None:
    """The rows a live collector would have left behind. Written as needs_migrator, the role that owns
    the throwaway schema, because the projection reads them as needs_runtime and must not be the one
    that could have put them there."""
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            ARTIFACT.format(s=pgsql.Identifier(schema)),
            ("ART_LIST", "channel.videos", PANEL_CHANNEL, "fp", "dg", FIRST, LATER, ROUTE),
        )
        # A route nobody used for listings: `video.metadata` has a route of its own, and the
        # instrument key means the listing's.
        cur.execute(
            ARTIFACT.format(s=pgsql.Identifier(schema)),
            ("ART_META", "video.metadata", "V_LONG", "fp", "dg", FIRST, LATER, "ytdlp"),
        )
        rows = [
            # The same video observed twice: the earlier observation is the one kept (#93 D2).
            (
                "ART_1",
                "V_LONG",
                FIRST,
                "a long form title",
                PANEL_CHANNEL,
                620,
                PUBLISHED,
                _video_metadata(),
            ),
            (
                "ART_2",
                "V_LONG",
                LATER,
                "a long form title",
                PANEL_CHANNEL,
                620,
                PUBLISHED,
                _video_metadata(view_count="99999", collected_at="2026-09-01T07:00:00Z"),
            ),
            # Exactly on the boundary.
            (
                "ART_3",
                "V_SHORT",
                FIRST,
                "a short",
                PANEL_CHANNEL,
                60,
                PUBLISHED,
                _video_metadata(duration_seconds="60"),
            ),
            # A live stream: no duration, and the null is what carries video_unknown.
            (
                "ART_4",
                "V_UNKNOWN",
                FIRST,
                "a live stream",
                PANEL_CHANNEL,
                None,
                PUBLISHED,
                _video_metadata(duration_seconds=None, like_count=None),
            ),
            ("ART_6", "V_UNDATED", FIRST, "no time of its own", PANEL_CHANNEL, 300, None, _video_metadata()),
            # Off the panel: the projection's population is the active roster, nothing else.
            (
                "ART_7",
                "V_OFFPANEL",
                FIRST,
                "off the panel",
                "UCnotinroster0000000000",
                300,
                PUBLISHED,
                _video_metadata(),
            ),
        ]
        for row in rows:
            cur.execute(SNAPSHOT.format(s=pgsql.Identifier(schema)), row)
        # Flattened before contracts/ddl/tubedepth/004: no document to copy, so no document is
        # written -- ON CONFLICT DO NOTHING would make that emptiness permanent.
        cur.execute(
            SNAPSHOT.format(s=pgsql.Identifier(schema)),
            ("ART_5", "V_NOMETA", FIRST, "no metadata yet", PANEL_CHANNEL, 300, PUBLISHED, None),
        )
        cur.execute(
            LISTING.format(s=pgsql.Identifier(schema)),
            ("ART_LIST", 0, PANEL_CHANNEL, FIRST, "V_LISTED_ONLY", "listed, not flattened", PANEL_CHANNEL),
        )
        comments = [
            ("V_LONG", "C1", "the same words", "Some Person", RAW_AUTHOR, 7, PUBLISHED, FIRST, FIRST),
            ("V_LONG", "C2", "the same words", "Other Person", RAW_AUTHOR, None, PUBLISHED, LATER, LATER),
            ("V_LONG", "C3", "   ", "Third Person", RAW_AUTHOR, 1, PUBLISHED, FIRST, FIRST),
            ("V_LONG", "C4", "other words", None, author_hash(RAW_AUTHOR), 2, PUBLISHED, FIRST, FIRST),
            ("V_LONG", "C5", "no time of its own", None, RAW_AUTHOR, 0, None, FIRST, FIRST),
            # A comment on a video this projection never writes a document for.
            ("V_NOMETA", "C6", "no parent document", None, RAW_AUTHOR, 0, PUBLISHED, FIRST, FIRST),
        ]
        for row in comments:
            cur.execute(CMT.format(s=pgsql.Identifier(schema)), row)
        conn.commit()


@pytest.fixture
def live(
    needs_schema: str, needs_runtime_url: str, tubedepth_side_schema: str, _schema_name: str
) -> tuple[str, str]:
    """The archive and the live source side by side, the way production has them: snapshot 1 loaded
    from the handover fixture, `tubedepth` in a schema of its own, and both views laid on the way
    db/migrate.sh stage (f) lays them on."""
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            for role in ("needs_owner", "needs_runtime"):
                conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{tubedepth_side_schema}" TO {role}')
                conn.exec_driver_sql(
                    f'GRANT SELECT ON ALL TABLES IN SCHEMA "{tubedepth_side_schema}" TO {role}'
                )
            conn.exec_driver_sql("SET ROLE needs_owner")
            for path in (VIOLATION_VIEW, ROUTE_VIEW):
                conn.exec_driver_sql(
                    path.read_text(encoding="utf-8")
                    .replace("needs.", f'"{_schema_name}".')
                    .replace("tubedepth.", f'"{tubedepth_side_schema}".')
                )
    finally:
        engine.dispose()
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn:
        corpus.load(conn, FIXTURE)
    _stand_up_tubedepth(needs_schema, tubedepth_side_schema)
    return needs_runtime_url, tubedepth_side_schema


def _rows(url: str, statement: LiteralString, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute(statement, params)
        return cur.fetchall()


ARCHIVE_DIGEST: LiteralString = (
    "SELECT md5(string_agg(source_metadata::text || '|' || content_type || '|' || quality_flags"
    " || '|' || text || '|' || collected_at::text, E'\\n' ORDER BY doc_id))"
    " FROM corpus_document WHERE snapshot_id = 1"
)


@pytest.mark.postgres
def test_the_projection_writes_the_live_snapshot_and_nothing_else(live: tuple[str, str]):
    url, schema = live
    with connect(url) as conn:
        outcome = project.project(conn, youtube_schema=schema)

    assert outcome.snapshot_id != corpus.SNAPSHOT_ID
    assert outcome.status == "ok", outcome.violations
    # V_NOMETA is deferred, V_UNDATED has no published_at, V_OFFPANEL is not on the roster.
    assert outcome.counts.videos == 3
    assert outcome.counts.deferred_videos == 1
    assert outcome.counts.undated_videos == 1
    assert outcome.counts.unflattened_listed == 1
    # C5 has no time of its own; C6 hangs on a video that got no document.
    assert outcome.counts.comments == 4
    assert outcome.counts.undated_comments == 1

    snapshot = _rows(
        url,
        "SELECT label, lineage, active, produced_by, source_runs, collected_at, instrument"
        " FROM corpus_snapshot WHERE snapshot_id = %s",
        (outcome.snapshot_id,),
    )[0]
    label, lineage, active, produced_by, source_runs, collected_at, instrument = snapshot
    assert (label, lineage, produced_by) == (project.LIVE_LABEL, "live", project.LIVE_PRODUCED_BY)
    # The coordinator switches the active snapshot by hand (#93 D5); a cron line never does.
    assert active is False
    assert source_runs == [project.SOURCE_RUN]
    assert collected_at == FIRST
    assert instrument["listing_route"] == [ROUTE]
    assert instrument["refetch_window_days"] == COMMENT_REFETCH_WINDOW_DAYS
    assert instrument["comment_depth"] == {
        "threads": MAX_COMMENTS_PER_VIDEO,
        "include_replies": COMMENT_INCLUDE_REPLIES,
    }

    documents = dict(
        _rows(
            url,
            "SELECT source_item_id, content_type FROM corpus_document"
            " WHERE snapshot_id = %s AND source = %s ORDER BY 1",
            (outcome.snapshot_id, project.VIDEO),
        )
    )
    assert documents == {
        "V_LONG": project.VIDEO_LONG,
        "V_SHORT": project.VIDEO_SHORT,
        "V_UNKNOWN": project.VIDEO_UNKNOWN,
    }


@pytest.mark.postgres
def test_the_first_observation_is_what_is_kept_and_a_second_pass_moves_nothing(live: tuple[str, str]):
    """#93 D2: `collected_at` is the first observation. V_LONG was seen twice an hour apart, and the
    later observation carries a different view_count -- neither of them may reach the row."""
    url, schema = live
    with connect(url) as conn:
        first = project.project(conn, youtube_schema=schema)
        again = project.project(conn, youtube_schema=schema)

    assert again.snapshot_id == first.snapshot_id
    assert (again.counts.videos, again.counts.comments) == (first.counts.videos, first.counts.comments)
    # The row counts a pass reads are the same; the rows it inserts the second time are none.
    assert (first.counts.video_rows, first.counts.comment_rows) == (3, 4)
    assert (again.counts.video_rows, again.counts.comment_rows) == (0, 0)

    stored = _rows(
        url,
        "SELECT collected_at, source_metadata ->> 'view_count' FROM corpus_document"
        " WHERE snapshot_id = %s AND source_item_id = 'V_LONG'",
        (first.snapshot_id,),
    )[0]
    assert stored == (FIRST, "12000")


@pytest.mark.postgres
def test_nothing_to_project_is_blocked_rather_than_an_empty_run(live: tuple[str, str]):
    """The collector has not been stood up yet: there is no run to call unsuccessful, and the message
    has to name what to run instead (the same convention as `cosmai trend quarter` with no snapshot)."""
    url, schema = live
    with connect(url) as conn, pytest.raises(project.NoLivePopulation) as blocked:
        project.project(conn, youtube_schema=schema, cutoff=FIRST - timedelta(days=1))
    assert "cosmai collect youtube" in str(blocked.value)
    assert _rows(url, "SELECT count(*) FROM corpus_snapshot")[0][0] == 1


@pytest.mark.postgres
def test_snapshot_one_is_never_written(live: tuple[str, str]):
    """The archive is read, never recomputed (#93 D0). Asked two ways: the refusal when it is named,
    and the archive's own rows byte for byte after a pass that was not."""
    url, schema = live
    before = _rows(url, ARCHIVE_DIGEST)[0][0]
    with connect(url) as conn, conn.cursor() as cur:
        cur.execute("UPDATE corpus_snapshot SET lineage = 'archive' WHERE snapshot_id = 1")
        conn.commit()
    with connect(url) as conn:
        with pytest.raises(project.ArchiveSnapshotRefused):
            project.project(conn, youtube_schema=schema, snapshot_id=corpus.SNAPSHOT_ID)
        outcome = project.project(conn, youtube_schema=schema)
    assert outcome.snapshot_id != corpus.SNAPSHOT_ID
    assert _rows(url, ARCHIVE_DIGEST)[0][0] == before
    assert _rows(url, "SELECT count(*) FROM corpus_document WHERE snapshot_id = 1")[0][0] == 12


@pytest.mark.postgres
@pytest.mark.parametrize("arm", ["repr_boolean", "jsonb_null", "always_null_key"])
def test_the_archive_and_the_live_rows_answer_the_same_three_questions(live: tuple[str, str], arm: str):
    """The three ways a rebuilt `source_metadata` diverges, asked of the stored rows of both lineages
    at once. Each of them returns rows, raises nothing and changes a measurement."""
    url, schema = live
    with connect(url) as conn:
        outcome = project.project(conn, youtube_schema=schema)
    both = (corpus.SNAPSHOT_ID, outcome.snapshot_id)

    if arm == "repr_boolean":
        # A JSON `true` here reads back as 'true' and `declared == DECLARED` is false for every row.
        types = _rows(
            url,
            "SELECT DISTINCT jsonb_typeof(source_metadata -> 'has_paid_product_placement'),"
            " source_metadata ->> 'has_paid_product_placement'"
            " FROM corpus_document WHERE snapshot_id = ANY(%s) AND source = 'youtube_video'",
            (list(both),),
        )
        assert {t for t, _ in types} == {"string"}
        assert {v for _, v in types} <= {DECLARED, "False"}
        replies = _rows(
            url,
            "SELECT DISTINCT jsonb_typeof(source_metadata -> 'is_reply'),"
            " source_metadata ->> 'is_reply' FROM corpus_document"
            " WHERE snapshot_id = ANY(%s) AND source = 'youtube_comment'",
            (list(both),),
        )
        assert {t for t, _ in replies} == {"string"}
        assert {v for _, v in replies} <= {DECLARED, "False"}
    elif arm == "jsonb_null":
        # V_UNKNOWN's duration and C2's like count are the load-bearing nulls: the first is exactly
        # what makes that row video_unknown.
        nulls = _rows(
            url,
            "SELECT source_item_id, jsonb_typeof(source_metadata -> 'duration_seconds')"
            " FROM corpus_document WHERE snapshot_id = %s AND source_item_id = 'V_UNKNOWN'",
            (outcome.snapshot_id,),
        )
        assert nulls == [("V_UNKNOWN", "null")]
        assert _rows(
            url,
            "SELECT jsonb_typeof(source_metadata -> 'like_count') FROM corpus_document"
            " WHERE snapshot_id = %s AND source_item_id = 'C2'",
            (outcome.snapshot_id,),
        ) == [("null",)]
        # The string 'None' is what a Python repr of a missing value looks like, and it is never right.
        assert _rows(
            url,
            "SELECT count(*) FROM corpus_document WHERE snapshot_id = ANY(%s)"
            " AND position('None' in source_metadata::text) > 0",
            (list(both),),
        ) == [(0,)]
    else:
        missing = _rows(
            url,
            "SELECT count(*) FROM corpus_document WHERE snapshot_id = ANY(%s)"
            " AND source = 'youtube_comment' AND NOT source_metadata ? 'parent_comment_id'",
            (list(both),),
        )
        assert missing == [(0,)]
        assert _rows(
            url,
            "SELECT DISTINCT jsonb_typeof(source_metadata -> 'parent_comment_id') FROM corpus_document"
            " WHERE snapshot_id = ANY(%s) AND source = 'youtube_comment'",
            (list(both),),
        ) == [("null",)]


@pytest.mark.postgres
def test_no_raw_author_identifier_reaches_the_corpus(live: tuple[str, str]):
    """`tubedepth.comments` still carries the display name and the raw channel id (the fix belongs at
    the collector); neither may cross into a corpus document, and the hash has to be the one #92
    named -- including for a value that arrives already hashed."""
    url, schema = live
    with connect(url) as conn:
        outcome = project.project(conn, youtube_schema=schema)

    stored = dict(
        _rows(
            url,
            "SELECT source_item_id, source_metadata FROM corpus_document"
            " WHERE snapshot_id = %s AND source = 'youtube_comment'",
            (outcome.snapshot_id,),
        )
    )
    assert set(stored) == {"C1", "C2", "C3", "C4"}
    for metadata in stored.values():
        assert tuple(sorted(metadata)) == tuple(sorted(contract.COMMENT_METADATA_KEYS))
        assert metadata["author_channel_hash"] == author_hash(RAW_AUTHOR)
    assert _rows(
        url,
        "SELECT count(*) FROM corpus_document WHERE snapshot_id = %s"
        " AND position('Some Person' in source_metadata::text) > 0",
        (outcome.snapshot_id,),
    ) == [(0,)]
    # Empty means true, and it is the same view the run itself asks before it closes.
    assert outcome.violations == []
    assert (
        _rows(
            url,
            "SELECT violation, row_key FROM author_identifier_violation"
            " WHERE relation = 'needs.corpus_document'",
        )
        == []
    )


@pytest.mark.postgres
def test_a_comment_carries_its_parents_channel_and_the_flags_the_rules_name(live: tuple[str, str]):
    url, schema = live
    with connect(url) as conn:
        outcome = project.project(conn, youtube_schema=schema)
    rows = _rows(
        url,
        "SELECT source_item_id, parent_item_id, channel_id, quality_flags, url FROM corpus_document"
        " WHERE snapshot_id = %s AND source = 'youtube_comment' ORDER BY 1",
        (outcome.snapshot_id,),
    )
    assert [(r[0], r[3]) for r in rows] == [
        ("C1", project.CLEAN),
        ("C2", project.DUPLICATE_IN_PARENT),
        ("C3", project.EMPTY_TEXT),
        ("C4", project.CLEAN),
    ]
    # Rule 3: the quarter rides the parent video, so the comment carries the parent's channel.
    assert {r[1] for r in rows} == {"V_LONG"}
    assert {r[2] for r in rows} == {PANEL_CHANNEL}
    assert {r[4] for r in rows} == {f"{project.WATCH_URL}V_LONG"}


@pytest.mark.postgres
def test_the_run_records_the_snapshot_and_the_cutoff_it_read(live: tuple[str, str]):
    """`contracts/versioning.md`: `analysis_run.versions` carries `snapshot` and `cutoff`, and the same
    cutoff gives the same answer -- which is what replaces a frozen copy (#93 D2)."""
    url, schema = live
    edge = FIRST + timedelta(minutes=30)
    with connect(url) as conn:
        early = project.project(conn, youtube_schema=schema, cutoff=edge)
        # C2 was first seen an hour later than the rest, so the cutoff is the only thing keeping it out.
        assert early.counts.comments == 3
        assert project.project(conn, youtube_schema=schema, cutoff=edge).counts.comments == 3
        full = project.project(conn, youtube_schema=schema)
    assert full.counts.comments == 4
    assert full.run_id == early.run_id  # found by note and reopened, never piled up
    run = _rows(url, "SELECT status, note, versions FROM analysis_run WHERE run_id = %s", (full.run_id,))[0]
    assert run[0] == "ok"
    assert run[1] == project.run_note_of(full.snapshot_id)
    assert run[2]["snapshot"] == full.snapshot_id
    assert run[2]["cutoff"] == full.cutoff.isoformat()
