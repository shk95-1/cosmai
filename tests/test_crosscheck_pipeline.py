"""대조의 읽기: 그 run 을 찾고, 네 소스를 훑고, 아무것도 쓰지 않는다 (포크 #7).

민감도(#41)와 같은 자리다 -- 답이 표가 아니라 stdout 이라, 이 파일이 지는 것은 값이 아니라 **모양**과
**막힘·종료 코드**, 그리고 "정말 아무것도 안 썼는가" 다. 값은 규칙 테스트 둘이 진다.

커머스 원천은 소유 롤이 넣고 `needs_runtime` 은 SELECT 로만 읽는다 (`tests/test_aggregate_run.py` 와
같은 방식이고 같은 이유다 -- 운영에서 `trend_radar` 는 collectors/commerce 의 것이다).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from analysis import crosscheck
from analysis.crosscheck.pipeline import NoCrosscheck, build, run
from analysis.judge.pipeline import run as judge_run
from analysis.retrieval import topics as topic_registry
from analysis.trend.pipeline import run as quarter_run
from cosmai.cli import main
from db import corpus, seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "trend_sample"
VIEWS = (
    ROOT / "db" / "views" / "metrics_topic_quarter_violation.sql",
    ROOT / "db" / "views" / "topic_quarter_judgement_violation.sql",
)
AT = datetime(2026, 8, 20, tzinfo=UTC)
# 선케어 보드에 오른 제품 하나, 오르지 않은 제품 하나. 랭킹이 모집단을 정하는 것이 계약이라, 밖의
# 제품이 새어 들어오면 이 픽스처가 먼저 말한다.
RANKED = [
    ("oliveyoung", "suncare", "c1", "sun", AT, "01 > 선케어 > 선블록", 1, "톤업 선크림", 12000),
    ("oliveyoung", "skincare", "c2", "amp", AT, "01 > 스킨케어 > 앰플", 1, "PDRN 앰플", 30000),
]
REVIEWS = [
    ("oliveyoung", "r1", AT, "sun", "백탁 없이 촉촉해요 선크림 좋아요"),
    ("oliveyoung", "r2", AT, "sun", "눈시림이 심해요"),
    ("oliveyoung", "r3", AT, "amp", "끈적임 없이 좋아요"),
]
RATED = [
    ("oliveyoung", "sun", "t1", AT, "자극없이 순해요", 70, "자극도"),
    ("oliveyoung", "sun", "t2", AT, "자극이 느껴져요", 30, "자극도"),
    ("oliveyoung", "amp", "t1", AT, "자극없이 순해요", 99, "자극도"),
]


def _install_views(url: str, schema: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET ROLE needs_owner"))
            for view in VIEWS:
                conn.exec_driver_sql(view.read_text(encoding="utf-8").replace("needs.", f'"{schema}".'))
    finally:
        engine.dispose()


def _grant_commerce(url: str) -> None:
    """행이 없어도 권한은 있어야 한다 -- 없으면 "선케어 제품이 없다" 와 "못 읽는다" 가 같은 실패가 된다."""
    with connect(url) as source, source.cursor() as cur:
        cur.execute("GRANT SELECT ON rank_snapshot, review, review_topic, product TO needs_runtime")
        source.commit()


def _seed_commerce(url: str) -> None:
    with connect(url) as source, source.cursor() as cur:
        cur.executemany(
            "INSERT INTO rank_snapshot (source, board, category_key, product_key, captured_at, "
            "category_name, rank, product_name, price) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            RANKED,
        )
        cur.executemany(
            "INSERT INTO review (source, review_key, captured_at, product_key, body) VALUES (%s,%s,%s,%s,%s)",
            REVIEWS,
        )
        cur.executemany(
            "INSERT INTO review_topic (source, product_key, topic_key, captured_at, topic_name, "
            "share_pct, topic_group) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            RATED,
        )
        cur.execute(
            "INSERT INTO product (source, product_key, captured_at, name, first_seen_at, "
            "last_seen_at, ingredients) VALUES ('oliveyoung','amp',%s,'PDRN 앰플',%s,%s,%s)",
            (AT, AT, AT, "정제수, 병풀추출물, 나이아신아마이드(20,000 ppm)"),
        )
        cur.execute("GRANT SELECT ON rank_snapshot, review, review_topic, product TO needs_runtime")
        source.commit()


def _chunk(conn, chunk_id: str, source: str, doc_id: str, text_: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
            "VALUES (%s,%s,%s,0,%s,md5(%s))",
            (chunk_id, doc_id, source, text_, text_),
        )
    conn.commit()


@pytest.fixture
def crossable(
    needs_schema: str, trend_radar_schema: str, needs_runtime_url: str, database_url_for_tests: str,
    _schema_name: str,
) -> str:  # fmt: skip
    """`quarter` → `judge` 까지 간 스키마에 커머스 원천과 청크 몇 줄을 얹은 상태."""
    _install_views(needs_schema, _schema_name)
    seed.run_all(needs_runtime_url, only=("panel",))
    where = ["--kind", "aspect", "--version", "1", "--url", needs_runtime_url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0
    _seed_commerce(database_url_for_tests)
    with connect(needs_runtime_url) as conn:
        corpus.load(conn, FIXTURE / "corpus")
        quarter_run(conn)
        judge_run(conn)
        for source, review_key, _at, _product, body in REVIEWS:
            _chunk(
                conn, f"cr-{review_key}", "commerce_review", f"commerce_review:{source}:{review_key}", body
            )
        _chunk(conn, "tr-1#0", "youtube_transcript", "youtube_transcript:v1", "선크림 백탁 없이 촉촉")
        _chunk(conn, "cm-1#0", "youtube_comment", "youtube_comment:c1", "눈시림 있어요")
        _chunk(conn, "vt-1#0", "youtube_video", "youtube_video:v1", "선크림 리뷰")
    return needs_runtime_url


def test_the_answer_lands_on_the_run_the_judgement_already_has(crossable: str):
    with connect(crossable) as conn:
        built = build(conn, commerce_schema="")
        assert built.violations == ()
        assert built.status == "ok"
        assert built.quarter in built.quarters
        assert f"run={built.run_id}" in built.note


def test_the_ranking_decides_which_commerce_documents_count(crossable: str):
    """선케어 보드에 오른 제품의 리뷰만 든다 -- 이름 부분문자열로 고르지 않는다 (계약 §대조)."""
    with connect(crossable) as conn:
        built = build(conn, commerce_schema="")
    seen = {row.documents[crosscheck.COMMERCE_REVIEW] for row in built.composition}
    assert built.documents[crosscheck.COMMERCE_REVIEW] == 2, "amp 리뷰는 선케어 보드 밖이다"
    assert seen != {0}


def test_a_product_outside_the_suncare_boards_is_not_rated_either(crossable: str):
    with connect(crossable) as conn:
        built = build(conn, commerce_schema="")
    (rating,) = built.ratings
    assert rating.topic_key == "자극_눈시림"
    assert rating.products_rated == 1, "amp 의 설문은 들지 않는다"
    assert rating.positive_rate_mean == pytest.approx(70.0)


def test_every_source_carries_its_own_denominator_end_to_end(crossable: str):
    with connect(crossable) as conn:
        built = build(conn, commerce_schema="")
    for source in crosscheck.SOURCES:
        total = sum(row.shares[source] for row in built.composition)
        assert total == pytest.approx(100.0) or total == 0.0


def test_the_ingredient_audit_rides_along_and_says_what_it_caught(crossable: str):
    with connect(crossable) as conn:
        built = build(conn, commerce_schema="")
    caught = {row.key: row.rows for row in built.ingredients.audits}
    assert caught["시카센텔라"] == 1 and caught["나이아신아마이드"] == 1
    assert built.ingredients.suspects == ()
    assert built.ingredients.formula_products == 1


def test_the_answer_writes_nothing(crossable: str):
    """읽기 전용이라 운영 DB 에 그대로 돌린다 -- 그 문장을 지문으로 붙든다."""
    with connect(crossable) as conn:
        before = _fingerprint(conn)
        run(conn, commerce_schema="")
        assert _fingerprint(conn) == before


def _fingerprint(conn) -> list[tuple]:
    """이 스키마의 모든 표의 행수. 읽을 수 있는 것만 센다 -- 커머스 원천은 소유 롤의 것이라
    `needs_runtime` 에게는 SELECT 도 없는 표가 섞여 있다."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name, (xpath('/row/c/text()', "
            "query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), "
            "false, true, '')))[1]::text::int FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' "
            "AND has_table_privilege(format('%I.%I', table_schema, table_name), 'SELECT') "
            "ORDER BY table_name"
        )
        return cur.fetchall()


def test_a_run_without_judgement_rows_is_blocked_not_failed(
    needs_schema: str, trend_radar_schema: str, needs_runtime_url: str, database_url_for_tests: str,
    _schema_name: str,
):  # fmt: skip
    """판정을 아직 안 한 것이라 실패가 아니라 막힘이다 (`cosmai trend judge` 를 돌려라)."""
    _install_views(needs_schema, _schema_name)
    seed.run_all(needs_runtime_url, only=("panel",))
    where = ["--kind", "aspect", "--version", "1", "--url", needs_runtime_url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0
    _seed_commerce(database_url_for_tests)
    with connect(needs_runtime_url) as conn:
        corpus.load(conn, FIXTURE / "corpus")
        quarter_run(conn)
        with pytest.raises(NoCrosscheck, match="trend judge"):
            build(conn, commerce_schema="")


def test_no_suncare_product_in_the_ranking_is_blocked_not_failed(
    needs_schema: str, trend_radar_schema: str, needs_runtime_url: str, database_url_for_tests: str,
    _schema_name: str,
):  # fmt: skip
    """대조할 커머스 소스가 아직 없다는 뜻이다 -- 0행을 조용히 답으로 내면 안 된다."""
    _install_views(needs_schema, _schema_name)
    seed.run_all(needs_runtime_url, only=("panel",))
    where = ["--kind", "aspect", "--version", "1", "--url", needs_runtime_url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0
    _grant_commerce(database_url_for_tests)
    with connect(needs_runtime_url) as conn:
        corpus.load(conn, FIXTURE / "corpus")
        quarter_run(conn)
        judge_run(conn)
        with pytest.raises(NoCrosscheck, match="collect commerce"):
            build(conn, commerce_schema="")


def test_the_sources_disagreeing_is_not_a_partial_outcome(crossable: str):
    """**어긋남은 발견이지 실패가 아니다** -- #41 이 §민감도 에서 못 박은 자리와 같다."""
    with connect(crossable) as conn:
        outcome = run(conn, commerce_schema="")
    assert outcome.status == "ok" and outcome.violations == ()
    assert [line for line in outcome.lines if line.startswith("구성")]
    assert [line for line in outcome.lines if line.startswith("평가")]
    assert [line for line in outcome.lines if line.startswith("성분")]
    assert [line for line in outcome.lines if line.startswith("감사")]
    # 어긋난 주제가 실제로 있어야 이 테스트가 "0 은 발견을 숨기지 않는다" 를 말한다.
    assert [row for row in outcome.built.composition if row.reading]


def test_the_cli_calls_blocked_blocked(crossable: str, capsys: pytest.CaptureFixture[str]):
    """대조할 커머스 소스가 없는 것은 막힘(2)이다. **성공 경로를 CLI 로 몰지 않는 이유**: 검사용
    스키마 하나가 needs 와 trend_radar 를 함께 담는데(tests/conftest.py) CLI 는 운영의
    `trend_radar` 를 이름으로 부른다 -- 운영 `needs_runtime` 의 search_path 가 `needs` 뿐이라
    그래야 한다. 그 갈래는 위 테스트가 파이프라인에서 진다."""
    assert main(["trend", "crosscheck", "--url", crossable]) == 2
    assert "collect commerce" in capsys.readouterr().out
