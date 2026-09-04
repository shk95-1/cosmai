"""승격이 근거와 카드를 바꾸지 않았음을 기계가 말한다: ydc 출력과의 대조 (포크 #6).

#5·#40 의 골든이 지표와 판정에서 한 일을 여기서 한다. 고정 입력은 같은 표본 한 벌
(`tests/fixtures/trend_sample/`)이고, 대조군은 ydc 를 **손대지 않고** 그 표본에 돌려 얻었다:

    python evidence_comments.py --common tests/fixtures/trend_sample/corpus --top 3 \
        --out .../ydc_evidence_v0.2.csv
    python cards.py --quarter <분기>            # reports/ 에 위 CSV 와 #40 의 판정 CSV 를 두고

이 표본의 판정 격자에는 **2025Q1 이 없다**(그 분기에 선크림 장문 영상이 없다). 그래서 카드 골든은 11분기
이고, 격자에 없는 분기를 물으면 카드는 막힘(2)이다 -- 그 자리는 `tests/test_evidence_pipeline.py` 가 진다.

**한 자리가 1:1 이 아니고, 그것이 이 파일의 답이다.** ydc 는 좋아요로만 정렬하고 동점의 승자를 CSV
읽기 순서가 정한다(파이썬 정렬이 안정적이다). 저장되는 표는 재실행이 같은 행을 내야 하므로 우리는
`doc_id` 를 2차 키로 못 박았고(계약 §근거), 그래서 **좋아요 사다리는 102/102 로 같고 자리는 24행에서
갈린다** -- 그중 20행은 같은 셋 안의 순서 차이이고, ydc 가 아예 안 뽑은 문서를 우리가 고른 것은 4행이다.
갈린 24행이 전부 동점 자리이고 ydc 의 문서도 그 자리의 정당한 후보라는 것을 아래가 증명한다 -- 그 성질이
깨지면 갈린 것은 동점 처리가 아니라 규칙이다.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from analysis.cards.pipeline import collect
from analysis.evidence.pipeline import build, run
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
    ROOT / "db" / "views" / "topic_quarter_evidence_quote.sql",
    ROOT / "db" / "views" / "topic_quarter_evidence_violation.sql",
)
OWNER = text("SET ROLE needs_owner")
# 동점 자리에서 ydc 와 다른 문서를 고르는 행의 수. 숫자를 못 박는 것은 "조금 갈린다"가 조용히 자라지
# 않게 하기 위해서다 -- 규칙이 바뀌면 이 수가 먼저 움직인다.
TIED_ROWS_THAT_DIFFER = 24
LADDER_ROWS = 102
# 그중 ydc 가 세 자리 안에 아예 넣지 않은 문서를 고른 행. 24 는 자리가 갈린 수이고 이것은 뽑힌 집합이
# 갈린 수다 -- 나머지 20 은 같은 셋의 순서만 다르다.
DOCS_YDC_DID_NOT_PICK = 4


def _ydc_evidence() -> list[dict[str, str]]:
    with (FIXTURE / "ydc_evidence_v0.2.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _ydc_cards() -> dict[str, list[dict]]:
    return json.loads((FIXTURE / "ydc_cards_v0.2.json").read_text(encoding="utf-8"))


@pytest.fixture
def quoted(needs_schema: str, needs_runtime_url: str, _schema_name: str) -> str:
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            for view in VIEWS:
                conn.exec_driver_sql(view.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
    finally:
        engine.dispose()
    seed.run_all(needs_runtime_url, only=("panel",))
    where = ["--kind", "aspect", "--version", "1", "--url", needs_runtime_url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0
    with connect(needs_runtime_url) as conn:
        corpus.load(conn, FIXTURE / "corpus")
        quarter_run(conn)
        judge_run(conn)
        run(conn)
    return needs_runtime_url


def _judged_topics(url: str) -> set[str]:
    with connect(url) as conn:
        return {entry["topic"] for entry in topic_registry.load(conn).entries if entry["trend_use"]}


def test_the_topics_ydc_quotes_but_we_do_not_are_the_ones_with_no_judgement_cell(quoted: str):
    """`선크림`·`추천_재구매` 는 필터·장르 표시라 판정하지 않는다(`trend_use = false`) -- 025 의 FK 가
    거절할 행을 애초에 만들지 않는다."""
    judged = _judged_topics(quoted)
    outside = {row["topic_id"] for row in _ydc_evidence()} - judged
    assert outside == {"선크림", "추천_재구매"}
    with connect(quoted) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT topic_key FROM topic_quarter_evidence")
        assert {topic for (topic,) in cur.fetchall()} <= judged


def test_our_ladder_is_the_ladder_ydc_evidence_comments_writes(quoted: str):
    """같은 후보에서 좋아요로 줄을 세운 것이라 (셀, 자리) 마다 좋아요가 같아야 한다."""
    judged = _judged_topics(quoted)
    golden = {
        (row["topic_id"], row["quarter"], int(row["rank"])): row
        for row in _ydc_evidence()
        if row["topic_id"] in judged
    }
    with connect(quoted) as conn:
        ours = {(row.topic_key, row.quarter, row.rank): row for row in build(conn).rows}
    assert set(ours) == set(golden), sorted(set(ours) ^ set(golden))
    assert len(ours) == LADDER_ROWS
    assert [key for key, row in ours.items() if row.like_count != int(golden[key]["like_count"])] == []


def test_every_row_where_we_pick_another_document_is_a_tie_ydc_could_have_picked_either(quoted: str):
    """갈리는 자리가 동점이 아니면 갈린 것은 동점 처리가 아니라 규칙이다."""
    judged = _judged_topics(quoted)
    golden = {
        (row["topic_id"], row["quarter"], int(row["rank"])): row
        for row in _ydc_evidence()
        if row["topic_id"] in judged
    }
    with connect(quoted) as conn:
        made = build(conn)
    quotable: dict[tuple[str, str], dict[str, int]] = {}
    for candidate in made.candidates:
        if candidate.quality_flags == "":
            quotable.setdefault((candidate.topic_key, candidate.quarter), {})[candidate.doc_id] = (
                candidate.like_count
            )
    differ = []
    for row in made.rows:
        want = golden[(row.topic_key, row.quarter, row.rank)]
        if want["doc_id"] == row.doc_id:
            continue
        differ.append((row.topic_key, row.quarter, row.rank))
        pool = quotable[(row.topic_key, row.quarter)]
        # ydc 가 고른 문서도 이 셀의 후보이고, 같은 좋아요를 가졌다 -- 즉 동점의 다른 승자다.
        assert pool.get(want["doc_id"]) == row.like_count, (row, want["doc_id"])
    assert len(differ) == TIED_ROWS_THAT_DIFFER, differ


def test_the_matched_term_is_the_one_the_corpus_already_recorded(quoted: str):
    """다시 매칭하면 지표가 센 언급과 근거가 고른 언급이 다른 규칙 위에 선다."""
    golden = {row["doc_id"] + "|" + row["topic_id"]: row["matched_term"] for row in _ydc_evidence()}
    with connect(quoted) as conn:
        rows = build(conn).rows
    # 동점 자리에서 우리가 다른 문서를 골랐으면 ydc 의 CSV 에 그 (문서, 주제) 가 없다. 겹치는 자리만
    # 대조하고, 그 자리가 대다수라는 것도 함께 본다 -- 겹침이 무너지면 갈린 것은 동점 처리가 아니다.
    shared = [row for row in rows if f"{row.doc_id}|{row.topic_key}" in golden]
    assert len(shared) == LADDER_ROWS - DOCS_YDC_DID_NOT_PICK
    for row in shared:
        assert row.matched_term == golden[f"{row.doc_id}|{row.topic_key}"]


def test_the_cards_are_the_cards_ydc_cards_py_makes(quoted: str):
    """유형·배정 근거·표의 숫자·한계까지. 규칙이 유형을 정한다는 문장이 여기서 값으로 확인된다."""
    golden = _ydc_cards()
    differences: list[str] = []
    for quarter, wanted in golden.items():
        with connect(quoted) as conn:
            made = collect(conn, quarter).cards
        if [c["topic_id"] for c in wanted] != [c.topic_key for c in made]:
            differences.append(
                f"{quarter}: ydc {[c['topic_id'] for c in wanted]} != {[c.topic_key for c in made]}"
            )
            continue
        for want, card in zip(wanted, made, strict=True):
            for column, got in (
                ("card_type", card.card_type),
                ("type_basis", card.type_basis),
                ("comment_type", card.comment_type),
                ("video_type", card.video_type),
                ("comment_composition_pct", card.comment_composition_pct),
                ("video_composition_pct", card.video_composition_pct),
                ("gap_pp", card.gap_pp),
                ("opportunity_score", card.opportunity_score),
                ("velocity_yoy", card.velocity_yoy),
                ("evidence_strength", card.evidence_strength),
                ("document_count", card.mentions),
            ):
                expected = want[column]
                if isinstance(got, float | int) and not isinstance(got, bool):
                    expected = float(expected) if expected not in ("", None) else None
                    got = float(got)
                elif expected == "":
                    expected = None
                if expected != got:
                    differences.append(
                        f"{quarter} {card.topic_key} {column}: ydc {want[column]!r} != {got!r}"
                    )
            if list(want["limits"]) != list(card.limits):
                differences.append(
                    f"{quarter} {card.topic_key} limits: {want['limits']} != {list(card.limits)}"
                )
    assert not differences, differences[:20]


def test_the_quotes_on_a_card_are_the_quotes_ydc_put_there(quoted: str):
    """카드가 인용하는 순서는 저장된 자리(좋아요)가 아니라 (별칭 구체성, 좋아요) 다 -- 이 표본에 그 둘이
    실제로 갈리는 카드가 있다(`자극_눈시림` 2026Q2 의 3위는 좋아요 11 로 1·2위보다 많다).

    좋아요 사다리는 네 카드 모두에서 같고, 발화 자체는 **한 카드에서만** 갈린다 -- 2025Q2 의 좋아요 2
    동점 자리다. 근거 선별이 그 자리를 `doc_id` 로 가르는 것이 계약이므로(§근거) 이것은 어긋남이 아니라
    적어 둘 사실이고, 갈리는 카드가 하나뿐이라는 것을 여기서 못 박는다.
    """
    moved: list[str] = []
    for quarter, wanted in _ydc_cards().items():
        if not wanted:
            continue
        with connect(quoted) as conn:
            made = collect(conn, quarter).cards
        for want, card in zip(wanted, made, strict=True):
            assert [q["like_count"] for q in want["quotes"]] == [str(q.like_count) for q in card.quotes]
            same_terms = [q["matched_term"] for q in want["quotes"]] == [q.matched_term for q in card.quotes]
            same_text = [" ".join(q["text"].split())[:60] for q in want["quotes"]] == [
                " ".join(q.text.split())[:60] for q in card.quotes
            ]
            if not (same_terms and same_text):
                moved.append(f"{quarter} {card.topic_key}")
    assert moved == ["2025Q2 자극_눈시림"], moved


def test_the_run_carries_all_three_definition_versions(quoted: str):
    """지표·판정·근거가 같은 run 에 산다 (A19: 파생 표는 `*_version` 컬럼을 갖지 않는다)."""
    with connect(quoted) as conn, conn.cursor() as cur:
        cur.execute("SELECT versions FROM analysis_run ORDER BY run_id DESC LIMIT 1")
        found = cur.fetchone()
        assert found is not None
        assert found[0] == {"metric": "v0.2", "judgement": "v0.2", "evidence": "rule-v0.1"}


def test_the_sample_sized_table_passes_the_two_invariants(quoted: str):
    with connect(quoted) as conn, conn.cursor() as cur:
        cur.execute("SELECT violation, quarter, detail FROM topic_quarter_evidence_violation")
        assert cur.fetchall() == []
