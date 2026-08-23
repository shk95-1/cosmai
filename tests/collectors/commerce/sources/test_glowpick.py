"""Fixture-based parser tests for glowpick, offline."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from collectors.commerce.contract import Payload
from collectors.commerce.models import Dataset, NewProductRecord, RankRecord, ReviewRecord
from collectors.commerce.sources.glowpick import BOARDS, Glowpick

AT = datetime(2026, 8, 18, 9, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "glowpick"


def _payload(fetch, body: bytes) -> Payload:
    return Payload(
        fetch=fetch, status=200, body=body, final_url=fetch.url, headers={}, elapsed_ms=1, captured_at=AT
    )


def test_a_category_page_yields_ranked_products_and_reviews():
    fetch = Glowpick().seeds(Dataset.RANKING)[0]
    body = (FIXTURES / "ranking/category-109.html").read_bytes()
    out = Glowpick().parse(_payload(fetch, body))
    ranks = [r for r in out.records if isinstance(r, RankRecord)]
    reviews = [r for r in out.records if isinstance(r, ReviewRecord)]
    assert ranks
    assert all(r.board == "category" for r in ranks)
    # Free: the review feed rides along on the same category-page bytes as the ranking.
    assert reviews


def test_twelve_boards_are_walked():
    assert len(BOARDS) == 12
    assert len(Glowpick().seeds(Dataset.RANKING)) == 12


def test_new_product_board_yields_new_product_records_and_no_rank():
    fetch = Glowpick().seeds(Dataset.NEW_PRODUCT)[0]
    body = (FIXTURES / "new_product/brand-new.html").read_bytes()
    out = Glowpick().parse(_payload(fetch, body))
    assert not [r for r in out.records if isinstance(r, RankRecord)]
    assert [r for r in out.records if isinstance(r, NewProductRecord)]
