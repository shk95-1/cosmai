"""기준선 상수는 contracts/interfaces.md 표의 사본이다 — 표를 파싱해 숫자를 대조한다 (T10/T11)."""

from __future__ import annotations

import re
from pathlib import Path

from analysis.baselines import BASELINES, RULE_MEASURED, for_task
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
