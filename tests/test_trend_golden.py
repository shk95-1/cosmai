"""승격이 값을 바꾸지 않았음을 기계가 말한다: ydc `trend.py` 출력과 우리 산출의 1:1 골든 (포크 #5).

#3 등급 A 리뷰가 남긴 질문 -- "§수식이 ydc 를 옮겨 적은 것인지 서술한 것인지" -- 은 문서로는 답할 수
없다. 답은 **같은 스냅샷에서 두 구현이 같은 수를 내는가**이고, 이 파일이 그 대조다.

고정 입력은 `tests/fixtures/trend_sample/` 한 벌이다. 2026-08-19 코퍼스 전량(261,317행 · 174M)은
`archive/` 에 있고 그 자리는 수정 금지이므로 레포에는 **대표 표본**만 둔다 -- 채널 4개로 잘라 모집단이
닫힌 표본이고, 두 파일은 같은 잘린 run 에서 각각 이렇게 나왔다:

    python to_common_schema.py <잘린 run> --out corpus              # → corpus/*
    python trend.py <잘린 run> --panel eval/panel/channels_v1.csv   # → ydc_trend_v0.2.csv

전량 대조는 워커가 한 번 돌려 보고했다(2026-08-26): 338행 × 13열 전부 일치, 차이 0.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from analysis.retrieval import topics as topic_registry
from analysis.trend import METRIC_VERSION
from analysis.trend.pipeline import SCOPE, build, run
from cosmai.cli import main
from db import corpus, seed
from db.corpus import verify
from db.seed._common import connect

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "trend_sample"
VIEW = ROOT / "db" / "views" / "metrics_topic_quarter_violation.sql"
OWNER = text("SET ROLE needs_owner")

# ydc CSV 열 -> 우리 컬럼. `category`·`metric_version` 은 따로 본다 (아래 두 테스트).
COLUMNS = {
    "document_count": "mentions",
    "quarter_documents": "documents",
    "quarter_mentions": "quarter_mentions",
    "composition": "composition",
    "velocity_yoy": "velocity_yoy",
    "persistence": "persistence",
    "persistence_count": "persist_quarters",
    "window_quarters": "window_quarters",
    "unique_ratio": "unique_ratio",
    "channel_count": "channel_count",
    "panel_channels": "denom_channels",
    "channel_diffusion": "channel_diffusion",
    "sample_ok": "sample_ok",
}
KEYS = ("quarter", "topic_id", "source", "content_type")
INTEGERS = frozenset(
    {"mentions", "documents", "quarter_mentions", "persist_quarters", "window_quarters",
     "channel_count", "denom_channels"}
)  # fmt: skip
# ydc 의 `category` 는 주제 사전의 이름이고 우리 `scope` 는 metrics_need.scope 와 같은 어휘다.
YDC_CATEGORY = "선크림"


def _key(row: dict[str, str]) -> tuple[str, str, str, str]:
    quarter, topic, source, content_type = (row[name] for name in KEYS)
    return quarter, topic, source, content_type


def _install_registry(url: str) -> None:
    """주제 축의 레지스트리를 세우는 길은 운영과 같은 하나다 -- 픽스처가 사전을 손으로 다시 적으면
    축이 두 벌이 되고, 그때부터 이 테스트는 자기 사본을 검사한다."""
    where = ["--kind", "aspect", "--version", "1", "--url", url]
    assert main(["lexicon", "load", *where, str(topic_registry.DICTIONARY_CSV)]) == 0
    assert main(["lexicon", "activate", *where]) == 0


def _golden() -> list[dict[str, str]]:
    with (FIXTURE / "ydc_trend_v0.2.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture
def sampled(needs_schema: str, needs_runtime_url: str, _schema_name: str) -> str:
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
        corpus.load(conn, FIXTURE / "corpus")
    return needs_runtime_url


def test_the_fixture_corpus_is_the_population_its_manifest_claims(sampled: str):
    """표본을 다시 자르면 이 세 숫자가 먼저 움직인다 -- 골든이 무엇 위에 서 있는지가 여기 있다."""
    manifest = json.loads((FIXTURE / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    with connect(sampled) as conn:
        counted = verify.reproduce(conn)
    assert counted == {k: v for k, v in manifest["reproduces"].items() if isinstance(v, int)}


def test_the_golden_names_every_column_this_test_compares():
    """비교하지 않는 열이 골든에 남아 있으면 그 열은 조용히 아무 말도 하지 않는다."""
    header = set(_golden()[0])
    assert header == set(KEYS) | set(COLUMNS) | {"category", "metric_version"}


def test_our_rows_are_the_rows_ydc_trend_py_writes(sampled: str):
    """같은 스냅샷에서 나온 값이므로 원칙적으로 같아야 한다 -- 다르면 어디서 갈리는지가 답이다."""
    with connect(sampled) as conn:
        made = build(conn)
    ours = {(r.quarter, r.topic_key, r.source, r.content_type): r for r in made.rows}
    golden = {_key(row): row for row in _golden()}
    assert set(ours) == set(golden), sorted(set(ours) ^ set(golden))

    differences: list[str] = []
    for key, want in golden.items():
        row = ours[key]
        for column, attribute in COLUMNS.items():
            got = getattr(row, attribute)
            expected: Any
            if attribute == "sample_ok":
                expected = want[column] == "True"
            elif want[column] == "":
                expected = None
            elif attribute in INTEGERS:
                expected = int(want[column])
            else:
                expected = float(want[column])
            if expected != got:
                differences.append(f"{key} {column}: ydc {want[column]!r} != {got!r}")
    assert not differences, differences[:20]


def test_the_scope_axis_is_the_category_vocabulary_not_the_topic_dictionary(sampled: str):
    """ydc 의 `category` 는 주제 사전의 `선크림` 이고, 우리 `scope` 는 `metrics_need.scope` 와 같은 어휘다."""
    assert {row["category"] for row in _golden()} == {YDC_CATEGORY}
    with connect(sampled) as conn:
        made = build(conn)
    assert {row.scope for row in made.rows} == {SCOPE}


def test_the_metric_version_ydc_stamps_on_every_row_lands_on_the_run(sampled: str):
    """ydc 는 열로, 우리는 `analysis_run.versions.metric` 으로 든다 (A19: 집계 표는 버전 컬럼이 없다)."""
    assert {row["metric_version"] for row in _golden()} == {METRIC_VERSION}
    with connect(sampled) as conn:
        outcome = run(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT versions->>'metric' FROM analysis_run WHERE run_id = %s", (outcome.run_id,))
            assert cur.fetchone() == (METRIC_VERSION,)


def test_the_sample_sized_table_still_passes_the_two_invariants(sampled: str):
    with connect(sampled) as conn:
        outcome = run(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT violation, quarter, detail FROM metrics_topic_quarter_violation")
            assert cur.fetchall() == []
    assert outcome.written == len(_golden())
    assert outcome.violations == []
