"""The seed puts the 2026-08-23 slice + eval rows into `needs` and a second run changes nothing."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from db import seed
from db.seed._common import DEFAULT_SLICES, REPO_ROOT

pytestmark = pytest.mark.postgres

# ../architect from the repo, or ../../architect when the repo is checked out as a sibling worktree.
CANDIDATES = [DEFAULT_SLICES, REPO_ROOT.parents[1] / "architect"]

# 기대 행 수. 출처: 각 슬라이스 README.md 산출물 표 + eval/README.md 건수 열.
EXPECTED = {
    # brand 859 surface + 109 alias − 18 중복 surface, ingredient 32행 → 42 surface
    "entity_lexicon": 992,
    # aspects_generic.py GENERIC 20 + polarity.py ASPECTS 15 + SPECIFIC 37 − 선블록 중복 2
    "aspect_lexicon": 70,
    # AXIS_MAP 25축 × 그 축을 실제로 내놓은 사이트. 올영 25 = slice-p1-category-gap/
    # site_topic_raw.csv 의 topic_group, 다이소 9 = 같은 곳 site_answer_raw.csv 의 question_name.
    "site_axis_map": 34,
    "labeled_set": 760,  # eval/README.md: polarity 400 + wish 160 + brand_link 120 + product_match 80
    "product_ref": 154,  # slice-suncare 18 + slice-p2 145 − 겹침 9
    "product_member": 348,  # slice-suncare 29 + slice-p2 341 − 같은 (source, product_key) 22
    "product_denominator": 38,  # slice-p1-category-gap/README.md
    "rank_daily": 17948,  # slice-p2-ranking-dynamics/README.md
    "price_event": 363,  # slice-p2-ranking-dynamics/README.md
    "need_mention": 15498,  # slice-suncare 2,266 + slice-p1 13,780 − UNIQUE 충돌 548
    "wish_mention": 18489,  # slice-p9-wish-mining/README.md
    "brand_mention": 48481,  # slice-p3-youtube-brand-link/README.md
    "analysis_run": 3,  # 슬라이스마다 run 하나: seed:slice-{suncare,p1,p9}
    "metrics_need": 346,  # suncare 15 + 30 (suncare run) + p1 301 (p1 run)
    "metrics_wish": 601,  # slice-p9-wish-mining/wish_aggregates.csv
}


@pytest.fixture(scope="module")
def slices() -> Path:
    named = os.environ.get("COSMAI_SLICES_DIR")
    found = [Path(named)] if named else [p for p in CANDIDATES if p.is_dir()]
    if not found or not found[0].is_dir():
        pytest.skip(f"no slice-*/ under {CANDIDATES}; pass COSMAI_SLICES_DIR")
    return found[0]


def test_seed_loads_the_slice_row_counts_and_is_idempotent(needs_schema: str, slices: Path):
    first = seed.run_all(needs_schema, slices=slices)
    assert first == EXPECTED
    second = seed.run_all(needs_schema, slices=slices)
    assert second == EXPECTED
