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

from analysis.retrieval import topics as topic_registry
from analysis.trend import METRIC_VERSION
from analysis.trend.pipeline import NoPopulation, TopicAxisDrift, build, note_of, run
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
OBSERVED = ("발림성", "백탁")
# 축은 관측이 아니라 레지스트리다 (interfaces.md "`trend_use=true` 인 주제(현재 13개)"): 이 픽스처가
# 관측하는 주제는 둘뿐이라, 그 13 × 1분기 × 2 source 가 이 해석이 강제되는 자리다.
AXIS_TOPICS = 13
ROWS = AXIS_TOPICS * 2


def _axis(conn: psycopg.Connection[Any]) -> list[str]:
    return [entry["topic"] for entry in topic_registry.load(conn).entries if entry["trend_use"]]


def _install_registry(url: str) -> None:
    """주제 축의 레지스트리를 세우는 길은 운영과 같은 하나다 -- 픽스처가 사전을 손으로 다시 적으면
    축이 두 벌이 되고, 그때부터 이 테스트는 자기 사본을 검사한다."""
    where = ["--kind", "aspect", "--version", "1", "--url", url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0


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
    _install_registry(needs_runtime_url)
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
    # trend_use 주제 13 × 분기 1 × source 2. 언급이 하나도 없는 (발림성, 댓글) 칸도 행이다.
    assert outcome.written == ROWS
    assert outcome.counts == {"youtube_video": AXIS_TOPICS, "youtube_comment": AXIS_TOPICS}


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
    for topic in OBSERVED:
        video = stored[("youtube_video", topic, QUARTER)]
        comment = stored[("youtube_comment", topic, QUARTER)]
        assert video["channel_diffusion"] == comment["channel_diffusion"]
        assert video["denom_channels"] == comment["denom_channels"]


def test_a_topic_the_registry_marks_as_not_for_trends_gets_no_row(loaded: str):
    """`선크림`(영상의 93%)은 필터·장르 표시이지 축이 아니다 -- 섞이면 분모가 닫히지 않는다 (규칙 7)."""
    with connect(loaded) as conn:
        run(conn)
        axis = _axis(conn)
        with conn.cursor() as cur:
            stored = _stored(cur)
    assert {"선크림", "추천_재구매"}.isdisjoint(axis)
    assert {topic for _, topic, _ in stored} == set(axis)


def test_a_registry_topic_the_snapshot_never_mentions_is_still_a_row(loaded: str):
    """축의 두 변은 갈라져 있다 (interfaces.md §분기 표의 행 집합): 분기는 이 산출에 존재하는 것,
    주제는 레지스트리의 `trend_use=true` 전부다. 관측 distinct 로 축을 만들면 한 번도 안 걸린 주제가
    표에서 조용히 사라지고, 격자는 여전히 직사각형이라 불변식 뷰가 아무 말도 하지 않는다."""
    with connect(loaded) as conn:
        run(conn)
        axis = _axis(conn)
        with conn.cursor() as cur:
            stored = _stored(cur)
    silent = [topic for topic in axis if topic not in OBSERVED]
    assert len(axis) == AXIS_TOPICS and len(silent) == AXIS_TOPICS - len(OBSERVED)
    for topic in silent:
        for source in ("youtube_video", "youtube_comment"):
            row = stored[(source, topic, QUARTER)]
            # 언급 0 칸의 모양은 계약이 부른다: mentions=0 · composition=0 · unique_ratio=1 · sample_ok=false.
            assert (row["mentions"], row["channel_count"], row["sample_ok"]) == (0, 0, False)
            assert (float(row["composition"]), float(row["unique_ratio"])) == (0.0, 1.0)
            assert row["quarter_mentions"] == stored[(source, OBSERVED[0], QUARTER)]["quarter_mentions"]


def test_a_snapshot_topic_the_registry_does_not_know_is_blocked(loaded: str):
    """레지스트리 밖 주제의 언급은 어느 행에도 `quarter_mentions` 에도 들지 못해 분모에서 조용히
    빠진다 -- 스냅샷과 활성 사전이 갈린 것이므로 표를 세우지 않는다."""
    with connect(loaded) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus_mention (snapshot_id, doc_id, topic_id, topic_type, trend_use)"
                " SELECT snapshot_id, doc_id, '무명_주제', 'attribute', true FROM corpus_document"
                " WHERE source = 'youtube_video' ORDER BY doc_id LIMIT 1"
            )
        conn.commit()
        with pytest.raises(TopicAxisDrift, match="무명_주제"):
            run(conn)
    # 막힘이지 실패가 아니다 -- 사전 버전을 맞추면 같은 명령이 그대로 선다.
    assert main(["trend", "quarter", "--url", loaded]) == 2


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
    assert again.written == ROWS


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
    assert f"rows={ROWS}" in printed and f"youtube_comment={AXIS_TOPICS}" in printed
    with connect(loaded) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM metrics_topic_quarter")
        assert cur.fetchone() == (ROWS,)


def test_the_subcommand_is_blocked_when_the_snapshot_is_not_there_yet(needs_runtime_url: str, capsys: Any):
    seed.run_all(needs_runtime_url, only=("panel",))
    assert main(["trend", "quarter", "--url", needs_runtime_url]) == 2
    assert "corpus" in capsys.readouterr().out
