"""`needs.collection_lineage`: from one original-text row to the collection that gathered it and the
fetch it rests on (#144 paths 6a, 6b, 7).

The review branch's last hop is a **point of loss**. `trend_radar.review` carries no `run_id`, and that
table is someone else's archived one so upstream cannot add it -- only three things link it:
`captured_at` (a run's time bucket, `collectors/commerce/models.py`), `run.sources` and `run.datasets`,
and a run that tried the same bucket twice is two rows (RunLog in
`collectors/commerce/storage/db.py`). So this view reports **candidates as candidates**: single
confirmed, several candidates, or unknown split into `match`, and the screen shows exactly that (user
decision 2026-08-27). Collapsing it to one value or hiding it is worse.

That all three predicates are needed is half of what this file is about. Drop `datasets` and the hourly
`ranking` run fills every bucket and becomes **a candidate for every review**, making unknown
unreachable and breaking user decision 2 (measured in production over 30,043 reviews: sources alone
9,327/20,716/0 -- with datasets 18,100/9,660/2,283).
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text

import collectors.commerce.sources  # noqa: F401  -- registration is an import side effect
from collectors.commerce.registry import SOURCES

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW = REPO_ROOT / "db" / "views" / "collection_lineage.sql"

BUCKET = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)  # a run's time bucket = review.captured_at
OTHER_BUCKET = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
OY_BUCKET = datetime(2026, 8, 22, 3, 0, tzinfo=UTC)  # a bucket only oliveyoung ran -- the gated side
LOW_BUCKET = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)  # a bucket only the review_low walk ran
MULTI_BUCKET = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)  # the bucket of a run carrying two datasets
GP_RANK_BUCKET = datetime(2026, 8, 25, 3, 0, tzinfo=UTC)  # a bucket glowpick only ran as a ranking run
LONELY = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)  # not a single run row (glowpick's 08-20~26 gap)

RUN_ONE = UUID("11111111-1111-4111-8111-111111111111")  # the only run of a different bucket
RUN_A = UUID("22222222-2222-4222-8222-222222222222")  # glowpick, BUCKET's first attempt
RUN_B = UUID("33333333-3333-4333-8333-333333333333")  # glowpick, BUCKET's retry
# Three sharing the same bucket and **the same source**, differing only in dataset. oliveyoung's
# parse() gates by dataset (oliveyoung.py:225-227), so its ranking/review_stats runs never write a
# review body.
RUN_OY_RANK = UUID("44444444-4444-4444-8444-444444444444")  # oliveyoung, OY_BUCKET, hourly ranking
RUN_OY_STATS = UUID("55555555-5555-4555-8555-555555555555")  # oliveyoung, OY_BUCKET, review_stats
RUN_OY_REVIEW = UUID("99999999-9999-4999-8999-999999999999")  # oliveyoung, OY_BUCKET, review
RUN_OTHER_SITE = UUID("66666666-6666-4666-8666-666666666666")  # oliveyoung, BUCKET, review
RUN_LOW = UUID("77777777-7777-4777-8777-777777777777")  # oliveyoung, LOW_BUCKET, review_low
RUN_MULTI = UUID("88888888-8888-4888-8888-888888888888")  # glowpick, MULTI_BUCKET, 'ranking, review'
# glowpick has **no** gate: parse() calls _reviews(...) unconditionally without looking at
# payload.fetch.dataset (glowpick.py:108, 135, and the comment at :64-66 writes the reason -- ranking
# and review are the same category page). So this run is a legitimate candidate for a review.
RUN_GP_RANK = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")  # glowpick, GP_RANK_BUCKET, ranking

ART_OLD = "a" * 32
ART_NEW = "b" * 32
ART_META = "c" * 32
JOB_ID = "j" * 32

FIRST_SEEN = datetime(2026, 8, 22, 1, 0, tzinfo=UTC)


def _seed_and_create_view(url: str, schema: str, td_schema: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".run (id, captured_at, started_at, finished_at, status, sources,'
            " datasets, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    RUN_ONE,
                    OTHER_BUCKET,
                    OTHER_BUCKET,
                    OTHER_BUCKET + timedelta(minutes=5),
                    "ok",
                    "glowpick,oliveyoung",
                    "review",
                    None,
                ),
                (
                    RUN_A,
                    BUCKET,
                    BUCKET,
                    BUCKET + timedelta(minutes=4),
                    "partial",
                    "glowpick,oliveyoung",
                    "review",
                    "first try",
                ),
                (
                    RUN_B,
                    BUCKET,
                    BUCKET + timedelta(minutes=10),
                    BUCKET + timedelta(minutes=14),
                    "ok",
                    "glowpick",
                    "review",
                    "retry",
                ),
                # An hourly ranking. Same bucket, same source, so the sources predicate never filters
                # it out, and oliveyoung's parse() gates by dataset so this never writes a single
                # review body.
                (
                    RUN_OY_RANK,
                    OY_BUCKET,
                    OY_BUCKET,
                    OY_BUCKET + timedelta(minutes=2),
                    "ok",
                    "oliveyoung",
                    "ranking",
                    None,
                ),
                # review_stats only follows _stats_fetch/_summary_fetch (_parse_ranking in
                # oliveyoung.py) -- strpos matching on 'review' would pull this in as a candidate too.
                (
                    RUN_OY_STATS,
                    OY_BUCKET,
                    OY_BUCKET,
                    OY_BUCKET + timedelta(minutes=3),
                    "ok",
                    "oliveyoung",
                    "review_stats",
                    None,
                ),
                (
                    RUN_OY_REVIEW,
                    OY_BUCKET,
                    OY_BUCKET + timedelta(minutes=5),
                    OY_BUCKET + timedelta(minutes=12),
                    "ok",
                    "oliveyoung",
                    "review",
                    None,
                ),
                # The dataset matches but the source differs -- the sources predicate is still needed
                # even after the list becomes per-site.
                (
                    RUN_OTHER_SITE,
                    BUCKET,
                    BUCKET,
                    BUCKET + timedelta(minutes=6),
                    "ok",
                    "oliveyoung",
                    "review",
                    None,
                ),
                # Only oliveyoung declares review_low (oliveyoung.py:128-130) -- the same record type
                # walked by a different pass (the Dataset docstring in models.py).
                (
                    RUN_LOW,
                    LOW_BUCKET,
                    LOW_BUCKET,
                    LOW_BUCKET + timedelta(minutes=8),
                    "ok",
                    "oliveyoung",
                    "review_low",
                    None,
                ),
                # A run carrying several datasets, plus whitespace. The column's format is
                # ",".join(...), so IN cannot catch it.
                (
                    RUN_MULTI,
                    MULTI_BUCKET,
                    MULTI_BUCKET,
                    MULTI_BUCKET + timedelta(minutes=9),
                    "ok",
                    " glowpick , oliveyoung ",
                    " ranking , review ",
                    None,
                ),
                # glowpick's hourly ranking run. With no gate, this is the run that writes the review
                # body -- in production this is usually the first writer of trend_radar.review
                # (DO NOTHING upsert).
                (
                    RUN_GP_RANK,
                    GP_RANK_BUCKET,
                    GP_RANK_BUCKET,
                    GP_RANK_BUCKET + timedelta(minutes=7),
                    "ok",
                    "glowpick",
                    "ranking",
                    None,
                ),
            ],
        )
        # Path 7: what that run actually requested. Only 2xx counts as ok (the same line as
        # collector_health).
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".fetch_log (run_id, at, source, dataset, url, status, attempt)'
            " VALUES (%s, %s, %s, 'review', %s, %s, 1)",
            [
                (RUN_A, BUCKET, "glowpick", "https://glowpick/a", 200),
                (RUN_A, BUCKET, "glowpick", "https://glowpick/b", 403),
                (RUN_A, BUCKET, "oliveyoung", "https://oliveyoung/z", 200),
            ],
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{schema}".review (source, review_key, captured_at, product_key, rating, body)'
            " VALUES (%s, %s, %s, %s, %s, %s)",
            [
                ("glowpick", "r:single", OTHER_BUCKET, "g:1", 3.0, "한 run 만 맞는 리뷰"),
                ("glowpick", "r:many", BUCKET, "g:1", 3.0, "후보가 둘인 리뷰"),
                ("glowpick", "r:none", LONELY, "g:1", 3.0, "run 행이 없는 리뷰"),
                ("glowpick", "r:multi", MULTI_BUCKET, "g:1", 3.0, "dataset 둘을 담은 run 의 리뷰"),
                ("glowpick", "r:byrank", GP_RANK_BUCKET, "g:1", 3.0, "ranking 런이 걷은 glowpick 리뷰"),
                ("oliveyoung", "r:oy", OY_BUCKET, "o:1", 3.0, "게이트가 있는 사이트의 리뷰"),
                ("oliveyoung", "r:low", LOW_BUCKET, "o:1", 1.0, "review_low 걸음이 걷은 리뷰"),
                ("oliveyoung", "r:single", OTHER_BUCKET, "o:1", 3.0, "다른 사이트의 같은 키"),
            ],
        )
        conn.exec_driver_sql(
            f'GRANT SELECT ON "{schema}".review, "{schema}".run, "{schema}".fetch_log TO needs_owner'
        )
        conn.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{td_schema}" TO needs_owner')
        conn.exec_driver_sql(
            f'GRANT SELECT ON "{td_schema}".comments, "{td_schema}".artifacts, "{td_schema}".jobs'
            " TO needs_owner"
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{td_schema}".comments (video_id, comment_id, text, like_count,'
            " is_hearted_by_uploader, is_pinned, published_at, first_seen_at, last_seen_at)"
            " VALUES (%s, %s, 'c', 0, false, false, %s, %s, %s)",
            [
                ("v-1", "c-1", FIRST_SEEN, FIRST_SEEN, FIRST_SEEN),
                ("v-none", "c-9", FIRST_SEEN, FIRST_SEEN, FIRST_SEEN),
            ],
        )
        # A video can have several artifacts (measured 3,378/3,922) -- fetched_at is the only thing
        # that tells them apart.
        conn.exec_driver_sql(
            f'INSERT INTO "{td_schema}".artifacts (identifier, kind, target, fingerprint, digest,'
            " byte_count, fetched_at, fresh_until) VALUES (%s, %s, %s, 'fp', 'dg', %s, %s, %s)",
            [
                (
                    ART_OLD,
                    "video.comments",
                    "v-1",
                    100,
                    FIRST_SEEN - timedelta(days=3),
                    FIRST_SEEN + timedelta(days=1),
                ),
                (
                    ART_NEW,
                    "video.comments",
                    "v-1",
                    200,
                    FIRST_SEEN - timedelta(minutes=30),
                    FIRST_SEEN + timedelta(days=7),
                ),
                # A different kind is not the artifact that collected this comment -- counting it would
                # make three candidates.
                (ART_META, "video.metadata", "v-1", 10, FIRST_SEEN, FIRST_SEEN),
            ],
        )
        conn.exec_driver_sql(
            f'INSERT INTO "{td_schema}".jobs (identifier, kind, target, state, attempt_count,'
            " max_attempts, scheduled_at, created_at, started_at, finished_at, webhook_attempts)"
            " VALUES (%s, 'video.comments', 'v-1', 'succeeded', 1, 3, %s, %s, %s, %s, 0)",
            (
                JOB_ID,
                FIRST_SEEN - timedelta(hours=1),
                FIRST_SEEN - timedelta(hours=1),
                FIRST_SEEN - timedelta(minutes=40),
                FIRST_SEEN - timedelta(minutes=30),
            ),
        )
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(
            VIEW.read_text(encoding="utf-8")
            .replace("needs.", f'"{schema}".')
            .replace("trend_radar.", f'"{schema}".')
            .replace("tubedepth.", f'"{td_schema}".')
        )
    engine.dispose()


@pytest.fixture
def rows(
    needs_schema: str,
    trend_radar_schema: str,
    tubedepth_side_schema: str,
    needs_runtime_url: str,
    _schema_name: str,
) -> list[dict[str, Any]]:
    _seed_and_create_view(needs_schema, _schema_name, tubedepth_side_schema)
    engine = create_engine(needs_runtime_url)
    with engine.connect() as conn:
        found = (
            conn.execute(text("SELECT * FROM collection_lineage ORDER BY src, site, doc_key, candidate_rank"))
            .mappings()
            .all()
        )
    engine.dispose()
    return [dict(r) for r in found]


def _for(rows: list[dict[str, Any]], src: str, site: str, key: str) -> list[dict[str, Any]]:
    return [r for r in rows if (r["src"], r["site"], r["doc_key"]) == (src, site, key)]


def test_one_candidate_reads_as_a_confirmed_single(rows: list[dict[str, Any]]):
    # Measured at 56%: exactly one run collected that site in the captured_at bucket.
    [row] = _for(rows, "review", "glowpick", "r:single")
    assert (row["match"], row["candidate_count"], row["candidate_rank"]) == ("single", 1, 1)
    assert row["collection_kind"] == "commerce_run"
    assert row["collection_id"] == str(RUN_ONE)
    assert row["doc_parent"] == "g:1"


def test_two_attempts_in_the_same_bucket_stay_two_candidates(rows: list[dict[str, Any]]):
    # Measured at 34%: a run tried twice in the same time bucket is two rows, and a review cannot say
    # which one collected it. Collapsing it to one would have the screen assert a fact it doesn't have
    # (user decision).
    found = _for(rows, "review", "glowpick", "r:many")
    assert [r["match"] for r in found] == ["candidate", "candidate"]
    assert [r["candidate_count"] for r in found] == [2, 2]
    assert [r["candidate_rank"] for r in found] == [1, 2]
    assert [r["collection_id"] for r in found] == [str(RUN_A), str(RUN_B)]


def test_a_run_that_did_not_collect_that_site_is_not_a_candidate(rows: list[dict[str, Any]]):
    # RUN_OTHER_SITE is a review run in the same bucket, but it belongs to oliveyoung. Even after the
    # list becomes per-site, without the sources predicate it would become a candidate for a glowpick
    # review.
    found = {r["collection_id"] for r in _for(rows, "review", "glowpick", "r:many")}
    assert str(RUN_OTHER_SITE) not in found
    assert found == {str(RUN_A), str(RUN_B)}


def test_a_gated_source_does_not_get_its_ranking_run_as_a_candidate(rows: list[dict[str, Any]]):
    """Drop the `datasets` predicate and the hourly ranking run becomes a candidate for every review of
    that site.

    Same bucket, **same source**, so the sources predicate filters out nothing -- in production 22,673
    of 64,648 candidate pairs (a run that never collected a single review) fell into this category.
    oliveyoung's parse() gates by dataset (oliveyoung.py:225-227), so only a review run is a candidate
    on that site.
    """
    found = {r["collection_id"] for r in _for(rows, "review", "oliveyoung", "r:oy")}
    assert str(RUN_OY_RANK) not in found, "oliveyoung 의 ranking run 이 리뷰의 후보가 됐다"
    # Matching strpos(datasets, 'review') would also let review_stats in. That walk only follows
    # _stats_fetch/_summary_fetch and never writes a single line into trend_radar.review.
    assert str(RUN_OY_STATS) not in found, "review_stats run 이 리뷰의 후보가 됐다"
    assert found == {str(RUN_OY_REVIEW)}


def test_an_ungated_source_does_get_its_ranking_run_as_a_candidate(rows: list[dict[str, Any]]):
    """glowpick 은 게이트가 없다 -- ranking 런이 리뷰 본문을 쓴다.

    `parse()` 가 `payload.fetch.dataset` 을 보지 않고 조건 없이 `_reviews(...)` 를 부르고
    (glowpick.py:108·135), :64-66 주석이 그 이유를 적는다 -- ranking 과 review 가 같은 카테고리
    페이지다. 크론이 ranking 매시 / review 하루 한 번이고 review 는 DO NOTHING upsert 라 운영에서
    **첫 기록자가 대개 hourly ranking 런**이다: dataset 을 사이트와 무관하게 {review, review_low}
    로 좁히면 glowpick 리뷰 3,597건 중 2,284건(63.5퍼센트)이 조용히 '미상' 으로 오분류된다.
    """
    [row] = _for(rows, "review", "glowpick", "r:byrank")
    assert (row["match"], row["collection_id"]) == ("single", str(RUN_GP_RANK))


def test_review_low_is_the_same_bodies_by_another_walk(rows: list[dict[str, Any]]):
    # models.py 의 Dataset docstring: REVIEW_LOW 는 REVIEW 와 같은 레코드 타입이다. 목록에서
    # 빼면 저평점 전수 걸음이 걷은 리뷰가 통째로 '미상' 이 된다. 선언한 사이트는 oliveyoung 뿐이다.
    [row] = _for(rows, "review", "oliveyoung", "r:low")
    assert (row["match"], row["collection_id"]) == ("single", str(RUN_LOW))


def test_a_run_carrying_two_datasets_still_counts(rows: list[dict[str, Any]]):
    # The column's format is ",".join(...) (RunLog.start). Writing it as IN would quietly drop this
    # run, and a list with whitespace mixed in dies the same way.
    [row] = _for(rows, "review", "glowpick", "r:multi")
    assert (row["match"], row["collection_id"]) == ("single", str(RUN_MULTI))


def test_no_candidate_still_yields_one_row_that_says_unknown(rows: list[dict[str, Any]]):
    # Measured at 10%: dropping the row would make "the collection never reached it" and "that review
    # doesn't exist" look the same on screen.
    [row] = _for(rows, "review", "glowpick", "r:none")
    assert (row["match"], row["candidate_count"]) == ("unknown", 0)
    assert row["collection_id"] is None
    assert row["candidate_rank"] == 1


def test_the_same_review_key_on_another_site_is_another_document(rows: list[dict[str, Any]]):
    # review's PK is (source, review_key) -- without filtering by site, two sites merge into one
    # document.
    assert len(_for(rows, "review", "oliveyoung", "r:single")) == 1


def test_the_request_evidence_comes_from_that_runs_own_source(rows: list[dict[str, Any]]):
    # Path 7. RUN_A is 2 glowpick requests (200, 403) + 1 oliveyoung request. Without narrowing by
    # source it would be 3.
    first = _for(rows, "review", "glowpick", "r:many")[0]
    assert (first["requests"], first["ok"]) == (2, 1)
    assert first["sample_url"].startswith("https://glowpick/")
    # A run with not a single fetch_log line is 0 -- meaning that run has no record of requesting this
    # site, which is not grounds to exclude it as a candidate (the request log and the collection run
    # are different tables).
    second = _for(rows, "review", "glowpick", "r:many")[1]
    assert (second["requests"], second["ok"]) == (0, 0)


def test_a_comment_reaches_the_artifact_that_carried_it(rows: list[dict[str, Any]]):
    # Measured at 3,922/3,922. With several artifacts of the same video, the one closest to
    # first_seen_at is first priority.
    found = _for(rows, "yt_comment", "youtube", "c-1")
    assert [r["match"] for r in found] == ["candidate", "candidate"]
    assert found[0]["collection_id"] == ART_NEW
    assert found[0]["collection_kind"] == "youtube_artifact"
    assert found[0]["bytes"] == 200
    assert found[1]["collection_id"] == ART_OLD
    # video.metadata is never the artifact that collected a comment.
    assert all(r["collection_id"] != ART_META for r in found)


def test_the_comment_artifact_carries_its_job(rows: list[dict[str, Any]]):
    # Path 6b's request evidence is tubedepth.jobs -- the state and time of the job that made that
    # artifact.
    found = _for(rows, "yt_comment", "youtube", "c-1")[0]
    assert found["status"] == "succeeded"
    assert found["finished_at"] == FIRST_SEEN - timedelta(minutes=30)
    assert found["scope_note"] == "video.comments"


def test_a_video_with_no_artifact_is_unknown_too(rows: list[dict[str, Any]]):
    [row] = _for(rows, "yt_comment", "youtube", "c-9")
    assert (row["match"], row["candidate_count"]) == ("unknown", 0)


# --- Does the view's list mirror the collector's declaration ---

# This only reads the `review_body_dataset (site, dataset) AS (VALUES ...)` block inside the view --
# mixing in another VALUES in the file, or an example inside a comment, would blur what this test
# actually measures.
_PAIR_BLOCK = re.compile(r"WITH review_body_dataset \(site, dataset\) AS \((.*?)\n\),", re.DOTALL)
_PAIR = re.compile(r"\('([a-z_]+)',\s*'([a-z_]+)'\)")


def _pairs_in_the_view() -> set[tuple[str, str]]:
    block = _PAIR_BLOCK.search(VIEW.read_text(encoding="utf-8"))
    assert block, "뷰에서 review_body_dataset VALUES 블록을 찾지 못했다 -- 이름이나 모양이 바뀌었다"
    return set(_PAIR.findall(block.group(1)))


def test_the_view_mirrors_the_collectors_declaration():
    """Writing a site list into SQL by hand means no one cries the next time a site is added -- this is
    that place.

    The source of truth is each source's own `review_body_datasets`, and whether that declaration
    disagrees with `parse()` is measured separately by
    tests/collectors/commerce/test_review_body_datasets.py, replaying recorded fixtures. This test only
    looks between that declaration and the view.
    """
    declared = {(cls.key, dataset.value) for cls in SOURCES.values() for dataset in cls.review_body_datasets}
    assert _pairs_in_the_view() == declared


def test_a_source_that_writes_no_review_bodies_is_absent_rather_than_empty():
    # hwahae only collects ranking. Writing an empty line would give the SQL a row that means "this
    # site never writes under any dataset", and that row filters nothing while only growing the list.
    sites = {site for site, _ in _pairs_in_the_view()}
    assert "hwahae" not in sites
    assert not SOURCES["hwahae"].review_body_datasets


# --- Deploy path ---


@pytest.fixture
def deployed() -> Any:
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET ROLE needs_owner"))
        yield conn
    engine.dispose()


def test_the_deploy_leaves_the_view_readable_by_the_screen(deployed: Any):
    assert deployed.execute(text("SELECT to_regclass('needs.collection_lineage')")).scalar_one() is not None
    for role in ("needs_runtime", "postgrest_anon"):
        granted = deployed.execute(
            text("SELECT has_table_privilege(:r, 'needs.collection_lineage', 'SELECT')"), {"r": role}
        ).scalar_one()
        assert granted, role
