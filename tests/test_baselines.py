"""기준선 상수는 contracts/interfaces.md 표의 사본이다 — 표를 파싱해 숫자를 대조한다 (T10/T11)."""

from __future__ import annotations

import re
from pathlib import Path

from analysis.baselines import BASELINES, for_task
from analysis.registry import TASKS

TABLE_HEADER = "| task | 평가셋 |"
INTERFACES = Path(__file__).resolve().parents[1] / "contracts" / "interfaces.md"
# 표의 채택 조건은 하네스가 내는 지표 키를 그대로 쓴다: `acc ≥ .77 그리고 P:불만 ≥ .89`.
CHECK = re.compile(r"([^\s|]+)\s*≥\s*(\d*\.\d+)")


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
