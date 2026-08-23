"""Fixture-based parser tests for oliveyoung, offline. Fixtures: tests/collectors/commerce/fixtures/
oliveyoung/ (origin: service/trend-radar/tests/fixtures/oliveyoung/, brought in for #7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from collectors.commerce.contract import Fetch, Payload, Transport
from collectors.commerce.models import Dataset, ProductRecord, RankRecord, ReviewRecord
from collectors.commerce.sources.oliveyoung import OliveYoung

AT = datetime(2026, 8, 18, 9, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "oliveyoung"


def _payload(fetch: Fetch, body: bytes) -> Payload:
    return Payload(
        fetch=fetch, status=200, body=body, final_url=fetch.url, headers={}, elapsed_ms=1, captured_at=AT
    )


def _ranking_records() -> list[RankRecord]:
    fetch = OliveYoung().seeds(Dataset.RANKING)[0]
    body = (FIXTURES / "ranking/best.html").read_bytes()
    out = OliveYoung().parse(_payload(fetch, body))
    return [r for r in out.records if isinstance(r, RankRecord)]


def test_needs_a_browser_transport():
    # Plain HTTP gets a Cloudflare challenge; declaring HTTP would make every run report blocked.
    assert OliveYoung.policy.transport is Transport.BROWSER


def test_the_whole_ranking_parses():
    # The 100 products are split across 25 <ul> blocks of four; a parser that took the first list
    # would silently collect four.
    assert len(_ranking_records()) == 100


def test_ranks_are_dense_and_start_at_one():
    assert [r.rank for r in _ranking_records()] == list(range(1, 101))


def test_price_is_todays_price_not_the_list_price():
    top = _ranking_records()[0]
    assert top.price == 10000


def test_review_rating_is_left_empty_because_the_page_has_none():
    # The star markup on this page is a JavaScript template, not a value.
    assert all(r.review_rating is None for r in _ranking_records())


def test_a_product_dataset_run_fetches_ingredients_not_ranking_rows():
    fetch = OliveYoung().seeds(Dataset.PRODUCT)[0]
    body = (FIXTURES / "ranking/best-skincare.html").read_bytes()
    out = OliveYoung().parse(_payload(fetch, body))
    assert not [r for r in out.records if isinstance(r, RankRecord)]
    assert out.follow
    assert all(f.dataset is Dataset.PRODUCT for f in out.follow)


def test_ingredients_are_read_from_the_disclosure_table():
    fetch = Fetch(
        url="https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo=X",
        dataset=Dataset.PRODUCT,
        context=(("kind", "product"), ("product", "A000000223414"), ("name", "n"), ("brand", "b")),
    )
    body = (FIXTURES / "product/detail.html").read_bytes()
    out = OliveYoung().parse(_payload(fetch, body))
    (record,) = out.records
    assert isinstance(record, ProductRecord)
    assert record.ingredients


def test_review_rows_parse_with_a_scrubbed_author():
    fetch = Fetch(
        url="https://m.oliveyoung.co.kr/review/api/v2/reviews/cursor",
        dataset=Dataset.REVIEW,
        context=(("kind", "reviews"), ("product", "A000000223414"), ("sort", "RATING_DESC"), ("page", "0")),
    )
    body = (FIXTURES / "review/list.json").read_bytes()
    out = OliveYoung().parse(_payload(fetch, body))
    reviews = [r for r in out.records if isinstance(r, ReviewRecord)]
    assert reviews
    assert all(r.author_hash is None or len(r.author_hash) == 16 for r in reviews)


def test_review_stats_parse_the_star_distribution():
    fetch = Fetch(
        url="https://m.oliveyoung.co.kr/review/api/v2/reviews/X/stats",
        dataset=Dataset.REVIEW_STATS,
        context=(("kind", "stats"), ("product", "A000000223414")),
    )
    body = (FIXTURES / "review/stats.json").read_bytes()
    out = OliveYoung().parse(_payload(fetch, body))
    assert out.records
