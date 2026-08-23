"""시드 run 3개를 골든으로 두고 RuleAggregator 가 같은 입력에서 무엇을 다르게 내는지 잰다 (#4 1차)."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from analysis.aggregate import WISH_SCOPES, RuleAggregator
from analysis.aggregate.pipeline import load_denominators, load_needs, load_wishes
from db import seed
from db.seed._common import DEFAULT_SLICES, REPO_ROOT, connect

pytestmark = pytest.mark.postgres

CANDIDATES = [DEFAULT_SLICES, REPO_ROOT.parents[1] / "architect"]
TOLERANCE = 0.01
SAMPLES = 6

# population_share_pct 는 빠져 있다: 시드의 p1 값은 계약 수식이 아니라 수집 표본 근사다 (interfaces.md B7).
NEED_COLUMNS = (
    "neg", "pos", "yt_neg", "yt_pos", "unresolved", "unresolved_new", "low_share", "low_mentioning",
    "denom_low", "denom_site", "strength_mean", "strength_low_rating_ratio", "persist_months",
    "persist_months_total", "persist_products", "persist_products_total", "aspect_scope",
)  # fmt: skip
WISH_COLUMNS = (
    "mentions", "channels", "videos", "months_present", "first_month", "last_month", "like_sum",
    "max_like", "example",
)  # fmt: skip

# 1차 패스 실측(2026-08-24). 줄어들면 2차 패스가 전진한 것이고, 늘어나면 회귀다.
EXPECTED: Mapping[str, Mapping[str, int]] = {
    # unresolved_new 는 제품의 first_seen 을 요구하는데 Aggregator 는 그것을 받지 못한다 (보고서 참조).
    "suncare": {"missing": 0, "extra": 0, "unresolved_new": 15},
    # neg/pos/persist_* 의 9행은 전부 '선블록' 이다: p1 이 재추출한 리뷰 548건이 need_mention 의
    # UNIQUE (src, ref, need_key, sentence) 에서 slice-suncare 행에 흡수되어 입력에 없다.
    # low_* 는 그 위에, 골든이 세는 중립 극성 언급이 need_mention 에 저장되지 않아 더 벌어진다.
    "p1": {
        "missing": 0,
        "extra": 0,
        "neg": 9,
        "pos": 9,
        "unresolved": 9,
        "persist_months": 9,
        "persist_products": 3,
        "low_mentioning": 93,
        "low_share": 69,
    },
    # 시드가 wish_mention.channel_id 를 채우지 않는다 — 채널 수는 입력에 존재하지 않는다.
    "p9": {"missing": 0, "extra": 0, "channels": 601},
}


@pytest.fixture(scope="module")
def slices() -> Path:
    named = os.environ.get("COSMAI_SLICES_DIR")
    found = [Path(named)] if named else [p for p in CANDIDATES if p.is_dir()]
    if not found or not found[0].is_dir():
        pytest.skip(f"no slice-*/ under {CANDIDATES}; pass COSMAI_SLICES_DIR")
    return found[0]


def _golden(cur: Any, table: str, keys: str, columns: Sequence[str], note: str, extra: str = "") -> dict:
    cur.execute(
        f"SELECT {keys}, {', '.join(columns)} FROM {table} "  # noqa: S608 - 컬럼은 이 모듈의 상수다
        f"WHERE run_id = (SELECT run_id FROM analysis_run WHERE note = %s) {extra}",
        (note,),
    )
    width = len(keys.split(","))
    return {tuple(r[:width]): dict(zip(columns, r[width:], strict=True)) for r in cur.fetchall()}


def _same(golden: Any, got: Any) -> tuple[bool, float]:
    if isinstance(golden, str) or golden is None or got is None:
        return golden == got, 0.0
    error = abs(float(golden) - float(got if got is not None else 0))
    return error <= TOLERANCE, error


def _measure(name: str, golden: dict, got: dict, columns: Sequence[str]) -> tuple[dict, list[str]]:
    counts = {"missing": len(set(golden) - set(got)), "extra": len(set(got) - set(golden))}
    worst: dict[str, float] = {}
    samples: list[str] = []
    for key in sorted(set(golden) & set(got), key=str):
        for column in columns:
            expected = golden[key][column]
            if expected is None:  # 골든이 재지 않은 칸은 비교 대상이 아니다.
                continue
            ok, error = _same(expected, got[key][column])
            if ok:
                continue
            counts[column] = counts.get(column, 0) + 1
            worst[column] = max(worst.get(column, 0.0), error)
            if len(samples) < SAMPLES:
                samples.append(f"{name} {key} {column}: golden {expected!r} != {got[key][column]!r}")
    cells = sum(1 for k in set(golden) & set(got) for c in columns if golden[k][c] is not None)
    hit = cells - sum(v for c, v in counts.items() if c not in ("missing", "extra"))
    samples.append(
        f"{name}: rows {len(golden)} golden / {len(got)} computed, cells {hit}/{cells} "
        f"({hit / cells:.1%}), worst { {c: round(e, 4) for c, e in sorted(worst.items())} }"
    )
    samples.append(f"{name}: 어긋난 행 수 {dict(sorted(counts.items()))}")
    return counts, samples


def _rows(rows: Iterable[Any], keys: Sequence[str], columns: Sequence[str]) -> dict:
    return {tuple(getattr(r, k) for k in keys): {c: getattr(r, c) for c in columns} for r in rows}


def test_the_aggregator_is_measured_against_the_three_seed_goldens(needs_runtime_url: str, slices: Path):
    seed.run_all(needs_runtime_url, slices=slices)
    aggregator = RuleAggregator()
    with connect(needs_runtime_url) as conn, conn.cursor() as cur:
        denominators = load_denominators(cur)
        suncare = load_needs(cur, ("slice-suncare",))
        # slice-p1 은 분모가 있는 올리브영만 집계했다; 다른 사이트의 같은 카테고리명은 골든에 없다.
        p1 = [m for m in load_needs(cur, ("slice-p1",)) if m.site == "oliveyoung"]
        wishes = load_wishes(cur, ("slice-p9",))

        goldens = {
            "suncare": (
                _golden(cur, "metrics_need", "scope, need_key", NEED_COLUMNS, "seed:slice-suncare",
                        "AND product_ref = '' AND month = ''"),
                _rows(aggregator.need_metrics(suncare, [], "선블록"), ("scope", "need_key"), NEED_COLUMNS),
            ),
            "p1": (
                _golden(cur, "metrics_need", "scope, need_key", NEED_COLUMNS, "seed:slice-p1"),
                _rows(
                    [r for s in {d.category for d in denominators if d.category}
                     for r in aggregator.need_metrics(p1, denominators, s)],
                    ("scope", "need_key"), NEED_COLUMNS,
                ),
            ),
            "p9": (
                _golden(cur, "metrics_wish", "scope, format, attribute, brand", WISH_COLUMNS,
                        "seed:slice-p9"),
                _rows(
                    [r for s in WISH_SCOPES for r in aggregator.wish_metrics(wishes, s)],
                    ("scope", "format", "attribute", "brand"), WISH_COLUMNS,
                ),
            ),
        }  # fmt: skip

    measured: dict[str, Mapping[str, int]] = {}
    report: list[str] = []
    for name, (golden, got) in goldens.items():
        columns = WISH_COLUMNS if name == "p9" else NEED_COLUMNS
        counts, samples = _measure(name, golden, got, columns)
        measured[name] = counts
        report += samples
    print("\n".join(report))
    assert measured == EXPECTED, "\n".join(report)
