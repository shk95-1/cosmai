"""회귀: 분기 입자가 옆에 서도 월 기반 산출은 한 줄도 달라지지 않는다 (포크 #3).

월은 `need_mention` 과 `metrics_*` 전체가 딛고 선 입자다. 분기를 기존 표에 `granularity` 같은 열로
얹으면 **이미 있는 행의 뜻이 바뀌므로** 별도 표에 뒀고(이슈 #3 결정 2), 그 결정이 지켜지는 자리는
둘이다: 월 표의 모양과, 집계기가 같은 입력에서 내는 행. 둘 다 여기서 얼린다 -- 뜻이 바뀌는 변경은
오류를 내지 않고, 숫자만 조용히 다른 것이 된다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import fields
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from analysis.aggregate import WISH_SCOPES, RuleAggregator
from analysis.types import (
    DenominatorRow,
    MetricsNeedRow,
    MetricsWishRow,
    NeedMentionRow,
    WishMentionRow,
)

DDL_DIR = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "needs"
MONTH_TABLES = ("need_mention", "metrics_need", "metrics_wish")

# 2026-08-26 의 월 표. 001 의 CREATE 와 그 뒤 마이그레이션의 ADD COLUMN 을 순서대로 이은 것이다.
FROZEN_COLUMNS: dict[str, tuple[str, ...]] = {
    "need_mention": (
        "mention_id",
        "src",
        "site",
        "ref",
        "product_ref",
        "source_product_key",
        "category",
        "need_key",
        "aspect_scope",
        "polarity",
        "strength",
        "rating",
        "observed_at",
        "observed_at_resolution",
        "month",
        "sentence",
        "extractor_version",
        "polarity_version",
        "polarity_reason",
        "lexicon_category",
        "kind",
        "marker",
    ),  # fmt: skip
    "metrics_need": (
        "run_id",
        "scope",
        "need_key",
        "month",
        "product_ref",
        "neg",
        "pos",
        "unresolved",
        "low_share",
        "population_share_pct",
        "strength_low_rating_ratio",
        "persist_months",
        "persist_products",
        "yt_neg",
        "yt_pos",
        "persist_months_total",
        "persist_products_total",
        "strength_mean",
        "unresolved_new",
        "aspect_scope",
        "low_mentioning",
        "denom_low",
        "denom_site",
    ),  # fmt: skip
    "metrics_wish": (
        "run_id",
        "scope",
        "format",
        "attribute",
        "brand",
        "mentions",
        "channels",
        "months_present",
        "like_sum",
        "like_cap_sum",
        "videos",
        "max_like",
        "first_month",
        "last_month",
        "example",
    ),  # fmt: skip
}


def _columns_from(paths: Iterable[Path], table: str) -> tuple[str, ...]:
    """DDL 파일들이 선언하는 그 표의 컬럼 -- CREATE 블록 다음에 ADD COLUMN, 파일명 순."""
    columns: list[str] = []
    for path in sorted(paths):
        body = path.read_text(encoding="utf-8")
        created = re.search(rf"CREATE TABLE needs\.{table} \((.*?)\n\);", body, re.DOTALL)
        if created:
            for line in created.group(1).splitlines():
                for part in re.sub(r"--.*", "", line).split(","):
                    name = re.match(r"\s*([a-z_]+)\s+\S", part)
                    if name and not part.strip().upper().startswith(("PRIMARY KEY", "UNIQUE", "CHECK")):
                        columns.append(name.group(1))
        columns += [
            column
            for touched, column in re.findall(r"ALTER TABLE needs\.(\w+)\s+ADD COLUMN (\w+)", body)
            if touched == table
        ]
    return tuple(columns)


@pytest.mark.parametrize("table", MONTH_TABLES)
def test_the_month_tables_carry_exactly_the_columns_they_carried(table: str):
    assert _columns_from(DDL_DIR.glob("*.sql"), table) == FROZEN_COLUMNS[table]


@pytest.mark.parametrize("table", MONTH_TABLES)
def test_the_freeze_catches_a_grain_column_smuggled_into_a_month_table(tmp_path: Path, table: str):
    """이 회귀가 무엇을 막는지 스스로 보인다 -- 월 표에 입자 열이 붙는 것이 막는 대상이다."""
    smuggled = tmp_path / "099_granularity.sql"
    smuggled.write_text(f"ALTER TABLE needs.{table} ADD COLUMN granularity text;\n", encoding="utf-8")
    assert _columns_from([*DDL_DIR.glob("*.sql"), smuggled], table) != FROZEN_COLUMNS[table]


@pytest.mark.parametrize(
    ("row_type", "frozen"),
    [
        (NeedMentionRow, (
            "src", "site", "ref", "product_ref", "source_product_key", "category", "lexicon_category",
            "need_key", "aspect_scope", "polarity", "strength", "rating", "observed_at",
            "observed_at_resolution", "month", "sentence", "kind", "marker", "polarity_reason",
            "extractor_version", "polarity_version",
        )),
        (MetricsNeedRow, (
            "run_id", "scope", "need_key", "month", "product_ref", "neg", "pos", "yt_neg", "yt_pos",
            "unresolved", "unresolved_new", "low_share", "population_share_pct", "low_mentioning",
            "denom_low", "denom_site", "strength_mean", "strength_low_rating_ratio", "persist_months",
            "persist_months_total", "persist_products", "persist_products_total", "aspect_scope",
        )),
        (MetricsWishRow, (
            "run_id", "scope", "format", "attribute", "brand", "mentions", "channels", "videos",
            "months_present", "first_month", "last_month", "like_sum", "like_cap_sum", "max_like",
            "example",
        )),
    ],
    ids=lambda value: value.__name__ if isinstance(value, type) else "",
)  # fmt: skip
def test_the_month_row_types_gained_no_field(row_type: type, frozen: tuple[str, ...]):
    # 분기 값이 이 셋 중 하나에 필드로 붙으면 같은 행이 두 입자를 뜻하게 된다.
    assert tuple(f.name for f in fields(row_type)) == frozen


def _need(
    need_key: str,
    polarity: str,
    *,
    src: str = "review",
    ref: str = "p/1",
    product: str | None = "oy:p",
    month: str = "2026-01",
    rating: float | None = None,
    strength: float | None = None,
) -> NeedMentionRow:
    return NeedMentionRow(
        src=src,
        site="oliveyoung",
        ref=ref,
        product_ref=product,
        source_product_key=ref.split("/", 1)[0],
        category="선블록",
        lexicon_category="선블록",
        need_key=need_key,
        aspect_scope="generic",
        polarity=polarity,
        strength=strength,
        rating=rating,
        observed_at=date.fromisoformat(f"{month}-01"),
        observed_at_resolution="month",
        month=month,
        sentence=f"{need_key}-{ref}-{polarity}",
        kind=None,
        marker=None,
        polarity_reason=None,
        extractor_version="t",
        polarity_version="t",
    )


def _denom(product_key: str) -> DenominatorRow:
    return DenominatorRow(
        source="oliveyoung",
        product_key=product_key,
        captured_at=date(2026, 8, 23),
        category="선블록",
        site_review_count=1000,
        low_collected=10,
        low_complete=True,
        site_low_est=100,
    )


def _wish(
    wish_class: str,
    *,
    ref: str,
    fmt: str | None = None,
    attribute: str | None = None,
    brand: str | None = None,
    like: int = 0,
) -> WishMentionRow:
    return WishMentionRow(
        src="yt_comment",
        ref=ref,
        video_id="v",
        channel_id="ch",
        channel_is_brand_owner=None,
        product_ref=None,
        observed_at=date(2026, 1, 1),
        observed_at_resolution="month",
        month="2026-01",
        wish_class=wish_class,
        brand=brand,
        format=fmt,
        attribute=attribute,
        marker=None,
        sentence="s",
        like_count=like,
        extractor_version="t",
    )


MENTIONS = (
    _need("밀림", "불만", ref="a/1", product="oy:a", month="2026-01", rating=1.0, strength=0.8),
    _need("밀림", "만족", ref="a/2", product="oy:a", month="2026-02"),
    _need("백탁", "불만", ref="b/1", product="oy:b", month="2026-02", rating=2.0, strength=0.6),
    _need("밀림", "불만", src="yt_comment", ref="v/1", product=None, month="2026-03"),
)
DENOMINATORS = (_denom("oy:a"), _denom("oy:b"))
WISHES = (
    _wish("a", ref="v/c1", fmt="세럼", brand="브랜드", like=10),
    _wish("a", ref="v/c2", fmt="세럼", attribute="산뜻", like=200),
    _wish("b", ref="v/c3", fmt="영상", like=5),
)

# 2026-08-26 실측(RuleAggregator rule-v1.0). 월에서 나오는 값이 전부 들어 있다: persist_months·
# months_present·first_month/last_month. 한 자리라도 움직이면 월 산출이 달라진 것이다.
FROZEN_NEED_ROWS = (
    MetricsNeedRow(
        run_id=0, scope="선블록", need_key="밀림", month="", product_ref="", neg=1, pos=1, yt_neg=1,
        yt_pos=0, unresolved=0.5, unresolved_new=None, low_share=0.0, population_share_pct=0.0,
        low_mentioning=0, denom_low=20, denom_site=2000, strength_mean=0.8,
        strength_low_rating_ratio=1.0, persist_months=1, persist_months_total=2, persist_products=1,
        persist_products_total=2, aspect_scope="generic",
    ),
    MetricsNeedRow(
        run_id=0, scope="선블록", need_key="백탁", month="", product_ref="", neg=1, pos=0, yt_neg=0,
        yt_pos=0, unresolved=1.0, unresolved_new=None, low_share=0.0, population_share_pct=0.0,
        low_mentioning=0, denom_low=20, denom_site=2000, strength_mean=0.6,
        strength_low_rating_ratio=1.0, persist_months=1, persist_months_total=2, persist_products=1,
        persist_products_total=2, aspect_scope="generic",
    ),
)  # fmt: skip
FROZEN_WISH_ROWS = (
    MetricsWishRow(
        run_id=0, scope="wish:a", format="", attribute="", brand="브랜드", mentions=1, channels=1,
        videos=1, months_present=1, first_month="2026-01", last_month="2026-01", like_sum=10,
        like_cap_sum=10.0, max_like=10, example="s",
    ),
    MetricsWishRow(
        run_id=0, scope="wish:a", format="", attribute="산뜻", brand="", mentions=1, channels=1,
        videos=1, months_present=1, first_month="2026-01", last_month="2026-01", like_sum=200,
        like_cap_sum=100.0, max_like=200, example="s",
    ),
    MetricsWishRow(
        run_id=0, scope="wish:a", format="세럼", attribute="", brand="", mentions=2, channels=1,
        videos=1, months_present=1, first_month="2026-01", last_month="2026-01", like_sum=210,
        like_cap_sum=110.0, max_like=200, example="s",
    ),
    MetricsWishRow(
        run_id=0, scope="wish:a:format×attr", format="세럼", attribute="", brand="", mentions=1,
        channels=1, videos=1, months_present=1, first_month="2026-01", last_month="2026-01",
        like_sum=10, like_cap_sum=10.0, max_like=10, example="s",
    ),
    MetricsWishRow(
        run_id=0, scope="wish:a:format×attr", format="세럼", attribute="산뜻", brand="", mentions=1,
        channels=1, videos=1, months_present=1, first_month="2026-01", last_month="2026-01",
        like_sum=200, like_cap_sum=100.0, max_like=200, example="s",
    ),
    MetricsWishRow(
        run_id=0, scope="wish:b", format="영상", attribute="", brand="", mentions=1, channels=1,
        videos=1, months_present=1, first_month="2026-01", last_month="2026-01", like_sum=5,
        like_cap_sum=5.0, max_like=5, example="s",
    ),
)  # fmt: skip


def _sorted_need(rows: Sequence[MetricsNeedRow]) -> tuple[MetricsNeedRow, ...]:
    return tuple(sorted(rows, key=lambda r: (r.scope, r.need_key, r.month, r.product_ref)))


def _sorted_wish(rows: Sequence[MetricsWishRow]) -> tuple[MetricsWishRow, ...]:
    return tuple(sorted(rows, key=lambda r: (r.scope, r.format, r.attribute, r.brand)))


def test_the_month_aggregation_produces_the_frozen_rows():
    rows = RuleAggregator().need_metrics(MENTIONS, DENOMINATORS, "선블록")
    assert _sorted_need(rows) == FROZEN_NEED_ROWS


def test_the_wish_aggregation_produces_the_frozen_rows():
    aggregator = RuleAggregator()
    rows = [row for scope in WISH_SCOPES for row in aggregator.wish_metrics(WISHES, scope)]
    assert _sorted_wish(rows) == FROZEN_WISH_ROWS


@pytest.mark.postgres
@pytest.mark.parametrize("table", MONTH_TABLES)
def test_the_applied_month_tables_have_exactly_the_frozen_columns(
    needs_schema: str, _schema_name: str, table: str
):
    """문서와 파일이 아니라 **적용된 스키마**를 본다 -- 뒤에 오는 마이그레이션이 무엇을 하든."""
    engine = create_engine(needs_schema)
    try:
        columns = [c["name"] for c in inspect(engine).get_columns(table, schema=_schema_name)]
    finally:
        engine.dispose()
    assert tuple(columns) == FROZEN_COLUMNS[table]
