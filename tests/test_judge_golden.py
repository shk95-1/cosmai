"""승격이 판정을 바꾸지 않았음을 기계가 말한다: ydc `judge.py` 출력과 우리 판정의 1:1 골든 (포크 #40).

#5 의 골든이 지표에서 한 일을 판정에서 한다. 고정 입력은 같은 표본 한 벌
(`tests/fixtures/trend_sample/`)이고, 골든은 ydc 를 **손대지 않고** 그 표본의 지표 CSV 에 돌려 얻었다:

    python judge.py tests/fixtures/trend_sample/ydc_trend_v0.2.csv --out .../ydc_judgement_v0.2.csv

즉 이 파일이 대조하는 사슬은 넷이다 -- 픽스처 코퍼스 → 우리 `metrics_topic_quarter`(#5 골든) →
우리 판정 → ydc 판정. 전량(338행) 대조는 워커가 일회용 컨테이너에서 한 번 돌려 보고했다(2026-08-26).

**표본이 못 보는 자리**: 이 표본에서 영상 쪽은 전부 `근거 부족`·`미확정` 이고, `지속 인기` 는 한 셀도
없다. `hold_reason` 도 넷 중 둘만 나온다. 그 갈래들은 `tests/test_judge_rules.py` 가 홀로 진다 --
표본을 다시 자를 일이 있으면 근거가 두꺼운 영상 셀이 든 (주제, 분기) 를 넣어라.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from analysis.judge import (
    ABOVE_HALF_PEAK,
    DIFFUSION_TAU,
    NO_PRIOR_YEAR,
    NO_RULE,
    TAU,
    WITHIN_TAU_SHORT_PERSISTENCE,
)
from analysis.judge.pipeline import build, run
from analysis.retrieval import topics as topic_registry
from analysis.trend import METRIC_VERSION
from analysis.trend.pipeline import build as build_quarter
from analysis.trend.pipeline import run as run_quarter
from cosmai.cli import main
from db import corpus, seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "trend_sample"
VIEWS = (
    ROOT / "db" / "views" / "metrics_topic_quarter_violation.sql",
    ROOT / "db" / "views" / "topic_quarter_judgement_violation.sql",
)
OWNER = text("SET ROLE needs_owner")
KEYS = ("quarter", "topic_id", "source")
# ydc 열 -> 우리 컬럼. 지표를 되받아 적은 열들은 test_trend_golden.py 가 이미 대조하므로 여기서 빼고,
# 어느 열도 조용히 빠지지 않게 아래 test_the_golden_names_every_column_this_test_compares 가 센다.
COLUMNS = {
    "evidence_strength": "evidence_strength",
    "opportunity_score": "opportunity_score",
    "trend_type": "trend_type",
    "gap_pp": "gap_pp",
    "judged": "judged",
    "single_source": "single_source",
}
# 판정이 아니라 지표의 값이라 #5 의 골든이 진다.
ECHOED = {
    "document_count", "composition", "velocity_yoy", "persistence", "persistence_count",
    "channel_diffusion", "unique_ratio", "metric_version",
}  # fmt: skip
# ydc 는 임계값을 행마다 컬럼으로 적고 우리는 run 의 versions 에 든다 (A19).
THRESHOLDS = {"tau": TAU, "diffusion_tau": DIFFUSION_TAU}
# ydc 는 사유를 사람이 읽는 한 문장으로 적는다. 앞 조각이 사유이고, `above_half_peak` 의 괄호 안 수는
# 같은 run 의 지표 행에서 다시 나오는 파생이라 우리는 저장하지 않는다 (계약 §판정).
REASONS = {
    "": "",
    "전년 동분기 표본 부족": NO_PRIOR_YEAR,
    "변화가 tau 이내이나 지속성 부족": WITHIN_TAU_SHORT_PERSISTENCE,
    "규칙 미해당": NO_RULE,
}


def _golden() -> list[dict[str, str]]:
    with (FIXTURE / "ydc_judgement_v0.2.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _reason(sentence: str) -> str:
    return ABOVE_HALF_PEAK if sentence.startswith("하락하지만 최고 분기(") else REASONS[sentence]


def _install_registry(url: str) -> None:
    where = ["--kind", "aspect", "--version", "1", "--url", url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0


@pytest.fixture
def sampled(needs_schema: str, needs_runtime_url: str, _schema_name: str) -> str:
    engine = create_engine(needs_schema)
    try:
        with engine.begin() as conn:
            conn.execute(OWNER)
            for view in VIEWS:
                conn.exec_driver_sql(view.read_text(encoding="utf-8").replace("needs.", f'"{_schema_name}".'))
    finally:
        engine.dispose()
    seed.run_all(needs_runtime_url, only=("panel",))
    _install_registry(needs_runtime_url)
    with connect(needs_runtime_url) as conn:
        corpus.load(conn, FIXTURE / "corpus")
        run_quarter(conn)
    return needs_runtime_url


def test_the_golden_names_every_column_this_test_compares():
    """비교하지 않는 열이 골든에 남아 있으면 그 열은 조용히 아무 말도 하지 않는다."""
    assert set(_golden()[0]) == set(KEYS) | set(COLUMNS) | ECHOED | set(THRESHOLDS) | {"hold_reason"}


def test_the_thresholds_ydc_stamps_on_every_row_are_the_constants_we_pin():
    for column, value in THRESHOLDS.items():
        assert {row[column] for row in _golden()} == {str(value)}


def test_our_judgement_is_the_judgement_ydc_judge_py_writes(sampled: str):
    """같은 지표 행에서 나온 판정이므로 원칙적으로 같아야 한다 -- 다르면 어디서 갈리는지가 답이다."""
    with connect(sampled) as conn:
        made = build(conn)
    ours = {(row.quarter, row.topic_key, row.source): row for row in made.rows}
    golden = {(row["quarter"], row["topic_id"], row["source"]): row for row in _golden()}
    assert set(ours) == set(golden), sorted(set(ours) ^ set(golden))

    differences: list[str] = []
    for key, want in golden.items():
        row = ours[key]
        for column, attribute in COLUMNS.items():
            got = getattr(row, attribute)
            expected: Any
            if attribute in ("judged", "single_source"):
                expected = want[column] == "true"
            elif attribute == "trend_type":
                expected = want[column]
            elif want[column] == "":
                expected = None
            else:
                expected = float(want[column])
            if expected != got:
                differences.append(f"{key} {column}: ydc {want[column]!r} != {got!r}")
        if _reason(want["hold_reason"]) != row.hold_reason:
            differences.append(f"{key} hold_reason: ydc {want['hold_reason']!r} != {row.hold_reason!r}")
    assert not differences, differences[:20]


def test_the_peak_ydc_writes_into_the_hold_reason_is_the_peak_of_our_metric_rows(sampled: str):
    """사유의 괄호 안 수를 저장하지 않는 대신, 그 수가 지표 행에서 그대로 나오는지 여기서 본다 --
    빼기만 하면 골든이 그 자리에서 눈을 감는다."""
    with connect(sampled) as conn:
        metrics = build_quarter(conn).rows
    peaks: dict[tuple[str, str], float] = {}
    for row in metrics:
        key = (row.topic_key, row.source)
        peaks[key] = max(peaks.get(key, 0.0), float(row.composition or 0.0))
    checked = 0
    for want in _golden():
        if not want["hold_reason"].startswith("하락하지만 최고 분기("):
            continue
        peak = peaks[(want["topic_id"], want["source"])]
        assert f"최고 분기({peak * 100:.1f}%)" in want["hold_reason"]
        checked += 1
    # 이 표본에는 그 사유가 없다. 전량에서는 3셀이고, 그 자리는 tests/test_judge_rules.py 가 진다.
    assert checked == 0


def test_the_judgement_version_lands_on_the_run_the_metrics_already_carry(sampled: str):
    """ydc 는 행마다 컬럼으로, 우리는 같은 run 의 `versions` 두 키로 든다 (A19)."""
    with connect(sampled) as conn:
        outcome = run(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT versions->>'metric', versions->>'judgement' FROM analysis_run WHERE run_id = %s",
                (outcome.run_id,),
            )
            assert cur.fetchone() == (METRIC_VERSION, "v0.2")
    assert {row["metric_version"] for row in _golden()} == {METRIC_VERSION}


def test_the_sample_sized_table_still_passes_the_two_invariants(sampled: str):
    with connect(sampled) as conn:
        outcome = run(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT violation, quarter, detail FROM topic_quarter_judgement_violation")
            assert cur.fetchall() == []
    assert outcome.written == len(_golden())
    assert outcome.violations == []
