"""코퍼스 스냅샷에서 분기 표가 서고, 저장된 행이 두 불변식에 답한다 (포크 #5).

`tests/test_trend_quarter.py` 가 수식을 DB 없이 묻는다면 여기는 **모집단**을 묻는다: 무엇이 분모에
들고 무엇이 안 드는가, 그리고 적재 뒤 `needs.metrics_topic_quarter_violation` 이 비어 있는가.
픽스처는 `tests/fixtures/yt_handoff` 한 벌이고, 그 열두 문서가 걸러 내야 할 다섯 가지를 다 담고 있다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import create_engine, text

from analysis.trend import METRIC_VERSION
from analysis.trend.pipeline import NoPopulation, build, note_of, run
from cosmai.cli import main
from db import corpus, seed
from db.corpus import verify
from db.seed._common import connect

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "yt_handoff"
VIEW = ROOT / "db" / "views" / "metrics_topic_quarter_violation.sql"
OWNER = text("SET ROLE needs_owner")

# 픽스처가 걸러 내야 하는 것들을 뺀 나머지: 장문·product·선크림 언급이 있는 영상 둘, 그 영상들에
# 달린 댓글 셋(하나는 같은 영상 안 복붙)이 전부다.
QUARTER = "2025Q2"
TOPICS = ("발림성", "백탁")


@pytest.fixture
def loaded(needs_schema: str, needs_runtime_url: str, _schema_name: str) -> str:
    """뷰는 배포(`db/migrate.sh`)가 얹는 것이라 per-test 스키마에서는 여기서 얹는다."""
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            conn.exec_driver_sql(VIEW.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
    finally:
        engine.dispose()
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn:
        corpus.load(conn, FIXTURE)
    return needs_runtime_url


def _stored(cur: psycopg.Cursor[Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    cur.execute(
        "SELECT source, topic_key, quarter, mentions, documents, quarter_mentions, denom_channels,"
        " composition, velocity_yoy, persistence, persist_quarters, window_quarters, unique_ratio,"
        " channel_count, channel_diffusion, sample_ok, scope, content_type, panel_version, panel_role"
        " FROM metrics_topic_quarter ORDER BY source, topic_key, quarter"
    )
    names = [c.name for c in cur.description or ()]
    rows = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    return {(r["source"], r["topic_key"], r["quarter"]): r for r in rows}


def _violations(cur: psycopg.Cursor[Any]) -> list[tuple[Any, ...]]:
    cur.execute("SELECT violation, quarter, detail FROM metrics_topic_quarter_violation")
    return cur.fetchall()


def test_the_stored_rows_pass_the_two_invariants_the_view_asks_about(loaded: str):
    """계약 문장이 아니라 저장된 행이 답한다 -- 뷰가 비어 있으면 격자가 조밀하고 분모가 닫힌 것이다."""
    with connect(loaded) as conn:
        outcome = run(conn)
        with conn.cursor() as cur:
            assert _violations(cur) == []
    assert outcome.violations == []
    assert outcome.status == "ok"
    # 주제 2 × 분기 1 × source 2. 언급이 하나도 없는 (발림성, 댓글) 칸도 행이다.
    assert outcome.written == 4
    assert outcome.counts == {"youtube_video": 2, "youtube_comment": 2}


def test_the_population_is_the_one_the_corpus_manifest_reproduces(loaded: str):
    """모집단이 `db/corpus/verify.py` 의 재현 정의와 갈리면 두 표가 다른 분모를 쓰는 것이다."""
    with connect(loaded) as conn:
        made = build(conn)
        reproduced = verify.reproduce(conn)
    videos = {row.quarter: row.documents for row in made.rows if row.source == "youtube_video"}
    comments = {row.quarter: row.documents for row in made.rows if row.source == "youtube_comment"}
    assert videos == {QUARTER: reproduced["선크림_장문_product"]}
    assert comments == {QUARTER: reproduced["그_영상_댓글_중복제외"]}


def test_the_video_row_counts_only_the_long_product_videos_that_mention_the_category(loaded: str):
    """쇼츠·video_unknown·expert 채널·선크림 언급이 없는 장문은 넷 다 분모 밖이다 (매니페스트 규칙 4·5·6)."""
    with connect(loaded) as conn:
        run(conn)
        with conn.cursor() as cur:
            stored = _stored(cur)
    row = stored[("youtube_video", "백탁", QUARTER)]
    assert (row["mentions"], row["documents"], row["quarter_mentions"], row["denom_channels"]) == (1, 2, 2, 2)
    assert float(row["composition"]) == 0.5
    assert (row["scope"], row["content_type"], row["panel_role"]) == ("선블록", "long_form", "product")


def test_the_comment_row_is_attributed_to_the_parent_videos_quarter_and_folds_copy_paste(loaded: str):
    """댓글 시각으로 분기를 만들면 분모가 정의되지 않는다 -- 복붙은 분모에만 들고 반응 1건이 아니다."""
    with connect(loaded) as conn:
        run(conn)
        with conn.cursor() as cur:
            stored = _stored(cur)
    row = stored[("youtube_comment", "백탁", QUARTER)]
    # 부모 영상은 2025-04, 댓글은 2025-05 에 달렸다.
    assert row["quarter"] == QUARTER
    assert (row["mentions"], row["documents"]) == (1, 2)
    assert float(row["unique_ratio"]) == 0.5
    assert row["channel_count"] == 1


def test_the_diffusion_column_does_not_depend_on_the_source(loaded: str):
    with connect(loaded) as conn:
        run(conn)
        with conn.cursor() as cur:
            stored = _stored(cur)
    for topic in TOPICS:
        video = stored[("youtube_video", topic, QUARTER)]
        comment = stored[("youtube_comment", topic, QUARTER)]
        assert video["channel_diffusion"] == comment["channel_diffusion"]
        assert video["denom_channels"] == comment["denom_channels"]


def test_a_topic_the_corpus_marks_as_not_for_trends_gets_no_row(loaded: str):
    """`선크림`(영상의 93%)은 필터·장르 표시이지 축이 아니다 -- 섞이면 분모가 닫히지 않는다 (규칙 7)."""
    with connect(loaded) as conn:
        run(conn)
        with conn.cursor() as cur:
            stored = _stored(cur)
    assert {topic for _, topic, _ in stored} == set(TOPICS)


def test_rerunning_writes_the_same_rows_into_the_same_run(loaded: str):
    with connect(loaded) as conn:
        first = run(conn)
        with conn.cursor() as cur:
            before = _stored(cur)
        second = run(conn)
        with conn.cursor() as cur:
            after = _stored(cur)
            cur.execute("SELECT count(*), min(status) FROM analysis_run")
            runs = cur.fetchone()
    assert first.run_id == second.run_id
    assert before == after
    assert runs == (1, "ok")


def test_a_row_left_over_from_an_earlier_run_is_cleared_not_merged(loaded: str):
    """부분 갱신이면 옛 주제의 행이 남아 격자가 조밀하지 않게 된다 -- 그때 뷰가 sparse_grid 를 낸다."""
    with connect(loaded) as conn:
        outcome = run(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO metrics_topic_quarter (run_id, scope, topic_key, quarter, source,"
                " content_type, panel_version, panel_role, mentions, documents, quarter_mentions,"
                " denom_channels, sample_ok) VALUES (%s, '선블록', '유기자차', '2024Q1',"
                " 'youtube_video', 'long_form', 1, 'product', 0, 2, 0, 2, false)",
                (outcome.run_id,),
            )
        conn.commit()
        with conn.cursor() as cur:
            assert _violations(cur) != []
        again = run(conn)
        with conn.cursor() as cur:
            assert _violations(cur) == []
    assert again.written == 4


def test_the_run_records_the_metric_version_the_rows_were_made_with(loaded: str):
    """`metrics_topic_quarter` 는 A19 로 `*_version` 컬럼이 없다 -- 답하는 자리는 이 키 하나뿐이다."""
    with connect(loaded) as conn:
        outcome = run(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT versions, note FROM analysis_run WHERE run_id = %s", (outcome.run_id,))
            recorded = cur.fetchone()
    assert recorded is not None
    versions, note = recorded
    assert versions["metric"] == METRIC_VERSION
    assert note == note_of("선블록", outcome.snapshot_id, outcome.panel_version)


def test_a_snapshot_with_no_panel_video_is_blocked_not_silently_empty(needs_runtime_url: str):
    """0 을 조용히 내면 '비율이 0'과 '비율이 없다'가 같은 표에서 같아 보인다."""
    seed.run_all(needs_runtime_url, only=("panel",))
    with connect(needs_runtime_url) as conn, pytest.raises(NoPopulation):
        run(conn)


def test_the_subcommand_writes_the_table_and_says_what_it_wrote(loaded: str, capsys: Any):
    assert main(["trend", "quarter", "--url", loaded]) == 0
    printed = capsys.readouterr().out
    assert "rows=4" in printed and "youtube_comment=2" in printed
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM metrics_topic_quarter")
        assert cur.fetchone() == (4,)


def test_the_subcommand_is_blocked_when_the_snapshot_is_not_there_yet(needs_runtime_url: str, capsys: Any):
    seed.run_all(needs_runtime_url, only=("panel",))
    assert main(["trend", "quarter", "--url", needs_runtime_url]) == 2
    assert "corpus" in capsys.readouterr().out
