"""#123: 카테고리 표기의 정본 — 분모와 언급이 같은 문자열을 적지 않으면 카테고리 scope 는 분모를 못 받는다.

`metrics_need.scope` 는 `need_mention.category` 에서 나오고(analysis/aggregate/pipeline.py:scopes_for),
그 값은 사이트가 발행한 경로 원문이다. 분모가 그 경로를 leaf 로 자르면 두 문자열은 절대 만나지 못하고
`population_share_pct`·`low_share`·`denom_*` 가 카테고리 scope 전체에서 결측이 된다 (운영 실측 run 24).
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

# 운영이 실제로 들고 있는 두 모양 — oliveyoung 은 경로를, glowpick 은 leaf 하나를 발행한다
# (trend_radar.rank_snapshot 실측 2026-08-27: oliveyoung 200 경로 전부 'N > 중분류 > leaf').
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
    """두 자리가 같은 원천(rank_snapshot.category_name)을 받으므로 같은 문자열이어야 한다."""
    unit = review_unit(
        source="oliveyoung", product_key="p", review_key="r0", body="", rating=1.0,
        written_at=date(2026, 1, 1), captured_at=date(2026, 1, 1), category=PATH,
    )  # fmt: skip
    assert _denominator(PATH).category == unit.category


def test_a_category_scope_gets_its_denominator_and_population_share():
    """#123 의 완료 기준 — 카테고리 scope 에서 population_share_pct 가 결측이 아니다."""
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
    """polarity 쪽은 captured_at DESC 로 하나를 고른다 (analysis/polarity/pipeline.py:288). 분모 쪽이
    fetch 순서에 맡기면 보드를 옮긴 제품에서 두 자리가 서로 다른 카테고리를 적는다."""
    old = RankSnapshot("oliveyoung", "b", "c", "p", datetime(2026, 8, 1, tzinfo=UTC), 3, None, LEAF_ONLY)
    new = RankSnapshot("oliveyoung", "b", "c", "p", datetime(2026, 8, 20, tzinfo=UTC), 3, None, PATH)
    from analysis.aggregate.ranking import latest_categories

    assert latest_categories([new, old]) == {("oliveyoung", "p"): PATH}
    assert latest_categories([old, new]) == {("oliveyoung", "p"): PATH}


def test_the_contract_and_the_module_name_the_same_canonical_places():
    """contracts/formats.md §카테고리 표기 가 정본이고, 코드가 그 값을 상수로 들고 있어야 대조가 된다."""
    declared: dict[str, object] = {}
    for block in CODE_BLOCK.findall(FORMATS.read_text(encoding="utf-8")):
        for node in ast.parse(block).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                declared[node.targets[0].id] = ast.literal_eval(node.value)
    assert declared["CATEGORY_CANONICAL_SOURCE"] == CATEGORY_CANONICAL_SOURCE
    assert declared["CATEGORY_CANONICAL_COLUMNS"] == CATEGORY_CANONICAL_COLUMNS
