"""origin: playbook/snippets/test_every_enum_member_is_collected.py (service/trend-radar
tests/test_every_dataset_has_a_collector.py). A member with a collector and no schedule is a real
recorded outage (playbook 02-test-discipline.md T10), so every commerce Dataset gets both."""

from __future__ import annotations

from pathlib import Path

import pytest

import collectors.commerce.sources  # noqa: F401 -- registers every source
from collectors.commerce.models import Dataset
from collectors.commerce.registry import SOURCES

CRONTAB = Path(__file__).resolve().parents[3] / "stack" / "crontab"


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
