"""stack/crontab.d actually schedules every naver Dataset (playbook T10: a collector with no cron
line is a real recorded outage), on a minute that isn't 0 and a time that doesn't collide with any
other collector's line -- same concern as tests/collectors/commerce/
test_every_dataset_is_collected_and_scheduled.py (#7), kept in this file per F-4 (fix round 1)
rather than editing that one."""

from __future__ import annotations

from pathlib import Path

import pytest

from collectors.naver.models import Dataset

REPO_ROOT = Path(__file__).resolve().parents[3]
# stack/crontab became one file per supercronic container. Reading the directory keeps the collision
# check below looking at the whole schedule, not just the container naver happens to live in today.
CRONTAB_D = REPO_ROOT / "stack" / "crontab.d"
ENTRYPOINTS_MD = REPO_ROOT / "contracts" / "entrypoints.md"


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


def _gated_times_by_dataset() -> dict[str, tuple[str, ...]]:
    """The same, for a `cosmai collect naver` line that is commented out -- a line gated on purpose,
    kept in the file so un-gating it is one character and the reason above it stays readable."""
    out: dict[str, tuple[str, ...]] = {}
    for path in sorted(p for p in CRONTAB_D.iterdir() if p.is_file()):
        for raw in path.read_text(encoding="utf-8").splitlines():
            body = raw.strip().lstrip("#").strip()
            if not raw.strip().startswith("#") or "cosmai collect naver" not in body:
                continue
            parts = body.split()
            # A prose comment mentioning the command starts with a word; a cron line starts with its
            # minute field.
            if "--dataset" not in parts or not parts[0][0].isdigit():
                continue
            out[parts[parts.index("--dataset") + 1]] = tuple(parts[:5])
    return out


def _gate_reason(dataset: str) -> str:
    """The comment lines directly above a gated dataset's line -- why it is off, in the file that
    turns it off."""
    reason: list[str] = []
    for path in sorted(p for p in CRONTAB_D.iterdir() if p.is_file()):
        for raw in path.read_text(encoding="utf-8").splitlines():
            body = raw.strip().lstrip("#").strip()
            if raw.strip().startswith("#") and f"--dataset {dataset}" in body:
                return "\n".join(reason)
            reason = [*reason, body] if raw.strip().startswith("#") else []
    return ""


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


def _entrypoints_schedule_block() -> str:
    """The §Schedule fenced block in contracts/entrypoints.md -- copied from the commerce/youtube
    guards rather than imported, since tests/ is not a package and a cross-test import would be the
    more fragile of the two."""
    text = ENTRYPOINTS_MD.read_text(encoding="utf-8")
    start = text.index("## Schedule")
    fence_start = text.index("```", start) + 3
    fence_end = text.index("```", fence_start)
    return text[fence_start:fence_end]


def _contract_named_datasets() -> set[str]:
    """Dataset names mentioned on the `naver:` line of the §Schedule block. Commerce and youtube state
    a time/period per dataset there; naver states a schedule in prose (`datalab once a month ...`), so
    this checks the weaker thing prose can promise -- every dataset gets named at all."""
    for raw in _entrypoints_schedule_block().splitlines():
        head, sep, rest = raw.partition("naver:")
        if not sep or head.strip():
            continue
        words = set(rest.split())
        return {d.value for d in Dataset if d.value in words}
    return set()


def test_the_contract_names_every_naver_dataset():
    # Same guard commerce and youtube keep over their §Schedule lines (#93): if the naver line stops
    # naming a dataset, that dataset's schedule is nowhere in the contract and nothing else notices.
    named = _contract_named_datasets()
    all_naver = {d.value for d in Dataset}
    assert named == all_naver, (
        f"contracts/entrypoints.md §Schedule names naver datasets {sorted(named)}, "
        f"but every naver dataset is {sorted(all_naver)}"
    )


def test_there_is_something_to_check():
    assert list(Dataset)
    assert _lines()


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_every_naver_dataset_has_a_cron_line(dataset: Dataset):
    # A gated line still counts as a line: it is present, it carries its time, and the reason it is
    # off is the comment right above it (#182 -- datalab waits on #90). A dataset with no line at all
    # is the recorded outage this test exists for.
    times = {**_times_by_dataset(), **_gated_times_by_dataset()}
    assert dataset.value in times, f"{dataset.value} has a collector (#9) and no cron line"


def test_no_naver_line_is_gated_now_that_the_anchor_is_in():
    """#182 commented the DataLab line out while there was no anchor to make a pull comparable;
    #90 put the anchor in every request, so both naver lines run. A line commented out again needs
    a reason above it naming the issue that restores it -- what `_gate_reason` reads."""
    gated = _gated_times_by_dataset()
    assert gated == {}, f"gated naver datasets: {sorted(gated)}"
    assert set(_times_by_dataset()) == {"datalab", "blog"}, sorted(_times_by_dataset())
    assert _gate_reason("datalab") == "", "a live line must not be read as a gated one"


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_no_naver_line_starts_on_minute_zero(dataset: Dataset):
    # Same rule commerce's daily lines follow (contracts/entrypoints.md §Schedule): minute 0 is the
    # hourly ranking walk's own start. A gated line is checked too -- un-gating must not need this
    # question asked again.
    minute = {**_times_by_dataset(), **_gated_times_by_dataset()}[dataset.value][0]
    assert minute != "0", f"naver {dataset.value} starts on minute 0, the hourly ranking walk's minute"


def test_no_two_collector_lines_share_a_time():
    times = _all_collector_times()
    seen: dict[tuple[str, ...], str] = {}
    for label, t in times:
        clash = seen.get(t)
        assert clash is None, f"{label} and {clash} both schedule {' '.join(t)}"
        seen[t] = label
