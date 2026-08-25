"""stack/crontab.d actually schedules every naver Dataset (playbook T10: a collector with no cron
line is a real recorded outage), on a minute that isn't 0 and a time that doesn't collide with any
other collector's line -- same concern as tests/collectors/commerce/
test_every_dataset_is_collected_and_scheduled.py (#7), kept in this file per F-4 (수정 라운드 1)
rather than editing that one."""

from __future__ import annotations

from pathlib import Path

import pytest

from collectors.naver.models import Dataset

REPO_ROOT = Path(__file__).resolve().parents[3]
# stack/crontab became one file per supercronic container. Reading the directory keeps the collision
# check below looking at the whole schedule, not just the container naver happens to live in today.
CRONTAB_D = REPO_ROOT / "stack" / "crontab.d"


def _lines() -> list[str]:
    return [
        stripped
        for path in sorted(p for p in CRONTAB_D.iterdir() if p.is_file())
        for ln in path.read_text(encoding="utf-8").splitlines()
        if (stripped := ln.split("#", 1)[0].strip())
    ]


def _times_by_dataset() -> dict[str, tuple[str, ...]]:
    """`{dataset: (minute, hour, dom, month, dow)}` for every `cosmai collect naver --dataset X`
    line in stack/crontab.d."""
    out: dict[str, tuple[str, ...]] = {}
    for ln in _lines():
        if "cosmai collect naver" not in ln or "--dataset" not in ln:
            continue
        parts = ln.split()
        out[parts[parts.index("--dataset") + 1]] = tuple(parts[:5])
    return out


def _all_collector_times() -> list[tuple[str, tuple[str, ...]]]:
    """`(label, (minute, hour, dom, month, dow))` for every `cosmai collect <x> --dataset <y>` line
    in the file, naver included -- what naver's own times must not collide with."""
    out: list[tuple[str, tuple[str, ...]]] = []
    for ln in _lines():
        if "cosmai collect" not in ln:
            continue
        parts = ln.split()
        collector = parts[parts.index("collect") + 1]
        dataset = parts[parts.index("--dataset") + 1] if "--dataset" in parts else ""
        out.append((f"{collector} {dataset}".strip(), tuple(parts[:5])))
    return out


# TODO(#93): commerce and youtube guard that the contract names every dataset's schedule; naver does not.
def test_there_is_something_to_check():
    assert list(Dataset)
    assert _lines()


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_every_naver_dataset_has_a_cron_line(dataset: Dataset):
    times = _times_by_dataset()
    assert dataset.value in times, f"{dataset.value} has a collector (#9) and no cron line"


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_no_naver_line_starts_on_minute_zero(dataset: Dataset):
    # Same rule commerce's daily lines follow (contracts/entrypoints.md §스케줄): minute 0 is the
    # hourly ranking walk's own start.
    minute = _times_by_dataset()[dataset.value][0]
    assert minute != "0", f"naver {dataset.value} starts on minute 0, the hourly ranking walk's minute"


def test_no_two_collector_lines_share_a_time():
    times = _all_collector_times()
    seen: dict[tuple[str, ...], str] = {}
    for label, t in times:
        clash = seen.get(t)
        assert clash is None, f"{label} and {clash} both schedule {' '.join(t)}"
        seen[t] = label
