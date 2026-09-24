"""Fixture-based parser tests for glowpick, offline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from collectors.commerce.contract import Payload
from collectors.commerce.models import Dataset, NewProductRecord, ProductRecord, RankRecord, ReviewRecord
from collectors.commerce.sources.glowpick import BOARDS, Glowpick, _reviews

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
    review_only = {r.product_key for r in reviews} - {r.product_key for r in ranks}
    assert review_only  # the fixture exercises the catalogue gap, not only ranked products
    products = [r for r in out.records if isinstance(r, ProductRecord)]
    assert review_only <= {r.product_key for r in products}
    assert len(products) == len({r.product_key for r in products})


def test_a_review_without_a_product_title_keeps_the_review_but_not_a_bad_product():
    row = {
        "idreviewcomment": "review-1",
        "reviewText": "Useful review",
        "product": {"idProduct": "product-1", "productTitle": None},
    }
    [(review, product)] = _reviews(json.dumps(row), source="glowpick", captured_at=AT)
    assert review.product_key == "product-1"
    assert product is None


def test_twelve_boards_are_walked():
    assert len(BOARDS) == 12
    assert len(Glowpick().seeds(Dataset.RANKING)) == 12


def test_new_product_board_yields_new_product_records_and_no_rank():
    fetch = Glowpick().seeds(Dataset.NEW_PRODUCT)[0]
    body = (FIXTURES / "new_product/brand-new.html").read_bytes()
    out = Glowpick().parse(_payload(fetch, body))
    assert not [r for r in out.records if isinstance(r, RankRecord)]
    assert [r for r in out.records if isinstance(r, NewProductRecord)]
