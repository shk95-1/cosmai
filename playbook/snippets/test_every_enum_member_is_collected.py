"""origin: service/trend-radar/tests/test_every_dataset_has_a_collector.py
reuse: point Dataset/SOURCES at your enum and registry. Add a third test that every member has a cron
line in stack/crontab -- the 2026-08-23 outage was exactly a member with a collector and no schedule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cosmai.contracts import Dataset  # the enum of "a run goes out and collects this"
from cosmai.collectors.registry import SOURCES  # key -> source class with .datasets and .seeds()

CRONTAB = Path(__file__).resolve().parents[1] / "stack" / "crontab"


def test_there_are_sources_and_datasets():
    assert SOURCES and list(Dataset)


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_some_source_collects_this_dataset(dataset: Dataset):
    collectors = sorted(k for k, cls in SOURCES.items() if dataset in cls.datasets)
    assert collectors, f"no source declares {dataset.value!r}: the row count is zero forever"


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_every_collector_of_it_has_a_seed(dataset: Dataset):
    seedless = sorted(k for k, cls in SOURCES.items() if dataset in cls.datasets and not cls().seeds(dataset))
    assert not seedless, f"{seedless} declare {dataset.value!r} and enqueue nothing; the run would report ok"


@pytest.mark.parametrize("dataset", list(Dataset), ids=lambda d: d.value)
def test_every_dataset_is_scheduled(dataset: Dataset):
    lines = [ln for ln in CRONTAB.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert any(f"--dataset {dataset.value}" in ln for ln in lines), f"{dataset.value} has a collector and no cron line"
