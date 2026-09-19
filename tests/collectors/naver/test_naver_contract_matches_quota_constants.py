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


def test_the_contract_states_what_actually_binds_is_the_start_wall():
    section = _naver_section()
    match = re.search(r"`BLOG_START_MAX` \((\d+)\)", section)
    assert match, "the naver section must name the start wall that binds, not the quota"
    assert int(match.group(1)) == scope.BLOG_START_MAX
    assert "quota is not what binds" in section
