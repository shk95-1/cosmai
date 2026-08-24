"""The youtube half of tests/collectors/commerce/test_every_dataset_is_collected_and_scheduled.py.

Same recorded failure, other collector: a dataset with a collector and no cron line runs zero times
and says nothing (playbook 02-test-discipline.md T10 -- three commerce walks stopped for two days in
2026-08 exactly that way). contracts/entrypoints.md §스케줄 named youtube's periods while
stack/crontab held none of them, so all four were in that state at once.

The contract states youtube as periods (`watch 1h`), not as cron lines the way it states commerce --
so this compares periods: what each cron line repeats at has to be what the contract asked for. The
fence reader is a copy of the commerce file's rather than an import, because tests/ is not a package
and a cross-test import would be the more fragile of the two.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from collectors.youtube.models import Dataset

REPO_ROOT = Path(__file__).resolve().parents[3]
CRONTAB_D = REPO_ROOT / "stack" / "crontab.d"
ENTRYPOINTS_MD = REPO_ROOT / "contracts" / "entrypoints.md"

UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}
_PERIOD = re.compile(r"(?P<dataset>[a-z_]+)\s+(?P<count>\d+)(?P<unit>[mhd])\b")


def _schedule_block() -> str:
    text = ENTRYPOINTS_MD.read_text(encoding="utf-8")
    start = text.index("## 스케줄")
    fence_start = text.index("```", start) + 3
    return text[fence_start : text.index("```", fence_start)]


def contract_periods() -> dict[str, int]:
    """`{dataset: seconds}` from the §스케줄 block's `youtube: watch 1h · flatten 15m · ...` line."""
    for raw in _schedule_block().splitlines():
        head, sep, rest = raw.partition("youtube:")
        if not sep or head.strip():
            continue
        return {m["dataset"]: int(m["count"]) * UNIT_SECONDS[m["unit"]] for m in _PERIOD.finditer(rest)}
    return {}


def _period_seconds(fields: tuple[str, ...], line: str) -> int:
    """How often a five-field cron line repeats. Only the three shapes the youtube schedule needs are
    accepted -- an unrecognised one raises instead of being guessed at, because a wrong guess here
    would turn this whole comparison green on a schedule nobody chose."""
    minute, hour, dom, month, dow = fields
    if (dom, month, dow) == ("*", "*", "*"):
        if hour == "*" and minute.startswith("*/") and minute[2:].isdigit():
            return int(minute[2:]) * 60
        if hour == "*" and minute.isdigit():
            return 3600
        if minute.isdigit() and hour.isdigit():
            return 86400
    raise AssertionError(f"unsupported cron period in {line!r}")


def cron_periods() -> dict[str, list[int]]:
    """`{dataset: [seconds, ...]}` for every `cosmai collect youtube --dataset X` line in crontab.d."""
    out: dict[str, list[int]] = {}
    for path in sorted(p for p in CRONTAB_D.iterdir() if p.is_file()):
        for raw in path.read_text(encoding="utf-8").splitlines():
            ln = raw.split("#", 1)[0].strip()
            if "cosmai collect youtube" not in ln or "--dataset" not in ln:
                continue
            parts = ln.split()
            out.setdefault(parts[parts.index("--dataset") + 1], []).append(
                _period_seconds(tuple(parts[:5]), ln)
            )
    return out


CONTRACT_PERIODS = contract_periods()
CRON_PERIODS = cron_periods()


def test_the_contract_names_a_period_for_every_youtube_dataset():
    # Without this, dropping a dataset from the contract line would quietly shrink the comparison
    # below instead of failing -- the same guard the commerce file keeps over its §스케줄 times.
    assert set(CONTRACT_PERIODS) == {d.value for d in Dataset}, (
        f"contracts/entrypoints.md §스케줄 names periods for {sorted(CONTRACT_PERIODS)}, "
        f"but every youtube dataset is {sorted(d.value for d in Dataset)}"
    )


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_every_dataset_has_exactly_one_cron_line(dataset: Dataset):
    found = CRON_PERIODS.get(dataset.value, [])
    assert len(found) == 1, (
        f"{dataset.value} has a collector and {len(found)} cron line(s) in stack/crontab.d; "
        "one dataset is one schedule"
    )


@pytest.mark.parametrize("dataset", sorted(CONTRACT_PERIODS), ids=lambda d: d)
def test_the_cron_line_repeats_at_the_contracted_period(dataset: str):
    assert dataset in CRON_PERIODS, f"{dataset} has a period in the contract but no line in crontab.d"
    assert CRON_PERIODS[dataset] == [CONTRACT_PERIODS[dataset]], (
        f"stack/crontab.d repeats youtube {dataset} every {CRON_PERIODS[dataset]}s, "
        f"contracts/entrypoints.md §스케줄 says every {CONTRACT_PERIODS[dataset]}s"
    )
