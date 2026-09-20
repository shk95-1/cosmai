"""contracts/entrypoints.md's naver section states the vendor's two request quotas and the
`BLOG_START_MAX` wall that actually binds a run (#110). This ties the contract's numbers to the
constants so either drifting alone -- the prose or `collectors/naver/scope.py` -- fails the check
(same split as test_naver_scope_matches_constants.py, applied to prose instead of scope.json)."""

from __future__ import annotations

import re
from pathlib import Path

from collectors.naver import scope

ENTRYPOINTS = Path(__file__).resolve().parents[3] / "contracts" / "entrypoints.md"


def _naver_section() -> str:
    text = ENTRYPOINTS.read_text(encoding="utf-8")
    start = text.index("**naver over HTTP")
    end = text.index("## DB connection knobs")
    return text[start:end]


def test_the_contract_states_the_blog_daily_quota():
    section = _naver_section()
    match = re.search(r"Search API \(blog\) (\d[\d,]*)/day", section)
    assert match, "the naver section must name the blog daily quota"
    assert int(match.group(1).replace(",", "")) == scope.BLOG_DAILY_QUOTA


def test_the_contract_states_the_datalab_monthly_quota():
    section = _naver_section()
    match = re.search(r"DataLab family (\d[\d,]*)/month each", section)
    assert match, "the naver section must name the datalab monthly quota"
    assert int(match.group(1).replace(",", "")) == scope.DATALAB_MONTHLY_QUOTA


def test_the_contract_states_the_launch_onset_budget_and_what_it_costs():
    # #285, the same tie #110 made for the two quotas: the prose states the per-run ceiling, the
    # per-product cost and the month's total, and either side drifting alone fails here.
    section = _naver_section()
    ceiling = re.search(r"\*\*(\d[\d,]*) requests a run\*\*", section)
    assert ceiling, "the naver section must name the launch_onset per-run ceiling"
    assert int(ceiling.group(1).replace(",", "")) == scope.LAUNCH_MAX_REQUESTS_PER_RUN

    per_product = re.search(r"\*\*(\d+) requests a product\*\*", section)
    assert per_product, "the naver section must name what one product costs"
    assert int(per_product.group(1)) == scope.LAUNCH_REQUESTS_PER_PRODUCT

    monthly = re.search(r"(\d[\d,]*) `needs\.product_ref` rows cost \*\*(\d[\d,]*) requests", section)
    assert monthly, "the naver section must name the month's total against today's catalogue"
    refs = int(monthly.group(1).replace(",", ""))
    total = int(monthly.group(2).replace(",", ""))
    assert total == refs * scope.LAUNCH_REQUESTS_PER_PRODUCT
    assert total <= scope.LAUNCH_MAX_REQUESTS_PER_RUN <= scope.DATALAB_MONTHLY_QUOTA


def test_the_contract_states_the_two_request_shapes_the_axis_depends_on():
    # The one-group and one-term rules are the axis's design, not a knob: a contract that stopped
    # naming them would leave the next reader free to pack a request and lose a small product's
    # series to the rescale (#285).
    section = _naver_section()
    groups = re.search(r"`scope\.LAUNCH_SEARCH_GROUPS_PER_REQUEST` = (\d+)", section)
    assert groups and int(groups.group(1)) == scope.LAUNCH_SEARCH_GROUPS_PER_REQUEST == 1
    keywords = re.search(r"`scope\.SHOPPING_MAX_KEYWORDS_PER_REQUEST` = (\d+)", section)
    assert keywords and int(keywords.group(1)) == scope.SHOPPING_MAX_KEYWORDS_PER_REQUEST


def test_the_contract_states_what_actually_binds_is_the_start_wall():
    section = _naver_section()
    match = re.search(r"`BLOG_START_MAX` \((\d+)\)", section)
    assert match, "the naver section must name the start wall that binds, not the quota"
    assert int(match.group(1)) == scope.BLOG_START_MAX
    assert "quota is not what binds" in section
