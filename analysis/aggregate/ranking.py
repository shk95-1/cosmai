"""랭킹·가격·분모 파생 (contracts/ddl/needs/001·002). trend_radar 읽기 → needs 자연키 upsert."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import astuple, dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql

from analysis.aggregate import LOW_RATING
from analysis.types import DenominatorRow

__all__ = [
    "ABSENT_RANK",
    "KST",
    "LOW_COMPLETE_THRESHOLD",
    "PriceEventRow",
    "PricePoint",
    "RankDailyRow",
    "RankSnapshot",
    "ReviewRow",
    "ReviewStatsRow",
    "denominators",
    "price_events",
    "rank_daily",
    "run_ranking",
    "scope_threshold",
]

KST = timezone(timedelta(hours=9))
# 창 안에 그 시각의 보드 스냅샷이 있는데 제품이 없으면 '순위 밖'이다 — 결측이 아니라 상수 벌점 (slice-p2).
ABSENT_RANK = 101
# collectors/commerce/scope.json 의 oliveyoung.review_low.low_complete_threshold (#7) 와 같은 값이다.
LOW_COMPLETE_THRESHOLD = 150
SCOPE_JSON = Path(__file__).resolve().parents[2] / "collectors" / "commerce" / "scope.json"
CATEGORY_SEP = " > "
WINDOWS = (6, 12, 24)


@dataclass(frozen=True)
class RankSnapshot:
    source: str
    board: str
    category_key: str
    product_key: str
    captured_at: datetime
    rank: int
    price: int | None = None
    category_name: str | None = None


@dataclass(frozen=True)
class PricePoint:
    source: str
    product_key: str
    captured_at: datetime
    price: int


@dataclass(frozen=True)
class ReviewRow:
    source: str
    product_key: str
    review_key: str
    rating: float | None


@dataclass(frozen=True)
class ReviewStatsRow:
    source: str
    product_key: str
    review_count: int | None
    pct_1: int | None
    pct_2: int | None


@dataclass(frozen=True)
class RankDailyRow:
    source: str
    board: str
    category_key: str
    product_key: str
    day_kst: date
    n_snapshots: int
    n_present: int
    present_share: float
    rank_mean: float
    rank_min: int
    rank_max: int
    price_mode: int | None
    aggregate_version: str


@dataclass(frozen=True)
class PriceEventRow:
    source: str
    product_key: str
    board: str
    t_change: datetime
    price_before: int
    price_after: int
    pct: float
    direction: str
    rank_pre6: float
    rank_post6: float
    rank_post12: float | None
    rank_post24: float | None
    n_pre: int
    n_post24: int
    aggregate_version: str


def scope_threshold() -> int:
    """상수가 수집기의 표본 설계와 갈라지면 low_complete 가 조용히 틀린다 (entrypoints.md)."""
    return int(json.loads(SCOPE_JSON.read_text())["oliveyoung"]["review_low"]["low_complete_threshold"])


def rank_daily(snapshots: Iterable[RankSnapshot], version: str) -> list[RankDailyRow]:
    rows = list(snapshots)
    # 보드가 그 날 몇 번 찍혔는지는 제품과 무관하다 — 미등장을 세려면 분모가 먼저 필요하다 (A16).
    board_times: dict[tuple[str, str, str, date], set[datetime]] = defaultdict(set)
    seen: dict[tuple[str, str, str, str, date], list[RankSnapshot]] = defaultdict(list)
    for s in rows:
        day = s.captured_at.astimezone(KST).date()
        board_times[(s.source, s.board, s.category_key, day)].add(s.captured_at)
        seen[(s.source, s.board, s.category_key, s.product_key, day)].append(s)
    out: list[RankDailyRow] = []
    for (source, board, category_key, product_key, day), group in sorted(seen.items()):
        ranks = [s.rank for s in group]
        prices = [s.price for s in group if s.price is not None]
        n_snapshots = len(board_times[(source, board, category_key, day)])
        out.append(
            RankDailyRow(
                source=source,
                board=board,
                category_key=category_key,
                product_key=product_key,
                day_kst=day,
                n_snapshots=n_snapshots,
                n_present=len(ranks),
                present_share=len(ranks) / n_snapshots,
                rank_mean=mean(ranks),
                rank_min=min(ranks),
                rank_max=max(ranks),
                price_mode=Counter(prices).most_common(1)[0][0] if prices else None,
                aggregate_version=version,
            )
        )
    return out


def _hour(moment: datetime) -> int:
    return int(moment.timestamp() // 3600)


def price_events(
    prices: Iterable[PricePoint], snapshots: Iterable[RankSnapshot], version: str
) -> list[PriceEventRow]:
    ranks: dict[tuple[str, str, str], dict[int, int]] = defaultdict(dict)
    board_hours: dict[tuple[str, str], set[int]] = defaultdict(set)
    for s in snapshots:
        ranks[(s.source, s.board, s.product_key)][_hour(s.captured_at)] = s.rank
        board_hours[(s.source, s.board)].add(_hour(s.captured_at))

    def window(source: str, board: str, product: str, start: int, end: int) -> tuple[float | None, int]:
        hours = [h for h in board_hours[(source, board)] if start <= h < end]
        if not hours:
            return None, 0
        present = ranks[(source, board, product)]
        return mean(present.get(h, ABSENT_RANK) for h in hours), len(hours)

    series: dict[tuple[str, str], list[PricePoint]] = defaultdict(list)
    for point in prices:
        series[(point.source, point.product_key)].append(point)
    out: list[PriceEventRow] = []
    for (source, product), points in sorted(series.items()):
        points.sort(key=lambda p: p.captured_at)
        for before, after in zip(points, points[1:], strict=False):
            if before.price == after.price:
                continue
            change = _hour(after.captured_at)
            for _, board, _ in sorted(k for k in ranks if k[0] == source and k[2] == product):
                pre, n_pre = window(source, board, product, change - WINDOWS[0], change)
                post6, _ = window(source, board, product, change, change + WINDOWS[0])
                post12, _ = window(source, board, product, change, change + WINDOWS[1])
                post24, n_post24 = window(source, board, product, change, change + WINDOWS[2])
                if pre is None or post6 is None:
                    continue
                out.append(
                    PriceEventRow(
                        source=source,
                        product_key=product,
                        board=board,
                        t_change=datetime.fromtimestamp(change * 3600, tz=UTC),
                        price_before=before.price,
                        price_after=after.price,
                        pct=(after.price - before.price) / before.price * 100,
                        direction="drop" if after.price < before.price else "rise",
                        rank_pre6=pre,
                        rank_post6=post6,
                        rank_post12=post12,
                        rank_post24=post24,
                        n_pre=n_pre,
                        n_post24=n_post24,
                        aggregate_version=version,
                    )
                )
    return out


def denominators(
    reviews: Iterable[ReviewRow],
    stats: Iterable[ReviewStatsRow],
    categories: dict[tuple[str, str], str],
    captured_at: date,
    version: str,
) -> list[DenominatorRow]:
    collected: dict[tuple[str, str], list[ReviewRow]] = defaultdict(list)
    for review in reviews:
        collected[(review.source, review.product_key)].append(review)
    reported = {(s.source, s.product_key): s for s in stats}
    out: list[DenominatorRow] = []
    for (source, product), group in sorted(collected.items()):
        ratings = [r.rating for r in group if r.rating is not None]
        low = sum(1 for r in ratings if r <= LOW_RATING)
        stat = reported.get((source, product))
        site_low_pct = ((stat.pct_1 or 0) + (stat.pct_2 or 0)) / 100 if stat else 0
        out.append(
            DenominatorRow(
                source=source,
                product_key=product,
                captured_at=captured_at,
                category=(categories.get((source, product)) or "").split(CATEGORY_SEP)[-1] or None,
                site_review_count=stat.review_count if stat else None,
                low_collected=low,
                # interfaces.md: 표본이 상한에 닿지 않았거나 3★ 이 섞였으면 ≤2★ 는 전수다.
                low_complete=low < LOW_COMPLETE_THRESHOLD or any(r == 3 for r in ratings),
                site_low_est=round((stat.review_count or 0) * site_low_pct) if stat else None,
            )
        )
    return out


RANK_SQL: LiteralString = """
INSERT INTO rank_daily
  (source, board, category_key, product_key, day_kst, n_snapshots, n_present, present_share,
   rank_mean, rank_min, rank_max, price_mode, aggregate_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source, board, category_key, product_key, day_kst) DO UPDATE
SET n_snapshots = EXCLUDED.n_snapshots, n_present = EXCLUDED.n_present,
    present_share = EXCLUDED.present_share, rank_mean = EXCLUDED.rank_mean,
    rank_min = EXCLUDED.rank_min, rank_max = EXCLUDED.rank_max, price_mode = EXCLUDED.price_mode,
    aggregate_version = EXCLUDED.aggregate_version
"""
PRICE_SQL: LiteralString = """
INSERT INTO price_event
  (source, product_key, board, t_change, price_before, price_after, pct, direction,
   rank_pre6, rank_post6, rank_post12, rank_post24, n_pre, n_post24, aggregate_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source, product_key, board, t_change) DO UPDATE
SET price_before = EXCLUDED.price_before, price_after = EXCLUDED.price_after, pct = EXCLUDED.pct,
    direction = EXCLUDED.direction, rank_pre6 = EXCLUDED.rank_pre6, rank_post6 = EXCLUDED.rank_post6,
    rank_post12 = EXCLUDED.rank_post12, rank_post24 = EXCLUDED.rank_post24, n_pre = EXCLUDED.n_pre,
    n_post24 = EXCLUDED.n_post24, aggregate_version = EXCLUDED.aggregate_version
"""
DENOMINATOR_SQL: LiteralString = """
INSERT INTO product_denominator
  (source, product_key, captured_at, category, site_review_count, low_collected, low_complete,
   site_low_est, aggregate_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source, product_key, captured_at) DO UPDATE
SET category = EXCLUDED.category, site_review_count = EXCLUDED.site_review_count,
    low_collected = EXCLUDED.low_collected, low_complete = EXCLUDED.low_complete,
    site_low_est = EXCLUDED.site_low_est, aggregate_version = EXCLUDED.aggregate_version
"""


def _from(schema: str, table: str) -> pgsql.Identifier:
    """운영은 trend_radar 스키마를, 테스트는 검사용 스키마 하나를 쓴다 (tests/conftest.py)."""
    return pgsql.Identifier(schema, table) if schema else pgsql.Identifier(table)


def run_ranking(
    conn: psycopg.Connection[Any],
    version: str,
    captured_at: date,
    source_schema: str = "trend_radar",
) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            pgsql.SQL(
                "SELECT source, board, category_key, product_key, captured_at, rank, price, "
                "category_name FROM {}"
            ).format(_from(source_schema, "rank_snapshot"))
        )
        snapshots = [RankSnapshot(*row) for row in cur.fetchall()]
        cur.execute(
            pgsql.SQL("SELECT source, product_key, captured_at, price FROM {}").format(
                _from(source_schema, "price_point")
            )
        )
        prices = [PricePoint(*row) for row in cur.fetchall()]
        cur.execute(
            pgsql.SQL("SELECT source, product_key, review_key, rating FROM {}").format(
                _from(source_schema, "review")
            )
        )
        reviews = [ReviewRow(*row) for row in cur.fetchall()]
        cur.execute(
            pgsql.SQL(
                "SELECT DISTINCT ON (source, product_key) source, product_key, review_count, "
                "pct_1, pct_2 FROM {} ORDER BY source, product_key, captured_at DESC"
            ).format(_from(source_schema, "review_stats"))
        )
        stats = [ReviewStatsRow(*row) for row in cur.fetchall()]

        # 리뷰 행에는 카테고리가 없다 — 사이트가 그 제품을 걸어 둔 보드 이름이 유일한 출처다 (B6).
        categories = {(s.source, s.product_key): s.category_name for s in snapshots if s.category_name}
        daily = rank_daily(snapshots, version)
        events = price_events(prices, snapshots, version)
        denoms = denominators(reviews, stats, categories, captured_at, version)
        cur.executemany(RANK_SQL, [astuple(r) for r in daily])
        cur.executemany(PRICE_SQL, [astuple(r) for r in events])
        cur.executemany(DENOMINATOR_SQL, [(*astuple(r), version) for r in denoms])
    conn.commit()
    return {"rank_daily": len(daily), "price_event": len(events), "product_denominator": len(denoms)}
