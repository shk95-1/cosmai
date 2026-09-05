"""#123: the canonical spelling of a category -- unless the denominator and the mentions write the same
string, a category scope gets no denominator.

`metrics_need.scope` comes from `need_mention.category` (analysis/aggregate/pipeline.py:scopes_for), and
that value is the path the site published. If the denominator cuts that path to its leaf, the two strings
never meet and `population_share_pct` · `low_share` · `denom_*` go missing across the whole category scope
(measured in production, run 24).
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, date, datetime
from pathlib import Path

from analysis.aggregate import AGGREGATE_VERSION, RuleAggregator
from analysis.aggregate.ranking import RankSnapshot, ReviewRow, ReviewStatsRow, denominators
from analysis.types import NeedMentionRow
from analysis.units import CATEGORY_CANONICAL_COLUMNS, CATEGORY_CANONICAL_SOURCE, review_unit

ROOT = Path(__file__).resolve().parents[1]
FORMATS = ROOT / "contracts" / "formats.md"
CODE_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)

# The two shapes production really holds -- oliveyoung publishes a path and glowpick a single leaf
# (measured on trend_radar.rank_snapshot 2026-08-27: all 200 oliveyoung paths are 'N > mid > leaf').
PATH = "01 > 선케어 > 선블록"
LEAF_ONLY = "크림"


def _mention(category: str, ref: str, rating: float, polarity: str = "불만") -> NeedMentionRow:
    return NeedMentionRow(
        src="review", site="oliveyoung", ref=ref, product_ref="oy:p",
        source_product_key=ref.split("/", 1)[0], category=category, lexicon_category="선블록",
        need_key="백탁", aspect_scope="category", polarity=polarity, strength=1 - rating / 5,
        rating=rating, observed_at=date(2026, 1, 1), observed_at_resolution="day", month="2026-01",
        sentence=ref, kind=None, marker=None, polarity_reason=None, extractor_version="t",
        polarity_version="t",
    )  # fmt: skip


def _denominator(category: str):
    reviews = [ReviewRow("oliveyoung", "p", f"r{i}", 1.0) for i in range(10)]
    stats = [ReviewStatsRow("oliveyoung", "p", 1000, 3, 2)]
    (row,) = denominators(
        reviews, stats, {("oliveyoung", "p"): category}, date(2026, 8, 23), AGGREGATE_VERSION
    )
    return row


def test_the_denominator_keeps_the_whole_category_path_the_site_published():
    assert _denominator(PATH).category == PATH


def test_a_site_that_publishes_one_level_is_already_canonical():
    assert _denominator(LEAF_ONLY).category == LEAF_ONLY


def test_the_denominator_writes_the_same_string_the_mention_carries():
    """Both places take the same source (rank_snapshot.category_name), so they have to be the same string."""
    unit = review_unit(
        source="oliveyoung", product_key="p", review_key="r0", body="", rating=1.0,
        written_at=date(2026, 1, 1), captured_at=date(2026, 1, 1), category=PATH,
    )  # fmt: skip
    assert _denominator(PATH).category == unit.category


def test_a_category_scope_gets_its_denominator_and_population_share():
    """The completion criterion of #123 -- population_share_pct is not missing on a category scope."""
    mentions = [_mention(PATH, f"p/{i}", 1.0) for i in range(3)]
    (row,) = [
        r
        for r in RuleAggregator().need_metrics(mentions, [_denominator(PATH)], PATH)
        if not r.month and not r.product_ref
    ]
    assert (row.denom_low, row.denom_site) == (10, 1000)
    assert row.low_share == 3 / 10
    assert row.population_share_pct is not None


def test_the_latest_snapshot_names_the_category():
    """The polarity side picks one by captured_at DESC (analysis/polarity/pipeline.py:288). If the denominator
    side leaves it to the fetch order, the two places write different categories for a product that moved
    boards."""
    old = RankSnapshot("oliveyoung", "b", "c", "p", datetime(2026, 8, 1, tzinfo=UTC), 3, None, LEAF_ONLY)
    new = RankSnapshot("oliveyoung", "b", "c", "p", datetime(2026, 8, 20, tzinfo=UTC), 3, None, PATH)
    from analysis.aggregate.ranking import latest_categories

    assert latest_categories([new, old]) == {("oliveyoung", "p"): PATH}
    assert latest_categories([old, new]) == {("oliveyoung", "p"): PATH}


def test_the_contract_and_the_module_name_the_same_canonical_places():
    """contracts/formats.md §Category notation is canonical, and the code has to hold that value as a constant
    for the comparison to be possible."""
    declared: dict[str, object] = {}
    for block in CODE_BLOCK.findall(FORMATS.read_text(encoding="utf-8")):
        for node in ast.parse(block).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                declared[node.targets[0].id] = ast.literal_eval(node.value)
    assert declared["CATEGORY_CANONICAL_SOURCE"] == CATEGORY_CANONICAL_SOURCE
    assert declared["CATEGORY_CANONICAL_COLUMNS"] == CATEGORY_CANONICAL_COLUMNS
