"""Response-body parsing, against fixtures shaped like the vendor docs (and
architect/slice-suncare/datalab_raw_v2.jsonl's actual captured points for datalab)."""

from __future__ import annotations

from datetime import UTC, datetime

from collectors.naver import parsing

NOW = datetime(2026, 8, 24, 6, 10, tzinfo=UTC)


def test_parse_datalab_response_unrolls_series_into_points():
    body = {
        "startDate": "2016-01-01",
        "endDate": "2026-08-23",
        "timeUnit": "month",
        "results": [
            {
                "title": "백탁",
                "keywords": ["선크림 백탁", "썬크림 백탁", "선크림 하얗게"],
                "data": [
                    {"period": "2016-01-01", "ratio": 10.36074},
                    {"period": "2016-02-01", "ratio": 13.90227},
                ],
            },
            {
                "title": "눈시림",
                "keywords": ["선크림 눈시림"],
                "data": [{"period": "2016-01-01", "ratio": 33.7}],
            },
        ],
    }
    points = parsing.parse_datalab_response(body, category="선블록", captured_at=NOW)
    assert len(points) == 3
    first = points[0]
    assert first.category == "선블록"
    assert first.group_key == "백탁"
    assert first.month == "2016-01"
    assert first.ratio == 10.36074
    assert first.terms == ("선크림 백탁", "썬크림 백탁", "선크림 하얗게")
    assert first.captured_at == NOW


def test_parse_datalab_response_skips_a_point_with_no_ratio_gracefully():
    body = {"results": [{"title": "밀림", "keywords": [], "data": [{"period": "2016-01-01"}]}]}
    points = parsing.parse_datalab_response(body, category="선블록", captured_at=NOW)
    assert len(points) == 1
    assert points[0].ratio is None


def test_parse_datalab_response_with_no_results_key_returns_nothing():
    assert parsing.parse_datalab_response({}, category="선블록", captured_at=NOW) == []


def test_parse_blog_response_strips_markup_and_parses_postdate():
    body = {
        "items": [
            {
                "title": "선크림 <b>백탁</b> 진짜 심해요",
                "link": "https://blog.naver.com/abc/1",
                "description": "이 선크림은 <b>백탁</b>이 &amp; 심함",
                "bloggername": "review_lover",
                "bloggerlink": "blog.naver.com/abc",
                "postdate": "20260801",
            }
        ]
    }
    posts = parsing.parse_blog_response(
        body, category="선블록", group_key="백탁", query="선크림 백탁", captured_at=NOW
    )
    assert len(posts) == 1
    post = posts[0]
    assert post.post_id == "https://blog.naver.com/abc/1"
    assert post.title == "선크림 백탁 진짜 심해요"
    assert post.excerpt == "이 선크림은 백탁이 & 심함"
    assert post.author == "review_lover"
    assert post.published_at is not None
    assert post.published_at.isoformat() == "2026-08-01"
    assert post.observed_at_resolution == "day"
    assert post.category == "선블록"
    assert post.group_key == "백탁"
    assert post.query == "선크림 백탁"


def test_parse_blog_response_skips_an_item_with_no_link():
    body = {"items": [{"title": "no link here"}]}
    assert parsing.parse_blog_response(body, category=None, group_key=None, query=None, captured_at=NOW) == []


def test_parse_blog_response_keeps_a_post_with_an_unparseable_postdate():
    body = {"items": [{"link": "https://blog.naver.com/x/2", "postdate": "not-a-date"}]}
    posts = parsing.parse_blog_response(body, category=None, group_key=None, query=None, captured_at=NOW)
    assert len(posts) == 1
    assert posts[0].published_at is None
    assert posts[0].title == ""


def test_blog_page_is_empty_when_items_is_an_empty_list():
    assert parsing.blog_page_is_empty({"items": []}) is True
    assert parsing.blog_page_is_empty({"items": [{"link": "x"}]}) is False
    assert parsing.blog_page_is_empty({}) is True
