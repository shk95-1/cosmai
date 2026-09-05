"""승격이 값을 바꾸지 않았음을 기계가 말한다: ydc `trend.py` 출력과 우리 산출의 1:1 골든 (포크 #5).

#3's grade A review left a question -- "is §Formulas a transcription of ydc or a description of it" -- that a
document cannot answer. The answer is **whether two implementations produce the same numbers from the same
snapshot**, and this file is that comparison.

The fixed input is one set, `tests/fixtures/trend_sample/`. The whole 2026-08-19 corpus (261,317
rows, 174M) lives in `archive/`, which is read-only, so the repository keeps a representative slice.

The cut is 18 channels of that corpus -- 11 `product` and 7 `expert` -- and it is closed at the
channel, so every ydc script run on the same sliced run answers the sample exactly. #57 re-cut it
(11 channels -> 18, product 4 -> 11) because the four-product cut could not reach five branches that
fire in the full population; `tool/measure-trend-sample` is the command that made this fixture and all
seven golden files at once, and its docstring carries the recipe:

    tool/measure-trend-sample --ydc <ydc checkout> --runs <run dirs> --channels <ids> \
        --out tests/fixtures/trend_sample

전량 대조는 워커가 한 번 돌려 보고했다(2026-08-26): 338행 × 13열 전부 일치, 차이 0.

The `expert` channels are in the corpus for `tests/test_sensitivity_golden.py`: the reporting
population is `panel_role='product'` only (corpus rule 5), so those documents reach no row of this
golden, and the panel sensitivity has nothing to measure unless both roles are in the sample.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from analysis.retrieval import topics as topic_registry
from analysis.trend import DIGITS, METRIC_VERSION, diffusion
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


def test_the_sample_has_a_cell_where_one_channel_dominates_the_topic(sampled: str):
    """#57's gap 2. `channel_diffusion` is breadth and evenness in halves (the formula section of
    `contracts/interfaces.md`), and on a cut where
    every (topic, quarter) is one video per channel the evenness half is 1 on every row -- so half the
    formula is a constant and the golden cannot tell it from a formula that never runs. This asks for
    the row where it is not a constant: a video cell whose diffusion is below what the same channel
    count spread evenly would give."""
    with connect(sampled) as conn:
        made = build(conn)
    uneven = []
    for row in made.rows:
        count, denom, value = row.channel_count, row.denom_channels, row.channel_diffusion
        if row.source != "youtube_video" or count is None or count < 2 or not denom or value is None:
            continue
        # The evenly-spread reference goes through the same rounding as the stored value.
        even = round(diffusion({str(i): 1 for i in range(count)}, denom), DIGITS["channel_diffusion"])
        if float(value) < even:
            uneven.append((row.topic_key, row.quarter, count, value, even))
    assert uneven, "every channel distribution in this sample is uniform; the entropy term says nothing"


def test_the_sample_sized_table_still_passes_the_two_invariants(sampled: str):
    with connect(sampled) as conn:
        outcome = run(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT violation, quarter, detail FROM metrics_topic_quarter_violation")
            assert cur.fetchall() == []
    assert outcome.written == len(_golden())
    assert outcome.violations == []
