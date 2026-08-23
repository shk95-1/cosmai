"""`cosmai eval <task>`: 구현체가 없으면 멈추고, 있으면 점수 표와 analysis_run 한 행을 남긴다."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest

from analysis.registry import LabeledRow, register, unregister
from cosmai.cli import main
from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres


@pytest.fixture
def labeled(needs_runtime_url: str) -> str:
    seed.run_all(needs_runtime_url, only=("labeled",))
    return needs_runtime_url


@pytest.fixture
def oracle() -> Iterator[None]:
    """골드를 그대로 돌려주는 더미 구현체 — 하네스가 재는 것이 배선이지 규칙이 아님을 분명히 한다."""

    def predict(rows: Sequence[LabeledRow]) -> Sequence[str]:
        return [row.gold for row in rows]

    for task in ("polarity", "wish_class", "brand_link", "product_match"):
        register(task, "oracle-v0", predict)
    yield
    for task in ("polarity", "wish_class", "brand_link", "product_match"):
        unregister(task)


def test_an_unregistered_task_stops_with_exit_code_2(labeled: str, capsys):
    assert main(["eval", "polarity", "--url", labeled]) == 2
    assert "polarity" in capsys.readouterr().out


def test_every_eval_set_of_the_task_is_scored_and_one_run_row_is_written(labeled, oracle, capsys):
    assert main(["eval", "polarity", "--url", labeled]) == 0
    out = capsys.readouterr().out
    assert "sun holdout 100" in out and "p1 blind40" in out
    assert "n=100" in out and "n=40" in out
    with connect(labeled) as conn, conn.cursor() as cur:
        cur.execute("SELECT note, versions FROM analysis_run")
        rows = cur.fetchall()
    assert len(rows) == 1
    note, versions = rows[0]
    assert note == "eval:polarity:oracle-v0"
    assert versions["polarity"] == "oracle-v0"
    assert versions["scores"]["sun holdout 100"]["acc"] == 1.0


def test_product_match_reports_strict_and_variant_tolerant_side_by_side(labeled, oracle, capsys):
    assert main(["eval", "product_match", "--url", labeled]) == 0
    out = capsys.readouterr().out
    assert "strict" in out and "변형허용" in out
    assert "n=40" in out


def test_brand_link_folds_the_reason_carrying_gold_into_ok_and_fp(labeled, oracle, capsys):
    assert main(["eval", "brand_link", "--url", labeled]) == 0
    out = capsys.readouterr().out
    assert "n=120" in out
    assert "OK" in out and "OK(retailer)" not in out


def test_check_baseline_passes_for_the_oracle_and_fails_for_a_constant(labeled, oracle):
    assert main(["eval", "wish_class", "--url", labeled, "--check-baseline"]) == 0
    register("wish_class", "always-n", lambda rows: ["n"] * len(rows))
    assert main(["eval", "wish_class", "--url", labeled, "--check-baseline"]) == 1


def test_a_prediction_of_the_wrong_length_is_refused(labeled, capsys):
    register("polarity", "short", lambda rows: ["중립"])
    try:
        assert main(["eval", "polarity", "--url", labeled]) == 2
    finally:
        unregister("polarity")
    assert "returned 1 prediction(s) for 100 row(s)" in capsys.readouterr().out
