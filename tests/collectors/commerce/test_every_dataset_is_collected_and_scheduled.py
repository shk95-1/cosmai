"""origin: playbook/snippets/test_every_enum_member_is_collected.py (service/trend-radar
tests/test_every_dataset_has_a_collector.py). A member with a collector and no schedule is a real
recorded outage (playbook 02-test-discipline.md T10), so every commerce Dataset gets both -- and where
contracts/entrypoints.md §스케줄 names a time, stack/crontab has to actually run at it (review round 1,
#7: review_stats drifted to 04:45 with nothing checking it)."""

from __future__ import annotations

from pathlib import Path

import pytest

import collectors.commerce.sources  # noqa: F401 -- registers every source
from collectors.commerce.models import Dataset
from collectors.commerce.registry import SOURCES

REPO_ROOT = Path(__file__).resolve().parents[3]
CRONTAB = REPO_ROOT / "stack" / "crontab"
ENTRYPOINTS_MD = REPO_ROOT / "contracts" / "entrypoints.md"


def _cron_times_by_dataset(text: str) -> dict[str, tuple[str, ...]]:
    """`{dataset: (minute, hour, dom, month, dow)}` for every `cosmai collect commerce --dataset X`
    line in `text` -- works on both stack/crontab and the fenced block in entrypoints.md's §스케줄,
    since both spell a cron line the same way."""
    out: dict[str, tuple[str, ...]] = {}
    for raw in text.splitlines():
        ln = raw.split("#", 1)[0].strip()
        if "cosmai collect commerce" not in ln or "--dataset" not in ln:
            continue
        parts = ln.split()
        out[parts[parts.index("--dataset") + 1]] = tuple(parts[:5])
    return out


def _entrypoints_schedule_block() -> str:
    text = ENTRYPOINTS_MD.read_text(encoding="utf-8")
    start = text.index("## 스케줄")
    fence_start = text.index("```", start) + 3
    fence_end = text.index("```", fence_start)
    return text[fence_start:fence_end]


CONTRACT_TIMES = _cron_times_by_dataset(_entrypoints_schedule_block())
CRONTAB_TIMES = _cron_times_by_dataset(CRONTAB.read_text(encoding="utf-8"))

# entrypoints.md §스케줄 names a time for these four only; review and new_product are scheduled (every
# Dataset member must be, per test_every_dataset_is_scheduled_in_the_commerce_crontab below) but the
# contract does not pin a time for them, so there is nothing here to check them against.
DATASETS_WITHOUT_A_CONTRACT_TIME = frozenset({"review", "new_product"})


def test_there_are_sources_and_datasets():
    assert SOURCES and list(Dataset)


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_some_source_collects_this_dataset(dataset: Dataset):
    collectors = sorted(k for k, cls in SOURCES.items() if dataset in cls.datasets)
    assert collectors, f"no source declares {dataset.value!r}: the row count is zero forever"


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_every_collector_of_it_has_a_seed(dataset: Dataset):
    seedless = sorted(k for k, cls in SOURCES.items() if dataset in cls.datasets and not cls().seeds(dataset))
    assert not seedless, f"{seedless} declare {dataset.value!r} and enqueue nothing"


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_every_dataset_is_scheduled_in_the_commerce_crontab(dataset: Dataset):
    lines = [
        ln for ln in CRONTAB.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")
    ]
    assert any("cosmai collect commerce" in ln and f"--dataset {dataset.value}" in ln for ln in lines), (
        f"{dataset.value} has a collector and no cron line"
    )


def test_the_contract_names_a_time_for_every_dataset_except_the_documented_gap():
    # If entrypoints.md §스케줄 starts or stops naming a dataset's time, this must be updated
    # deliberately -- it is the thing that stops the crontab-time check below from silently covering
    # nothing (or missing a dataset it now could check).
    named = set(CONTRACT_TIMES)
    all_commerce = {d.value for d in Dataset}
    assert all_commerce - named == DATASETS_WITHOUT_A_CONTRACT_TIME, (
        "contracts/entrypoints.md §스케줄's named datasets changed; update "
        "DATASETS_WITHOUT_A_CONTRACT_TIME to match"
    )


@pytest.mark.parametrize("dataset", sorted(CONTRACT_TIMES), ids=lambda d: d)
def test_crontab_time_matches_the_contract(dataset: str):
    assert dataset in CRONTAB_TIMES, f"{dataset} is scheduled in the contract but not in stack/crontab"
    assert CRONTAB_TIMES[dataset] == CONTRACT_TIMES[dataset], (
        f"stack/crontab schedules {dataset} at {' '.join(CRONTAB_TIMES[dataset])}, "
        f"contracts/entrypoints.md §스케줄 says {' '.join(CONTRACT_TIMES[dataset])}"
    )
