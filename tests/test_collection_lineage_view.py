"""`needs.collection_lineage`: 원문 한 줄에서 그것을 걷은 수집분과 요청 근거까지 (#144 경로 6a·6b·7).

리뷰 갈래의 마지막 한 칸은 **손실 지점**이다. `trend_radar.review` 에는 `run_id` 가 없고 그 표는
archive 된 남의 것이라 upstream 이 넣을 수 없다 — 이어지는 것은 `captured_at`(run 의 시간 버킷,
`collectors/commerce/models.py`) · `run.sources` · `run.datasets` 셋뿐이고, 같은 버킷에 두 번 시도한
run 은 두 행이다(`collectors/commerce/storage/db.py` 의 RunLog). 그래서 이 뷰는 **후보를 후보 그대로**
낸다: 단일 확정 · 후보 여럿 · 미상 셋이 `match` 로 갈리고, 화면이 그것을 그대로 보인다(사용자 결정
2026-08-27). 하나로 찍거나 숨기는 것이 더 나쁘다.

세 술어가 다 필요하다는 것이 이 파일의 절반이다. `datasets` 를 빼면 매시 도는 `ranking` run 이 모든
버킷을 채워 **모든 리뷰의 후보**가 되고, unknown 이 도달 불가가 되어 사용자 결정 2 가 무너진다
(운영 실측 리뷰 30,043건: sources 만 9,327/20,716/0 · +datasets 18,100/9,660/2,283).
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

import collectors.commerce.sources  # noqa: F401  -- 등록이 import 부작용이다
from collectors.commerce.registry import SOURCES

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
VIEW = REPO_ROOT / "db" / "views" / "collection_lineage.sql"

BUCKET = datetime(2026, 8, 20, 3, 0, tzinfo=UTC)  # run 의 시간 버킷 = review.captured_at
OTHER_BUCKET = datetime(2026, 8, 21, 3, 0, tzinfo=UTC)
OY_BUCKET = datetime(2026, 8, 22, 3, 0, tzinfo=UTC)  # oliveyoung 만 돈 버킷 -- 게이트가 있는 쪽
LOW_BUCKET = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)  # review_low 걸음만 돈 버킷
MULTI_BUCKET = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)  # dataset 을 둘 담은 run 의 버킷
GP_RANK_BUCKET = datetime(2026, 8, 25, 3, 0, tzinfo=UTC)  # glowpick 이 ranking 런으로만 돈 버킷
LONELY = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)  # run 행이 하나도 없는 버킷 (glowpick 08-20~26 자리)

RUN_ONE = UUID("11111111-1111-4111-8111-111111111111")  # 다른 버킷의 유일한 run
RUN_A = UUID("22222222-2222-4222-8222-222222222222")  # glowpick, BUCKET 첫 시도
RUN_B = UUID("33333333-3333-4333-8333-333333333333")  # glowpick, BUCKET 재시도
# 같은 버킷 · **같은 소스** 이면서 dataset 만 다른 셋. oliveyoung 은 parse() 가 dataset 으로
# 게이트하므로(oliveyoung.py:225-227) ranking·review_stats 런은 리뷰 본문을 쓰지 않는다.
RUN_OY_RANK = UUID("44444444-4444-4444-8444-444444444444")  # oliveyoung, OY_BUCKET, 매시 ranking
RUN_OY_STATS = UUID("55555555-5555-4555-8555-555555555555")  # oliveyoung, OY_BUCKET, review_stats
RUN_OY_REVIEW = UUID("99999999-9999-4999-8999-999999999999")  # oliveyoung, OY_BUCKET, review
RUN_OTHER_SITE = UUID("66666666-6666-4666-8666-666666666666")  # oliveyoung, BUCKET, review
RUN_LOW = UUID("77777777-7777-4777-8777-777777777777")  # oliveyoung, LOW_BUCKET, review_low
RUN_MULTI = UUID("88888888-8888-4888-8888-888888888888")  # glowpick, MULTI_BUCKET, 'ranking, review'
# glowpick 은 게이트가 **없다**: parse() 가 payload.fetch.dataset 을 보지 않고 조건 없이
# _reviews(...) 를 부른다(glowpick.py:108·135, 그리고 :64-66 주석이 이유를 적는다 -- ranking 과
# review 가 같은 카테고리 페이지다). 그래서 이 런은 리뷰의 정당한 후보다.
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
                # 매시 ranking. 같은 버킷 · 같은 소스라 sources 술어로는 안 걸러지고, oliveyoung 은
                # parse() 가 dataset 으로 게이트하므로 리뷰 본문을 한 줄도 쓰지 않는다.
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
                # review_stats 는 _stats_fetch·_summary_fetch 만 따라간다(oliveyoung.py 의
                # _parse_ranking) -- strpos 로 'review' 를 찾으면 이것까지 후보가 된다.
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
                # dataset 은 맞지만 소스가 다르다 -- 사이트별 목록이 된 뒤에도 sources 술어가 필요하다.
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
                # review_low 는 oliveyoung 만 선언한다(oliveyoung.py:128-130) -- 같은 레코드 타입을
                # 다른 걸음으로 걷는다(models.py 의 Dataset docstring).
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
                # 여러 dataset 을 담은 run + 공백. 컬럼의 형식이 ",".join(...) 이라 IN 으로는 못 잡는다.
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
                # glowpick 의 매시 ranking 런. 게이트가 없어 이 런이 리뷰 본문을 쓴다 -- 운영에서
                # trend_radar.review 의 첫 기록자가 대개 이쪽이다(DO NOTHING upsert).
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
    # RUN_OTHER_SITE 는 같은 버킷의 review 런이지만 oliveyoung 것이다. 사이트별 목록이 된 뒤에도
    # sources 술어가 없으면 그것이 glowpick 리뷰의 후보가 된다.
    found = {r["collection_id"] for r in _for(rows, "review", "glowpick", "r:many")}
    assert str(RUN_OTHER_SITE) not in found
    assert found == {str(RUN_A), str(RUN_B)}


def test_a_gated_source_does_not_get_its_ranking_run_as_a_candidate(rows: list[dict[str, Any]]):
    """`datasets` 술어가 빠지면 매시 도는 ranking run 이 그 사이트 모든 리뷰의 후보가 된다.

    같은 버킷 · **같은 소스**라 sources 술어로는 하나도 안 걸러진다 -- 운영에서 후보 짝 64,648 중
    22,673(리뷰를 한 줄도 안 걷은 run)이 이 부류였다. oliveyoung 은 parse() 가 dataset 으로
    게이트하므로(oliveyoung.py:225-227) 그 사이트에서는 review 런만 후보다.
    """
    found = {r["collection_id"] for r in _for(rows, "review", "oliveyoung", "r:oy")}
    assert str(RUN_OY_RANK) not in found, "oliveyoung 의 ranking run 이 리뷰의 후보가 됐다"
    # strpos(datasets, 'review') 로 찾으면 review_stats 까지 든다. 그 걸음은 _stats_fetch·
    # _summary_fetch 만 따라가고 trend_radar.review 에 한 줄도 쓰지 않는다.
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
    # 컬럼의 형식은 ",".join(...) 이다(RunLog.start). IN 으로 적으면 이 run 이 조용히 빠지고,
    # 공백이 섞인 목록도 같은 자리에서 죽는다.
    [row] = _for(rows, "review", "glowpick", "r:multi")
    assert (row["match"], row["collection_id"]) == ("single", str(RUN_MULTI))


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


# --- 뷰의 목록이 수집기의 선언을 비추는가 ---

# 뷰 안의 `review_body_dataset (site, dataset) AS (VALUES ...)` 블록만 읽는다 -- 파일의 다른 VALUES 나
# 주석 속 예시가 섞이면 이 테스트가 무엇을 재는지 흐려진다.
_PAIR_BLOCK = re.compile(r"WITH review_body_dataset \(site, dataset\) AS \((.*?)\n\),", re.DOTALL)
_PAIR = re.compile(r"\('([a-z_]+)',\s*'([a-z_]+)'\)")


def _pairs_in_the_view() -> set[tuple[str, str]]:
    block = _PAIR_BLOCK.search(VIEW.read_text(encoding="utf-8"))
    assert block, "뷰에서 review_body_dataset VALUES 블록을 찾지 못했다 -- 이름이나 모양이 바뀌었다"
    return set(_PAIR.findall(block.group(1)))


def test_the_view_mirrors_the_collectors_declaration():
    """SQL 에 사이트 목록을 손으로 적어 두면 다음에 사이트가 늘 때 아무도 안 운다 -- 여기가 그 자리다.

    정본은 각 소스의 `review_body_datasets` 이고, 그 선언이 `parse()` 와 갈리는지는
    tests/collectors/commerce/test_review_body_datasets.py 가 녹화 픽스처를 재생해서 따로 잰다.
    이 테스트는 그 선언과 뷰 사이만 본다.
    """
    declared = {(cls.key, dataset.value) for cls in SOURCES.values() for dataset in cls.review_body_datasets}
    assert _pairs_in_the_view() == declared


def test_a_source_that_writes_no_review_bodies_is_absent_rather_than_empty():
    # hwahae 는 랭킹만 걷는다. 빈 줄로 적어 두면 SQL 이 "이 사이트는 아무 dataset 으로도 안 쓴다" 를
    # 뜻하는 행을 갖게 되고, 그 행은 아무 것도 걸러 주지 않으면서 목록만 늘린다.
    sites = {site for site, _ in _pairs_in_the_view()}
    assert "hwahae" not in sites
    assert not SOURCES["hwahae"].review_body_datasets


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
