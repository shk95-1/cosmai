"""review_low's #7 generalization: `--board <name>` instead of a hardcoded `suncare`, and RATING_ASC
reading until a 3-star review appears instead of a fixed page count. origin (for the walk shape this
replaces): service/trend-radar collectors/commerce/_patches/0001-...review_low....patch (read-only
reference, not applied verbatim -- see the module docstring in sources/oliveyoung.py)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from collectors.commerce.contract import Fetch, Payload
from collectors.commerce.models import Dataset, RankRecord
from collectors.commerce.sources.oliveyoung import (
    LOW_ASC_PAGES_MAX,
    LOW_BOARDS,
    LOW_DESC_PAGES,
    LOW_PRODUCTS,
    REVIEW_ENDPOINT,
    REVIEW_PAGES,
    OliveYoung,
)

AT = datetime(2026, 8, 23, 3, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "oliveyoung"
GOODS = "A000000223414"


def _payload(fetch: Fetch, body: bytes) -> Payload:
    return Payload(
        fetch=fetch, status=200, body=body, final_url=fetch.url, headers={}, elapsed_ms=1, captured_at=AT
    )


def _ranking_out(board: str | None = None):
    fetch = OliveYoung().seeds(Dataset.REVIEW_LOW, board=board)[0]
    body = (FIXTURES / "ranking/best-skincare.html").read_bytes()
    return OliveYoung().parse(_payload(fetch, body))


def _review_body(rows: list[dict], *, has_next: bool = True) -> dict:
    return {
        "data": {
            "goodsReviewList": rows,
            "hasNext": has_next,
            "nextCursorId": 1 if has_next else None,
            "nextCursorScore": 1.0 if has_next else None,
        }
    }


def _row(review_id: int, score: int) -> dict:
    return {"reviewId": review_id, "reviewScore": score, "content": "x", "createdDateTime": "2026.08.20"}


def _review_payload(rows: list[dict], *, sort: str, page: int, has_next: bool = True) -> Payload:
    fetch = Fetch(
        url=REVIEW_ENDPOINT,
        dataset=Dataset.REVIEW_LOW,
        method="POST",
        context=(
            ("kind", "reviews"),
            ("product", GOODS),
            ("sort", sort),
            ("page", str(page)),
            ("pages", str(LOW_ASC_PAGES_MAX)),
        ),
    )
    return _payload(fetch, json.dumps(_review_body(rows, has_next=has_next)).encode())


# --- --board -------------------------------------------------------------------


def test_default_board_is_the_first_in_scope():
    seeds = OliveYoung().seeds(Dataset.REVIEW_LOW)
    assert seeds[0].ctx("board") == LOW_BOARDS[0]


def test_a_named_board_is_used_when_it_is_in_scope():
    board = LOW_BOARDS[0]
    seeds = OliveYoung().seeds(Dataset.REVIEW_LOW, board=board)
    assert len(seeds) == 1
    assert seeds[0].ctx("board") == board


def test_an_unknown_board_is_refused():
    with pytest.raises(ValueError):
        OliveYoung().seeds(Dataset.REVIEW_LOW, board="not-a-real-board")


def test_every_low_board_is_a_board_this_source_actually_walks():
    from collectors.commerce.sources.oliveyoung import _BOARD_NAMES

    assert set(LOW_BOARDS) <= _BOARD_NAMES


# --- what the ranking follows ---------------------------------------------------


def test_a_low_run_writes_no_rank_rows():
    assert not [r for r in _ranking_out().records if isinstance(r, RankRecord)]


def test_the_first_products_get_three_follows_each():
    follow = _ranking_out().follow
    products = [f.ctx("product") for f in follow]
    assert len(set(products)) == LOW_PRODUCTS
    assert len(follow) == LOW_PRODUCTS * 3


def test_every_follow_belongs_to_the_low_dataset():
    assert {f.dataset for f in _ranking_out().follow} == {Dataset.REVIEW_LOW}


def test_the_asc_follow_carries_the_ceiling_not_a_fixed_page_count():
    reviews = [f for f in _ranking_out().follow if f.ctx("sort") == "RATING_ASC"]
    assert {f.ctx("pages") for f in reviews} == {str(LOW_ASC_PAGES_MAX)}
    assert LOW_ASC_PAGES_MAX > REVIEW_PAGES


# --- RATING_ASC stops when a 3-star review appears -------------------------------


def test_a_page_with_no_three_star_review_keeps_walking():
    rows = [_row(1, 1), _row(2, 1), _row(3, 2)]
    out = OliveYoung().parse(_review_payload(rows, sort="RATING_ASC", page=0))
    assert len(out.follow) == 1
    assert out.follow[0].ctx("page") == "1"


def test_a_page_containing_a_three_star_review_stops_the_walk():
    rows = [_row(1, 2), _row(2, 3), _row(3, 3)]
    out = OliveYoung().parse(_review_payload(rows, sort="RATING_ASC", page=1))
    assert out.follow == ()


def test_the_three_star_stop_is_recorded_even_when_the_site_still_says_hasnext():
    # The point of #7: the boundary is content, not the site's own pagination signal.
    rows = [_row(1, 3)]
    out = OliveYoung().parse(_review_payload(rows, sort="RATING_ASC", page=0, has_next=True))
    assert out.follow == ()


def test_the_ceiling_still_stops_a_walk_that_never_reaches_three_stars():
    rows = [_row(1, 1), _row(2, 2)]
    out = OliveYoung().parse(
        _review_payload(rows, sort="RATING_ASC", page=LOW_ASC_PAGES_MAX - 1, has_next=True)
    )
    assert out.follow == ()


def test_the_three_star_stop_does_not_apply_to_the_plain_review_dataset():
    # REVIEW_LOW's stop condition must not leak into the sampled `review` walk, which keeps its own
    # fixed-page-count behaviour.
    rows = [_row(1, 3)]
    body = _review_body(rows, has_next=True)
    fetch = Fetch(
        url=REVIEW_ENDPOINT,
        dataset=Dataset.REVIEW,
        method="POST",
        context=(("kind", "reviews"), ("product", GOODS), ("sort", "RATING_ASC"), ("page", "0")),
    )
    out = OliveYoung().parse(_payload(fetch, json.dumps(body).encode()))
    assert len(out.follow) == 1


def test_the_desc_walk_still_stops_after_one_page():
    out = OliveYoung().parse(
        Payload(
            fetch=Fetch(
                url=REVIEW_ENDPOINT,
                dataset=Dataset.REVIEW_LOW,
                method="POST",
                context=(
                    ("kind", "reviews"),
                    ("product", GOODS),
                    ("sort", "RATING_DESC"),
                    ("page", "0"),
                    ("pages", str(LOW_DESC_PAGES)),
                ),
            ),
            status=200,
            body=json.dumps(_review_body([_row(1, 5)], has_next=True)).encode(),
            final_url=REVIEW_ENDPOINT,
            headers={},
            elapsed_ms=1,
            captured_at=AT,
        )
    )
    assert out.follow == ()


# --- the budget --------------------------------------------------------------


def test_the_low_walk_leaves_retry_room_in_the_100_request_budget():
    budget = OliveYoung.policy.max_requests_per_run
    assert budget is not None
    worst_case = 1 + LOW_PRODUCTS * (LOW_ASC_PAGES_MAX + LOW_DESC_PAGES + 1)
    assert worst_case <= budget
    assert budget - worst_case >= 5
