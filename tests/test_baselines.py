"""기준선 상수는 contracts/interfaces.md 표의 사본이다 — 표를 파싱해 숫자를 대조하고(T10/T11),
그 숫자로 만들어진 게이트를 기준선을 만든 규칙 자신이 통과하는지까지 본다."""

from __future__ import annotations

import re
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


def _table_rows() -> list[tuple[str, tuple[tuple[str, float], ...]]]:
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    start = lines.index(TABLE_HEADER + " 규칙 기준선 | 채택 조건 (단일 임계값) |") + 2
    rows = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append((cells[0].split()[0], tuple((name, float(n)) for name, n in CHECK.findall(cells[3]))))
    return rows


def test_the_table_and_the_constants_carry_the_same_checks_in_the_same_order():
    parsed = _table_rows()
    assert len(parsed) == 5
    assert parsed == [(b.task, tuple((c.metric, c.threshold) for c in b.checks)) for b in BASELINES]


def test_every_task_the_cli_offers_has_at_least_one_eval_set():
    assert all(for_task(task) for task in TASKS)


def test_an_eval_set_selects_rows_from_the_database_alone():
    for eval_set in BASELINES:
        assert bool(eval_set.extra_key) == bool(eval_set.extra_value), eval_set.name
        assert not (eval_set.extra_key and eval_set.ref_prefix), eval_set.name
    wish = next(b for b in BASELINES if b.task == "wish_class")
    assert (wish.extra_key, wish.extra_value) == ("set", "blind60_v2")


def _measured_rows() -> dict[str, dict[str, float]]:
    lines = INTERFACES.read_text(encoding="utf-8").splitlines()
    start = lines.index(MEASURED_HEADER) + 2
    out: dict[str, dict[str, float]] = {}
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        out[cells[0]] = {name: float(n) for name, n in MEASURED.findall(cells[1])}
    return out


def test_the_rule_measured_table_and_the_constant_carry_the_same_numbers():
    """교체 조건은 이 표다 — 상수만 있으면 #3 이 규칙을 고쳤을 때 조용히 낡는다 (수정 라운드 1, I-5)."""
    parsed = _measured_rows()
    assert parsed == {
        "sun holdout 100": {"acc": 0.870, "P:불만": 0.915},
        "p1 blind40": {"acc": 0.475, "P:불만": 0.667},
    }
    assert parsed == dict(RULE_MEASURED["polarity"])


def test_every_measured_set_is_one_of_the_contract_baseline_sets():
    names = {b.name for b in BASELINES}
    assert set(RULE_MEASURED["polarity"]) <= names


# 규칙 구현(#3/#4)이 홀드아웃에서 낸 **원값**. 표의 숫자는 이 분수들의 반올림 표기다 — 분수로 적어 두는
# 것은 다음 사람이 소수 표기만 보고 반올림 인공물을 다시 만들지 않게 하려는 것이다.
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
    """RULE_RAW 에서 한 줄이 빠지면 아래 두 검사가 그 칸을 조용히 건너뛴다 — 빠짐을 여기서 잡는다."""
    covered = {(b.name, c.metric) for b in BASELINES for c in b.checks}
    assert covered - set(RULE_RAW) == {KNOWN_MISS}


def test_the_rule_that_set_the_baselines_passes_them():
    """기준선을 만든 구현이 자기 게이트에 지면 그것은 기준이 아니라 반올림 인공물이다."""
    missed = [
        f"{b.name}: {c.metric} {RULE_RAW[(b.name, c.metric)]!r} < {c.threshold}"
        for b in BASELINES
        for c in b.checks
        if (b.name, c.metric) != KNOWN_MISS and not meets(RULE_RAW[(b.name, c.metric)], c.threshold)
    ]
    assert missed == []


def test_the_rule_measured_bar_is_met_by_the_raw_numbers_it_was_rounded_from():
    """§규칙 실측 표는 규칙 자신의 점수다 — 그 원값이 표에 지면 어떤 구현도 교체 판정을 받을 수 없다."""
    scores = {
        name: {metric: RULE_RAW[(name, metric)] for metric in wants}
        for name, wants in RULE_MEASURED["polarity"].items()
    }
    assert adoption_misses("polarity", scores) == ()


@pytest.mark.postgres
def test_the_rule_implementations_pass_check_baseline_on_the_holdout_sets(
    needs_runtime_url: str, monkeypatch: pytest.MonkeyPatch, capsys
):
    """RULE_RAW 는 손으로 옮긴 숫자다 — 실제 구현을 돌려 그 숫자가 아직 현실인지 여기서 못 박는다."""
    from analysis import predictors
    from cosmai.cli import main
    from db import seed

    seed.run_all(needs_runtime_url, only=("lexicon", "labeled"))
    # Predictor 계약이 연결을 주지 않아 구현체가 사전 접속을 스스로 연다 (tests/test_polarity.py 와 같다).
    monkeypatch.setattr(predictors, "LEXICON_URL", needs_runtime_url)
    gate = ["--url", needs_runtime_url, "--split", "holdout", "--check-baseline"]
    for task in ("polarity", "brand_link", "product_match"):
        assert main(["eval", task, *gate]) == 0, f"{task}: {capsys.readouterr().out}"
    assert main(["eval", "wish_class", *gate]) == 1
    assert "P9 blind60_v2: P:a" in capsys.readouterr().out
