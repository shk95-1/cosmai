"""`needs.collection_lineage`: 원문 한 줄에서 그것을 걷은 수집분과 요청 근거까지 (#144 경로 6a·6b·7).

리뷰 갈래의 마지막 한 칸은 **손실 지점**이다. `trend_radar.review` 에는 `run_id` 가 없고 그 표는
archive 된 남의 것이라 upstream 이 넣을 수 없다 — 이어지는 것은 `captured_at`(run 의 시간 버킷,
`collectors/commerce/models.py`) 뿐이고, 같은 버킷에 두 번 시도한 run 은 두 행이다
(`collectors/commerce/storage/db.py` 의 RunLog). 그래서 이 뷰는 **후보를 후보 그대로** 낸다:
단일 확정 · 후보 여럿 · 미상 셋이 `match` 로 갈리고, 화면이 그것을 그대로 보인다(사용자 결정
2026-08-27). 하나로 찍거나 숨기는 것이 더 나쁘다.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW = REPO_ROOT / "db" / "views" / "collection_lineage.sql"

BUCKET = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)  # run 의 시간 버킷 = review.captured_at
OTHER_BUCKET = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
LONELY = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)  # run 행이 하나도 없는 버킷 (glowpick 08-20~26 자리)

RUN_ONE = UUID("11111111-1111-4111-8111-111111111111")  # 다른 버킷의 유일한 run
RUN_A = UUID("22222222-2222-4222-8222-222222222222")  # glowpick, BUCKET 첫 시도
RUN_B = UUID("33333333-3333-4333-8333-333333333333")  # glowpick, BUCKET 재시도
RUN_C = UUID("44444444-4444-4444-8444-444444444444")  # BUCKET 이지만 sources 에 glowpick 이 없다

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
                (RUN_C, BUCKET, BUCKET, BUCKET + timedelta(minutes=2), "ok", "oliveyoung", "rank", None),
            ],
        )
        # 경로 7: 그 run 이 실제로 무엇을 요청했나. 2xx 만 ok 로 센다(collector_health 와 같은 선).
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
        # 같은 영상의 판이 여럿이다(실측 3,378/3,922) — 갈라 주는 것은 fetched_at 뿐이다.
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
                # 다른 kind 는 이 댓글을 걷은 판이 아니다 -- 세면 후보가 셋이 된다.
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
    # 실측 56%: captured_at 버킷에 그 사이트를 걷은 run 이 정확히 하나다.
    [row] = _for(rows, "review", "glowpick", "r:single")
    assert (row["match"], row["candidate_count"], row["candidate_rank"]) == ("single", 1, 1)
    assert row["collection_kind"] == "commerce_run"
    assert row["collection_id"] == str(RUN_ONE)
    assert row["doc_parent"] == "g:1"


def test_two_attempts_in_the_same_bucket_stay_two_candidates(rows: list[dict[str, Any]]):
    # 실측 34%: 같은 시간 버킷에 두 번 시도한 run 은 두 행이고, review 는 어느 쪽이 자기를 걷었는지
    # 말하지 못한다. 하나로 찍으면 화면이 없는 사실을 주장한다(사용자 결정).
    found = _for(rows, "review", "glowpick", "r:many")
    assert [r["match"] for r in found] == ["candidate", "candidate"]
    assert [r["candidate_count"] for r in found] == [2, 2]
    assert [r["candidate_rank"] for r in found] == [1, 2]
    assert [r["collection_id"] for r in found] == [str(RUN_A), str(RUN_B)]


def test_a_run_that_did_not_collect_that_site_is_not_a_candidate(rows: list[dict[str, Any]]):
    # RUN_C 는 같은 버킷이지만 sources 에 glowpick 이 없다. 버킷만 보고 세면 후보가 셋이 된다.
    assert all(r["collection_id"] != str(RUN_C) for r in _for(rows, "review", "glowpick", "r:many"))


def test_no_candidate_still_yields_one_row_that_says_unknown(rows: list[dict[str, Any]]):
    # 실측 10%: 행을 없애면 "수집분에 못 닿았다" 와 "그 리뷰가 없다" 가 화면에서 같아 보인다.
    [row] = _for(rows, "review", "glowpick", "r:none")
    assert (row["match"], row["candidate_count"]) == ("unknown", 0)
    assert row["collection_id"] is None
    assert row["candidate_rank"] == 1


def test_the_same_review_key_on_another_site_is_another_document(rows: list[dict[str, Any]]):
    # review 의 PK 는 (source, review_key) 다 — site 를 안 걸면 두 사이트가 한 문서로 뭉친다.
    assert len(_for(rows, "review", "oliveyoung", "r:single")) == 1


def test_the_request_evidence_comes_from_that_runs_own_source(rows: list[dict[str, Any]]):
    # 경로 7. RUN_A 는 glowpick 2건(200·403) + oliveyoung 1건이다. 소스를 안 좁히면 3 이 된다.
    first = _for(rows, "review", "glowpick", "r:many")[0]
    assert (first["requests"], first["ok"]) == (2, 1)
    assert first["sample_url"].startswith("https://glowpick/")
    # fetch_log 가 한 줄도 없는 run 은 0 이다 -- 그 run 이 이 사이트에 요청한 기록이 없다는 뜻이고,
    # 후보에서 빼는 근거는 되지 못한다(요청 로그와 수집 run 은 다른 표다).
    second = _for(rows, "review", "glowpick", "r:many")[1]
    assert (second["requests"], second["ok"]) == (0, 0)


def test_a_comment_reaches_the_artifact_that_carried_it(rows: list[dict[str, Any]]):
    # 실측 3,922/3,922. 같은 영상의 판이 여럿이면 first_seen_at 에 가까운 판이 1순위다.
    found = _for(rows, "yt_comment", "youtube", "c-1")
    assert [r["match"] for r in found] == ["candidate", "candidate"]
    assert found[0]["collection_id"] == ART_NEW
    assert found[0]["collection_kind"] == "youtube_artifact"
    assert found[0]["bytes"] == 200
    assert found[1]["collection_id"] == ART_OLD
    # video.metadata 는 댓글을 걷은 판이 아니다.
    assert all(r["collection_id"] != ART_META for r in found)


def test_the_comment_artifact_carries_its_job(rows: list[dict[str, Any]]):
    # 경로 6b 의 요청 근거는 tubedepth.jobs 다 — 그 판을 만든 일감의 상태와 시각.
    found = _for(rows, "yt_comment", "youtube", "c-1")[0]
    assert found["status"] == "succeeded"
    assert found["finished_at"] == FIRST_SEEN - timedelta(minutes=30)
    assert found["scope_note"] == "video.comments"


def test_a_video_with_no_artifact_is_unknown_too(rows: list[dict[str, Any]]):
    [row] = _for(rows, "yt_comment", "youtube", "c-9")
    assert (row["match"], row["candidate_count"]) == ("unknown", 0)


# --- 배포 경로 ---


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
