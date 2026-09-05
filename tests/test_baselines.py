"""The baseline constants are a copy of the table in contracts/interfaces.md -- the table is parsed and the
numbers compared (T10/T11), and the gate those numbers build is checked against the very rules that made the
baseline."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from analysis.baselines import BASELINES, RULE_MEASURED, adoption_misses, for_task, meets
from analysis.registry import TASKS

TABLE_HEADER = "| task | 평가셋 |"
INTERFACES = Path(__file__).resolve().parents[1] / "contracts" / "interfaces.md"
# 표의 채택 조건은 하네스가 내는 지표 키를 그대로 쓴다: `acc ≥ .77 그리고 P:불만 ≥ .89`.
CHECK = re.compile(r"([^\s|]+)\s*≥\s*(\d*\.\d+)")
# §규칙 실측 표: `acc .870 · P:불만 .915`.
MEASURED_HEADER = "| 평가셋 | 규칙 실측 |"
MEASURED = re.compile(r"([^\s|·]+)\s+(\d*\.\d+)")


def _written(number: str) -> str:
    """Compared as text so the decimal places written in the table are compared too -- `.870` shortened to
    `.87` changes the resolution of the gate."""
    return str(Decimal(number))


def _table_rows() -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    start = lines.index(TABLE_HEADER + " 규칙 기준선 | 채택 조건 (단일 임계값) |") + 2
    rows = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append((cells[0].split()[0], tuple((name, _written(n)) for name, n in CHECK.findall(cells[3]))))
    return rows


def test_the_table_and_the_constants_carry_the_same_checks_in_the_same_order():
    parsed = _table_rows()
    assert len(parsed) == 5
    assert parsed == [(b.task, tuple((c.metric, str(c.threshold)) for c in b.checks)) for b in BASELINES]


def test_every_task_the_cli_offers_has_at_least_one_eval_set():
    assert all(for_task(task) for task in TASKS)


def test_an_eval_set_selects_rows_from_the_database_alone():
    for eval_set in BASELINES:
        assert bool(eval_set.extra_key) == bool(eval_set.extra_value), eval_set.name
        assert not (eval_set.extra_key and eval_set.ref_prefix), eval_set.name
    wish = next(b for b in BASELINES if b.task == "wish_class")
    assert (wish.extra_key, wish.extra_value) == ("set", "blind60_v2")


def _measured_rows() -> dict[str, dict[str, str]]:
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    start = lines.index(MEASURED_HEADER) + 2
    out: dict[str, dict[str, str]] = {}
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        out[cells[0]] = {name: _written(n) for name, n in MEASURED.findall(cells[1])}
    return out


def test_the_rule_measured_table_and_the_constant_carry_the_same_numbers():
    """The replacement condition is this table -- constants alone go stale quietly the day #3 changes the
    rules (fix round 1, I-5)."""
    parsed = _measured_rows()
    assert parsed == {
        "sun holdout 100": {"acc": "0.870", "P:불만": "0.915"},
        "p1 blind40": {"acc": "0.475", "P:불만": "0.667"},
    }
    assert parsed == {
        name: {metric: str(want) for metric, want in wants.items()}
        for name, wants in RULE_MEASURED["polarity"].items()
    }


def test_every_measured_set_is_one_of_the_contract_baseline_sets():
    names = {b.name for b in BASELINES}
    assert set(RULE_MEASURED["polarity"]) <= names


# The **raw values** the rule implementations (#3/#4) produced on the holdout. The numbers in the table are
# the rounded form of these fractions -- they are written as fractions so the next person does not rebuild a
# rounding artefact from the decimal form alone.
RULE_RAW: dict[tuple[str, str], float] = {
    ("sun holdout 100", "acc"): 87 / 100,
    ("sun holdout 100", "P:불만"): 43 / 47,
    ("p1 blind40", "acc"): 19 / 40,
    ("p1 blind40", "P:불만"): 2 / 3,
    ("P3 120", "P:OK"): 115 / 116,
    ("P2 blind 40", "strict"): 10 / 13,
}
# wish 규칙은 블라인드 셋에서 **실제로** 진다 (P:a 14/23). 반올림이 아니라 성능이라 여기서 고치지 않는다.
KNOWN_MISS = ("P9 blind60_v2", "P:a")


def test_every_baseline_check_except_the_known_wish_miss_has_a_measured_rule_number():
    """A missing line in RULE_RAW makes the two checks below skip that cell quietly -- the omission is caught
    here."""
    covered = {(b.name, c.metric) for b in BASELINES for c in b.checks}
    assert covered - set(RULE_RAW) == {KNOWN_MISS}


def test_the_rule_that_set_the_baselines_passes_them():
    """If the implementation that made the baseline loses to its own gate, that is a rounding artefact rather
    than a baseline."""
    missed = [
        f"{b.name}: {c.metric} {RULE_RAW[(b.name, c.metric)]!r} < {c.threshold}"
        for b in BASELINES
        for c in b.checks
        if (b.name, c.metric) != KNOWN_MISS and not meets(RULE_RAW[(b.name, c.metric)], c.threshold)
    ]
    assert missed == []


def test_the_rule_measured_bar_is_met_by_the_raw_numbers_it_was_rounded_from():
    """The §Rule measurement table is the rules' own score — with its raw value lost from the table, no
    implementation could ever be judged a replacement."""
    scores = {
        name: {metric: RULE_RAW[(name, metric)] for metric in wants}
        for name, wants in RULE_MEASURED["polarity"].items()
    }
    assert adoption_misses("polarity", scores) == ()


@pytest.mark.postgres
def test_the_rule_implementations_pass_check_baseline_on_the_holdout_sets(
    needs_runtime_url: str, monkeypatch: pytest.MonkeyPatch, capsys
):
    """RULE_RAW is a number copied by hand -- the real implementation is run to pin down that the number is
    still reality."""
    from analysis import predictors
    from cosmai.cli import main
    from db import seed

    seed.run_all(needs_runtime_url, only=("lexicon", "labeled"))
    # The Predictor contract hands over no connection, so the implementation opens the dictionary connection
    # itself -- turning that one destination brings all four predictors with it
    # (tests/test_eval_lexicon_url.py keeps that property).
    monkeypatch.setattr(predictors, "LEXICON_URL", needs_runtime_url)
    gate = ["--url", needs_runtime_url, "--split", "holdout", "--check-baseline"]
    for task in ("polarity", "brand_link", "product_match"):
        assert main(["eval", task, *gate]) == 0, f"{task}: {capsys.readouterr().out}"
    # wish 규칙은 blind60_v2 에서 실제로 진다 (KNOWN_MISS) — 반올림이 아니라 성능이라 여기서 안 고친다.
    assert main(["eval", "wish_class", *gate]) == 1
    assert "P9 blind60_v2: P:a" in capsys.readouterr().out


def test_a_threshold_is_read_at_the_precision_it_is_written_to():
    """`.67` is two places -- reading .667, measured to three, as short of it puts the gate out of step with
    its own source. Written to more places it is that much finer: this is not a relaxation but matching the
    notation and the comparison to one ruler."""
    assert meets(2 / 3, Decimal(".67"))
    assert not meets(0.6649, Decimal(".67"))
    assert meets(0.76851, Decimal(".769"))
    assert not meets(0.7684, Decimal(".769"))
