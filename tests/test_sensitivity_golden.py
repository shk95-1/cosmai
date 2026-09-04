"""승격이 답을 바꾸지 않았음을 기계가 말한다: ydc 세 스크립트 출력과 우리 민감도의 1:1 골든 (포크 #41).

#5 의 골든이 지표에서, #40 이 판정에서 한 일을 여기서 한다. 고정 입력은 같은 표본 한 벌
(`tests/fixtures/trend_sample/`)이고, 골든은 ydc 를 **손대지 않고** 그 표본과 같은 채널로 잘라 낸 run
디렉터리에 돌려 얻었다:

    python panel_sensitivity.py <sliced run> --out .../ydc_panel_sensitivity_v0.2.csv
    python backtest.py         <sliced run> --out .../ydc_backtest_v0.2.csv
    python spam_ad_flags.py    <sliced run> --out .../ydc_spam_ad_v0.2.csv

표본에 expert 채널 7개가 들어 있는 것이 이 파일 때문이다(#41 이 더했다). 판정·보고 모집단은 product
뿐이라 그 채널들은 `metrics_topic_quarter` 의 어느 행에도 들지 않지만, 패널 민감도는 product 만인 산출과
43채널 전부인 산출을 갈라 봐야 하고 표본이 전부 product 면 두 산출이 같은 값이 되어 **아무 말도 하지
않는다.**

**What the sample reaches.** #57 re-cut it (11 channels -> 18, product 4 -> 11) and paid the cost that
kept #41 from doing it: seven golden files remade at once (#5 quarterly metrics · #40 verdicts · #41's
three · #6's evidence and cards). Both branches this file used to assert away are live now -- four
cells stand with `sample_ok` and one of them flips, and the promo variant drops comments that carry a
topic, so its rows move instead of copying the base.

**전량 대조는 CI 가 못 한다** -- 261,317문서는 `archive/` 에 있고 그 자리는 읽기 전용이다. 사람이 한 번
돌리는 절차와 대조 코드는 `tool/compare-ydc-sensitivity` 한 자리에 있다(2026-08-26 실행: 패널 26행 ·
후향 11행 · 표시 104행, 차이 0).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from analysis import sensitivity
from analysis.retrieval import topics as topic_registry
from analysis.sensitivity.pipeline import build
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
# ydc panel_sensitivity.csv 는 source 를 `video`·`comment` 로 적고 backtest.csv 는 `youtube_*` 로 적는다.
# 이 레포의 어휘는 하나다(022 의 CHECK) -- 그 하나로 옮겨 맞댄다.
SOURCE = {"video": "youtube_video", "comment": "youtube_comment"}
# ydc 는 변형을 사람이 읽는 라벨로 적는다. 어느 쪽도 다른 쪽의 번역이 아니라 같은 넷의 두 이름이다.
VARIANT = {
    "광고·협찬 영상 제외": sensitivity.AD_VIDEO,
    "운영자 댓글 제외": sensitivity.CREATOR_COMMENT,
    "홍보 댓글 제외": sensitivity.PROMO_COMMENT,
    "전부 제외": sensitivity.ALL_FLAGGED,
}


def _golden(name: str) -> list[dict[str, str]]:
    with (FIXTURE / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


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


@pytest.fixture
def built(sampled: str):
    with connect(sampled) as conn:
        return build(conn)


def test_the_recount_is_the_table_cosmai_trend_quarter_already_wrote(built):
    """세 측정의 차이가 뜻을 가지려면 기저와 변형이 같은 코드 경로에서 나와야 한다. 그 기저가 저장된
    행과 다르면 차이는 전부 무의미하므로, 그 사실이 먼저 나온다."""
    assert built.violations == ()


def test_our_panel_sensitivity_is_the_one_ydc_panel_sensitivity_py_writes(built):
    golden = {(SOURCE[r["source"]], r["topic_id"]): r for r in _golden("ydc_panel_sensitivity_v0.2.csv")}
    ours = {(row.source, row.topic_key): row for row in built.panel}
    assert set(ours) == set(golden), sorted(set(ours) ^ set(golden))
    differences = [
        f"{key} {column}: ydc {want[column]!r} != {got!r}"
        for key, want in golden.items()
        for column, got in (
            ("quarters_ok_product", ours[key].quarters_ok_product),
            ("quarters_ok_all", ours[key].quarters_ok_all),
            ("delta_product_pp", ours[key].delta_product_pp),
            ("delta_all_pp", ours[key].delta_all_pp),
            ("difference_pp", ours[key].difference_pp),
        )
        if _same(want[column], got) is False
    ]
    assert not differences, differences[:20]


def test_the_expert_channels_actually_move_the_panel_answer(built):
    """표본이 전부 product 면 두 산출이 같은 값이 되어 이 측정이 아무 말도 하지 않는다 -- 그 자리를
    표본이 실제로 열었는지 여기서 붙든다."""
    assert any(row.difference_pp for row in built.panel)
    assert any(row.quarters_ok_all != row.quarters_ok_product for row in built.panel)


def test_our_backtest_is_the_one_ydc_backtest_py_writes(built):
    golden = {(r["cutoff"], r["source"], r["topic_id"]): r for r in _golden("ydc_backtest_v0.2.csv")}
    ours = {(row.cutoff, row.source, row.topic_key): row for row in built.back.rows}
    assert set(ours) == set(golden), sorted(set(ours) ^ set(golden))
    # 사례가 둘 미만이면 후향 검증이라고 부를 것이 없다 (기획안 "사례 2건 이상").
    assert len(ours) >= 2
    differences = [
        f"{key} {column}: ydc {want[column]!r} != {got!r}"
        for key, want in golden.items()
        for column, got in (
            ("trend_type", ours[key].trend_type),
            ("before_pp", ours[key].before_pp),
            ("before_excl_pp", ours[key].before_excl_pp),
            ("after_pp", ours[key].after_pp),
            ("at_cutoff_pp", ours[key].at_cutoff_pp),
            ("expected", ours[key].expected),
            ("actual", ours[key].actual),
            ("hit", ours[key].hit),
            ("hit_level", ours[key].hit_level),
        )
        if _same(want[column], got) is False
    ]
    assert not differences, differences[:20]


def test_the_backtest_sample_carries_both_a_hit_and_a_miss(built):
    """적중만 있거나 실패만 있는 표본에서는 기준 A·B 의 갈림이 코드에서 오는지 데이터에서 오는지 모른다."""
    assert {row.hit for row in built.back.rows} == {True, False}
    assert {row.trend_type for row in built.back.rows} >= {"급상승", "사라짐"}


def test_our_ad_sensitivity_is_the_one_ydc_spam_ad_flags_py_writes(built):
    golden = {(VARIANT[r["variant"]], r["source"], r["topic_id"]): r for r in _golden("ydc_spam_ad_v0.2.csv")}
    ours = {(row.variant, row.source, row.topic_key): row for row in built.ad.rows}
    assert set(ours) == set(golden), sorted(set(ours) ^ set(golden))
    differences = [
        f"{key} {column}: ydc {want[column]!r} != {got!r}"
        for key, want in golden.items()
        for column, got in (
            ("composition_base_pp", ours[key].composition_base_pp),
            ("composition_kept_pp", ours[key].composition_kept_pp),
            ("diff_pp", ours[key].diff_pp),
            ("judged_cells", ours[key].judged_cells),
            ("flipped_cells", ours[key].flipped_cells),
        )
        if _same(want[column], got) is False
    ]
    assert not differences, differences[:20]


def test_the_three_flags_are_not_all_empty_in_this_sample(built):
    """표시가 0건이면 "빼도 결론이 같다"가 아무것도 뺀 적 없다는 말이 된다."""
    assert built.ad.ad_videos > 0
    assert built.ad.creator_comments > 0
    # #57's gap 5. While this was 0 the promo variant's rows were a copy of the base, so "excluding
    # them changes nothing" was a sentence about an empty exclusion.
    assert built.ad.promo_comments > 0
    moved = [row for row in built.ad.rows if row.variant == sensitivity.PROMO_COMMENT and row.diff_pp]
    assert moved, "promo comments are dropped but no composition moves; they carry no judged topic"


def test_the_flip_verdict_runs_on_this_sample_and_reaches_a_cell(built):
    """#57's gap 4. `flipped` filters on `sample_ok` first, so while no cell was judgeable the other
    two conditions -- opposite signs, and a move of at least `MATERIAL_PP` -- were never evaluated and
    the measurement idled. Both halves are asked here: that cells stand, and that the verdict is the
    one those three conditions give on them."""
    standing = [row for row in built.panel if row.sample_ok]
    assert standing, "no cell is judgeable, so the flip verdict runs zero times again"
    expected = [
        row
        for row in standing
        if row.delta_product_pp * row.delta_all_pp < 0
        and max(abs(row.delta_product_pp), abs(row.delta_all_pp)) >= sensitivity.MATERIAL_PP
    ]
    assert expected, "no standing cell flips, so the verdict is again only its own first filter"
    assert built.flipped == expected


def _same(want: str, got: object) -> bool:
    if isinstance(got, bool):
        return want == ("true" if got else "false")
    if isinstance(got, float):
        return abs(float(want) - got) < 1e-9
    if isinstance(got, int):
        return int(want) == got
    return want == got
