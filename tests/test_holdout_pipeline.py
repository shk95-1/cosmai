"""홀드아웃의 읽기: 청크 색인이 두 팔을 가르고, 아무것도 쓰지 않는다 (포크 #51).

§대조(#7)·§민감도(#41)와 같은 자리다 -- 답이 표가 아니라 stdout 이라, 이 파일이 지는 것은 값이 아니라
**두 팔의 가름**과 **한 시점**과 **막힘·종료 코드**, 그리고 "정말 아무것도 안 썼는가" 다. 값은 규칙
테스트(`tests/test_holdout_rules.py`)가 진다.

커머스 원천은 소유 롤이 넣고 `needs_runtime` 은 SELECT 로만 읽는다 (`tests/test_crosscheck_pipeline.py`
와 같은 방식이고 같은 이유다 -- 운영에서 `trend_radar` 는 collectors/commerce 의 것이다).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from analysis import holdout as rules
from analysis.holdout import pipeline
from analysis.holdout.pipeline import NoHoldout, build, load, run
from analysis.retrieval import topics as topic_registry
from cosmai.cli import main
from db.seed._common import connect

pytestmark = pytest.mark.postgres

EARLY = datetime(2026, 8, 18, tzinfo=UTC)
LATE = datetime(2026, 8, 26, tzinfo=UTC)
# 선케어 보드에 오른 제품 하나와 카테고리 이름으로만 걸리는 제품 하나. 밖의 제품(`amp`)이 새어 들어오면
# 이 픽스처가 먼저 말한다 -- 모집단은 §대조 의 술어 그대로다.
RANKED = [
    ("oliveyoung", "suncare", "c1", "sun", EARLY, "01 > 스킨케어 > 기타", 1, "톤업 선크림", 12000),
    ("glowpick", "category", "c9", "sun2", EARLY, "선크림", 1, "선크림 2호", 15000),
    ("oliveyoung", "skincare", "c2", "amp", EARLY, "01 > 스킨케어 > 앰플", 1, "PDRN 앰플", 30000),
]
# (source, review_key, captured_at, product_key, body, 청크가 있는가)
#
# **날짜와 청크 색인을 일부러 어긋나게 놓았다.** `r-late-seen` 은 늦게 수집됐는데 청크가 있고,
# `r-early-unseen` 은 일찍 수집됐는데 청크가 없다. 날짜 컷오프로 가르면 둘 다 반대 팔에 선다.
REVIEWS = [
    ("oliveyoung", "r-early-seen", EARLY, "sun", "백탁 없이 촉촉해요", True),
    ("oliveyoung", "r-late-seen", LATE, "sun", "백탁이 좀 있어요", True),
    ("oliveyoung", "r-early-unseen", EARLY, "sun", "백탁이 심해요", False),
    ("glowpick", "r-late-unseen", LATE, "sun2", "발림성이 좋아요", False),
    ("oliveyoung", "r-empty", LATE, "sun", "", False),
    ("oliveyoung", "r-offaxis", LATE, "amp", "끈적임 없이 좋아요", False),
]


def _seed_commerce(url: str) -> None:
    with connect(url) as source, source.cursor() as cur:
        cur.executemany(
            "INSERT INTO rank_snapshot (source, board, category_key, product_key, captured_at, "
            "category_name, rank, product_name, price) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            RANKED,
        )
        cur.executemany(
            "INSERT INTO review (source, review_key, captured_at, product_key, body) VALUES (%s,%s,%s,%s,%s)",
            [row[:5] for row in REVIEWS],
        )
        cur.execute("GRANT SELECT ON rank_snapshot, review, review_topic, product TO needs_runtime")
        source.commit()


def _chunk(conn, doc_id: str, text_: str, *, source: str = "commerce_review") -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO retrieval_chunk (chunk_id, doc_id, source, ordinal, text, text_md5) "
            "VALUES (%s,%s,%s,0,%s,md5(%s))",
            (f"{doc_id}#0", doc_id, source, text_, text_),
        )
    conn.commit()


def _dictionary(url: str) -> None:
    where = ["--kind", "aspect", "--version", "1", "--url", url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0


@pytest.fixture
def holdable(
    needs_schema: str, trend_radar_schema: str, needs_runtime_url: str, database_url_for_tests: str
) -> str:  # fmt: skip
    """커머스 원천과, 그중 일부만 청크로 구운 상태. **그 일부가 곧 기존 팔이다.**"""
    _dictionary(needs_runtime_url)
    _seed_commerce(database_url_for_tests)
    with connect(needs_runtime_url) as conn:
        for source, key, _at, _product, body, chunked in REVIEWS:
            if chunked:
                _chunk(conn, f"commerce_review:{source}:{key}", body)
        _chunk(conn, "youtube_comment:c1", "눈시림 있어요", source="youtube_comment")
    return needs_runtime_url


def _arms(url: str) -> tuple[list[str], list[str]]:
    with connect(url) as conn:
        read = load(conn, commerce_schema="")
    return (
        [review.product_key for review in read.seen],
        [review.product_key for review in read.holdout],
    )


def test_the_chunk_index_splits_the_arms_not_the_capture_date(holdable: str):
    """**컷오프는 날짜가 아니라 청크 색인이다** (계약 §홀드아웃). 픽스처는 둘을 어긋나게 놓았으므로,
    날짜로 가르는 구현은 여기서 두 팔이 통째로 뒤바뀐다."""
    with connect(holdable) as conn:
        read = load(conn, commerce_schema="")
    seen = {review.captured_at for review in read.seen}
    unseen = {review.captured_at for review in read.holdout}
    assert seen == {EARLY, LATE}, "늦게 수집됐어도 청크가 있으면 기존 팔이다"
    assert unseen == {EARLY, LATE}, "일찍 수집됐어도 청크가 없으면 홀드아웃이다"
    assert (len(read.seen), len(read.holdout)) == (2, 2)


def test_a_review_with_an_empty_body_sits_in_neither_arm_and_is_counted(holdable: str):
    """빈 본문은 청크를 만들지 않는다. 남기면 "안 본 리뷰" 가 아니라 "볼 것이 없는 리뷰" 가 홀드아웃을
    채운다 -- 그리고 그 순간 홀드아웃의 언급률이 조용히 내려간다 (계약 §홀드아웃)."""
    with connect(holdable) as conn:
        read = load(conn, commerce_schema="")
        built = build(conn, commerce_schema="")
    assert read.dropped_empty == 1
    assert len(read.seen) + len(read.holdout) == 4, "빈 본문이 팔에 들면 5 가 된다"
    assert "empty=1" in built.note


def test_the_ranking_decides_the_population_for_both_arms(holdable: str):
    """두 팔이 같은 술어 위에 서야 차이가 표본의 것이지 필터의 것이 아니다 (계약 §홀드아웃)."""
    seen, unseen = _arms(holdable)
    assert "amp" not in seen and "amp" not in unseen
    assert set(seen) == {"sun"} and set(unseen) == {"sun", "sun2"}


def test_the_answer_reaches_the_rules_with_the_topics_already_matched(holdable: str):
    with connect(holdable) as conn:
        built = build(conn, commerce_schema="")
    rows = {row.topic_key: row for row in built.comparison.topics}
    assert rows["백탁"].seen_documents == 2 and rows["백탁"].holdout_documents == 1
    assert rows["발림성"].holdout_documents == 1
    assert built.comparison.window == rules.WINDOW_EXTENDED


def test_a_review_committed_between_two_reads_lands_in_no_arm_at_all(
    holdable: str, database_url_for_tests: str, monkeypatch: pytest.MonkeyPatch
):
    """**네 읽기가 한 시점을 본다** (계약 §홀드아웃). 격리 수준을 `READ COMMITTED` 로 낮추면 이 리뷰가
    홀드아웃 팔에 나타나 `seen + holdout + empty` 가 어떤 모집단의 크기도 아니게 된다."""
    real = pipeline.commerce_sql

    def spy(schema: str, statement: str):
        # 청크 명부를 읽은 **뒤**, 모집단을 읽기 **직전**에 다른 커넥션이 커밋한다.
        if statement is pipeline.POPULATION:
            with connect(database_url_for_tests) as other, other.cursor() as cur:
                cur.execute(
                    "INSERT INTO review (source, review_key, captured_at, product_key, body) "
                    "VALUES ('oliveyoung','r-midread',%s,'sun','백탁 중간에 들어온 리뷰')",
                    (LATE,),
                )
                other.commit()
        return real(schema, statement)

    monkeypatch.setattr(pipeline, "commerce_sql", spy)
    with connect(holdable) as conn:
        read = load(conn, commerce_schema="")
    assert len(read.seen) + len(read.holdout) == 4, "중간에 들어온 리뷰가 팔에 들면 5 가 된다"
    # 그 리뷰가 정말 커밋됐는지 -- 안 됐다면 위 단언은 아무것도 말하지 않는다.
    with connect(database_url_for_tests) as other, other.cursor() as cur:
        cur.execute("SELECT count(*) FROM review WHERE review_key = 'r-midread'")
        assert (cur.fetchone() or (0,))[0] == 1


def test_the_isolation_level_is_put_back_when_the_read_raises(holdable: str):
    """격리 수준은 커넥션 전역이다. 막힘으로 빠져나가면서 두고 가면 그 뒤의 아무 코드나 물려받는다."""
    with connect(holdable) as conn:
        before = conn.isolation_level
        with conn.cursor() as cur:
            cur.execute("DELETE FROM retrieval_chunk")
        conn.commit()
        with pytest.raises(NoHoldout, match="retrieval chunk"):
            load(conn, commerce_schema="")
        assert conn.isolation_level == before


def test_a_commerce_chunk_without_a_review_row_makes_the_answer_partial(holdable: str):
    """**종료 코드 1 은 이 자리 하나를 위해 있다.** 청크에는 외래키가 없다(020) -- 원천이 사라지면
    기존 팔은 분석이 실제로 본 그 팔이 아니고, 그때 이 산출은 믿을 것이 못 된다."""
    with connect(holdable) as conn:
        _chunk(conn, "commerce_review:oliveyoung:r-gone", "사라진 리뷰의 청크")
        built = build(conn, commerce_schema="")
    assert built.status == "partial"
    assert [line for line in built.violations if line.startswith("chunk_orphan 1")]


def test_a_holdout_that_does_not_reproduce_is_not_a_partial_outcome(holdable: str):
    """**재현 실패는 발견이지 실패가 아니다** -- #41 이 §민감도 에서, #7 이 §대조 에서 못 박은 자리와 같다."""
    with connect(holdable) as conn:
        outcome = run(conn, commerce_schema="")
    assert outcome.status == "ok" and outcome.violations == ()
    assert outcome.built.comparison.verdict, "판정이 비면 이 테스트는 아무것도 말하지 않는다"
    for head in ("팔 ", "지표 ", "구성 ", "바스켓"):
        assert [line for line in outcome.lines if line.startswith(head)], head


def test_the_answer_writes_nothing(holdable: str):
    """읽기 전용이라 운영 DB 에 그대로 돌린다 -- 그 문장을 지문으로 붙든다."""
    with connect(holdable) as conn:
        before = _fingerprint(conn)
        run(conn, commerce_schema="")
        assert _fingerprint(conn) == before


def _fingerprint(conn) -> list[tuple]:
    """이 스키마의 모든 표의 행수. 읽을 수 있는 것만 센다 (§대조 의 같은 함수와 같은 이유)."""
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


def test_no_commerce_chunk_at_all_is_blocked_not_failed(holdable: str):
    """기준 시점 자체가 없다는 뜻이다 -- 0행을 조용히 "전부 홀드아웃" 이라고 답하면 안 된다."""
    with connect(holdable) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM retrieval_chunk WHERE source = 'commerce_review'")
        conn.commit()
        with pytest.raises(NoHoldout, match="retrieval chunk"):
            build(conn, commerce_schema="")


def test_a_chunk_index_that_misses_the_suncare_population_is_blocked_not_failed(holdable: str):
    """청크는 있는데 **선케어 모집단 안에** 하나도 없는 자리. 비교할 기존 팔이 없으므로, "전부
    홀드아웃" 이라고 답하면 그 순간 이 명령은 되묻기가 아니라 그냥 세기가 된다.

    막힘 여섯 중 이 갈래만 테스트가 없어 계약에만 있었다 (#51 리뷰 §5).
    """
    with connect(holdable) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM retrieval_chunk WHERE source = 'commerce_review'")
        conn.commit()
        # 랭킹 밖 제품(`amp`)의 리뷰 청크 하나만 남긴다 -- 청크는 있고 모집단 안에는 없다.
        _chunk(conn, "commerce_review:oliveyoung:r-offaxis", "끈적임 없이 좋아요")
        with pytest.raises(NoHoldout, match="no baseline arm"):
            build(conn, commerce_schema="")


def test_no_unseen_review_is_blocked_not_failed(holdable: str, database_url_for_tests: str):
    """되물을 새 표본이 아직 없다. 답이 계산된 것이 아니라 물음이 서지 않은 것이다."""
    with connect(holdable) as conn:
        for source, key, _at, _product, body, chunked in REVIEWS:
            if not chunked and body:
                _chunk(conn, f"commerce_review:{source}:{key}", body)
        with pytest.raises(NoHoldout, match="no unseen sample"):
            build(conn, commerce_schema="")


def test_no_suncare_product_in_the_ranking_is_blocked_not_failed(
    needs_schema: str, trend_radar_schema: str, needs_runtime_url: str, database_url_for_tests: str
):  # fmt: skip
    """되물을 커머스 소스가 아직 없다는 뜻이다 (§대조 와 같은 자리, 같은 메시지)."""
    _dictionary(needs_runtime_url)
    with connect(database_url_for_tests) as source, source.cursor() as cur:
        cur.execute("GRANT SELECT ON rank_snapshot, review, review_topic, product TO needs_runtime")
        source.commit()
    with connect(needs_runtime_url) as conn:
        with pytest.raises(NoHoldout, match="collect commerce"):
            build(conn, commerce_schema="")


def test_the_cli_calls_blocked_blocked(holdable: str, capsys: pytest.CaptureFixture[str]):
    """**성공 경로를 CLI 로 몰지 않는 이유**는 §대조 와 같다: 검사용 스키마 하나가 needs 와 trend_radar 를
    함께 담는데 CLI 는 운영의 `trend_radar` 를 이름으로 부른다."""
    assert main(["trend", "holdout", "--url", holdable]) == 2
    assert "collect commerce" in capsys.readouterr().out


def test_the_cli_gives_zero_when_the_holdout_merely_fails_to_reproduce(
    holdable: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(pipeline, "COMMERCE_SCHEMA", "")
    assert main(["trend", "holdout", "--url", holdable]) == 0
    printed = capsys.readouterr().out
    assert "trend holdout seen=" in printed and "팔    기존" in printed


def test_the_cli_turns_a_violation_into_exit_one(
    holdable: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """계약 §종료 코드 의 1 이 실제로 CLI 에서 나오는가."""
    monkeypatch.setattr(pipeline, "COMMERCE_SCHEMA", "")
    with connect(holdable) as conn:
        _chunk(conn, "commerce_review:oliveyoung:r-gone", "사라진 리뷰의 청크")
    assert main(["trend", "holdout", "--url", holdable]) == 1
    assert "chunk_orphan" in capsys.readouterr().out
