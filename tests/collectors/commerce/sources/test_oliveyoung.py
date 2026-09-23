"""Fixture-based parser tests for oliveyoung, offline. Fixtures: tests/collectors/commerce/fixtures/
oliveyoung/ (origin: service/trend-radar/tests/fixtures/oliveyoung/, brought in for #7)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from collectors.commerce.contract import Fetch, Payload, Transport
from collectors.commerce.engine import TransientError, collect
from collectors.commerce.models import Dataset, ProductRecord, RankRecord, ReviewRecord
from collectors.commerce.sources.oliveyoung import OliveYoung, product_boards

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


def test_mens_ranking_uses_the_current_site_category():
    mens = next(fetch for fetch in OliveYoung().seeds(Dataset.RANKING) if fetch.ctx("board") == "mens")
    assert "fltDispCatNo=10000060002" in mens.url
    assert mens.wait_for == "ul.cate_prd_list > li"
    body = (FIXTURES / "ranking/best-skincare.html").read_bytes()
    parsed = OliveYoung().parse(_payload(mens, body))
    ranks = [record for record in parsed.records if isinstance(record, RankRecord)]
    assert len(ranks) == 100
    assert all(record.board == "mens" and record.category_key == "10000060002" for record in ranks)


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


def test_product_group_records_its_actual_target_and_preserves_the_daily_target():
    source = OliveYoung()
    seeds = source.seeds(Dataset.PRODUCT, board="skincare,makeup")
    assert [seed.ctx("board") for seed in seeds] == ["skincare", "makeup"]
    assert source.run_scope(Dataset.PRODUCT, board="skincare,makeup") == {
        "product": {"boards": 2, "product_products": 34}
    }
    assert source.run_scope(Dataset.PRODUCT) == {"product": {"boards": 5, "product_products": 85}}


def test_two_board_pass_attempts_every_detail_even_when_each_needs_a_retry():
    ranking = (FIXTURES / "ranking/best-skincare.html").read_bytes()

    class RefusingDetails:
        def __init__(self):
            self.details: list[str] = []

        def fetch(self, fetch: Fetch) -> Payload:
            if fetch.ctx("kind") == "product":
                self.details.append(fetch.ctx("product") or "")
                raise TransientError("detail timed out")
            body = ranking
            if fetch.ctx("board") == "makeup":
                body = re.sub(rb"A([0-9]{12})", rb"B\1", ranking)
            return _payload(fetch, body)

    class NullSink:
        def write(self, records) -> None:
            pass

    now = [0.0]
    fetcher = RefusingDetails()
    report = collect(
        sources=[OliveYoung()],
        dataset=Dataset.PRODUCT,
        board="skincare,makeup",
        sink=NullSink(),
        captured_at=AT,
        fetcher=fetcher,
        clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    ).sources["oliveyoung"]
    assert len(set(fetcher.details)) == 34
    assert all(fetcher.details.count(key) == 2 for key in set(fetcher.details))
    assert report.requests == 70
    assert not report.budget_exhausted
    assert report.scope == {"product": {"boards": 2, "product_products": 34}}


@pytest.mark.parametrize("board", ["", "skincare,", "skincare,skincare", "skincare,makeup,hair", "body"])
def test_product_group_refuses_an_unbounded_or_unknown_scope(board: str):
    with pytest.raises(ValueError, match="one or two distinct"):
        product_boards(board)


def test_ingredient_pass_reads_the_low_review_board_under_its_own_budget():
    (fetch,) = OliveYoung().seeds(Dataset.INGREDIENTS)
    assert fetch.ctx("board") == "suncare"
    body = (FIXTURES / "ranking/best-skincare.html").read_bytes()
    out = OliveYoung().parse(_payload(fetch, body))
    assert not out.records
    assert len(out.follow) == 10
    assert all(f.dataset is Dataset.INGREDIENTS and f.ctx("kind") == "product" for f in out.follow)
    detail = (FIXTURES / "product/detail.html").read_bytes()
    parsed = OliveYoung().parse(_payload(out.follow[0], detail))
    assert len(parsed.records) == 1
    assert isinstance(parsed.records[0], ProductRecord)
    assert parsed.records[0].ingredients
    assert out.follow[0].timeout_s == 15.0


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
