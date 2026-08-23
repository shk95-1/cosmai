"""Fixture-based parser tests for hwahae, offline."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from collectors.commerce.contract import Payload
from collectors.commerce.models import Dataset, ProductRecord, RankRecord
from collectors.commerce.sources.hwahae import Hwahae

AT = datetime(2026, 8, 18, 9, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hwahae"


def _payload(fetch, body: bytes) -> Payload:
    return Payload(
        fetch=fetch, status=200, body=body, final_url=fetch.url, headers={}, elapsed_ms=1, captured_at=AT
    )


def test_the_home_page_yields_all_four_boards():
    fetch = Hwahae().seeds(Dataset.RANKING)[0]
    body = (FIXTURES / "ranking/home.html").read_bytes()
    out = Hwahae().parse(_payload(fetch, body))
    ranks = [r for r in out.records if isinstance(r, RankRecord)]
    assert {r.board for r in ranks} == {"trending", "category", "skin", "age"}


def test_products_and_prices_ride_along_with_the_ranking():
    fetch = Hwahae().seeds(Dataset.RANKING)[0]
    body = (FIXTURES / "ranking/home.html").read_bytes()
    out = Hwahae().parse(_payload(fetch, body))
    assert [r for r in out.records if isinstance(r, ProductRecord)]


def test_this_source_declares_only_ranking():
    assert Hwahae.datasets == frozenset({Dataset.RANKING})
