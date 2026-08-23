"""Fixture-based parser tests for daisomall, offline."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from collectors.commerce.contract import Fetch, Payload
from collectors.commerce.models import Dataset, NewProductRecord, RankRecord, ReviewAnswerRecord, ReviewRecord
from collectors.commerce.sources.daisomall import ATTR_ENDPOINT, REVIEW_ENDPOINT, DaisoMall

AT = datetime(2026, 8, 18, 9, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "daisomall"


def _payload(fetch, body: bytes) -> Payload:
    return Payload(
        fetch=fetch, status=200, body=body, final_url=fetch.url, headers={}, elapsed_ms=1, captured_at=AT
    )


def test_a_ranking_page_yields_ranked_products():
    fetch = DaisoMall().seeds(Dataset.RANKING)[0]
    body = (FIXTURES / "ranking/sale_daily.json").read_bytes()
    out = DaisoMall().parse(_payload(fetch, body))
    ranks = [r for r in out.records if isinstance(r, RankRecord)]
    assert ranks
    assert all(r.category_key == "CTGR_01050" for r in ranks)


def test_a_review_dataset_ranking_run_writes_no_rank_rows_and_follows_reviews():
    fetch = DaisoMall().seeds(Dataset.REVIEW)[0]
    body = (FIXTURES / "ranking/review.json").read_bytes()
    out = DaisoMall().parse(_payload(fetch, body))
    assert not [r for r in out.records if isinstance(r, RankRecord)]
    assert out.follow


def test_review_rows_carry_the_reviewers_own_survey_answers():
    fetch = Fetch(
        url=REVIEW_ENDPOINT,
        dataset=Dataset.REVIEW,
        method="POST",
        context=(("kind", "reviews"), ("product", "1"), ("page", "1"), ("size", "100")),
    )
    body = (FIXTURES / "review/list.json").read_bytes()
    out = DaisoMall().parse(_payload(fetch, body))
    assert [r for r in out.records if isinstance(r, ReviewRecord)]
    assert [r for r in out.records if isinstance(r, ReviewAnswerRecord)]


def test_the_attr_endpoint_yields_topics():
    fetch = Fetch(
        url=ATTR_ENDPOINT,
        dataset=Dataset.REVIEW,
        method="POST",
        context=(("kind", "attrs"), ("product", "1")),
    )
    body = (FIXTURES / "review/attr.json").read_bytes()
    out = DaisoMall().parse(_payload(fetch, body))
    assert out.records


def test_new_product_board_yields_new_product_records_with_no_listed_date():
    fetch = DaisoMall().seeds(Dataset.NEW_PRODUCT)[0]
    body = (FIXTURES / "new_product/new_weekly.json").read_bytes()
    out = DaisoMall().parse(_payload(fetch, body))
    records = [r for r in out.records if isinstance(r, NewProductRecord)]
    assert records
    assert all(r.listed_at is None for r in records)
