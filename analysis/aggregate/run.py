"""`analyze aggregate` 단계: needs 스키마 읽기 → RuleAggregator → analysis_run 과 metrics_* 쓰기."""

from __future__ import annotations

import json
from typing import Any, LiteralString

import psycopg

from analysis.aggregate import ROLLUP_SCOPE, WISH_SCOPES, RuleAggregator
from analysis.types import DenominatorRow, MetricsNeedRow, MetricsWishRow, NeedMentionRow, WishMentionRow

__all__ = ["load_denominators", "load_needs", "load_wishes", "run_aggregate"]

NEED_COLUMNS = (
    "src, site, ref, product_ref, source_product_key, category, lexicon_category, need_key, "
    "aspect_scope, polarity, strength, rating, observed_at, observed_at_resolution, month, sentence, "
    "kind, marker, polarity_reason, extractor_version, polarity_version"
)
WISH_COLUMNS = (
    "src, ref, video_id, channel_id, channel_is_brand_owner, product_ref, observed_at, "
    "observed_at_resolution, month, wish_class, brand, format, attribute, marker, sentence, "
    "like_count, extractor_version"
)
DENOMINATOR_COLUMNS = (
    "source, product_key, captured_at, category, site_review_count, low_collected, low_complete, site_low_est"
)

NEED_SQL: LiteralString = """
INSERT INTO metrics_need
  (run_id, scope, need_key, month, product_ref, neg, pos, yt_neg, yt_pos, unresolved, unresolved_new,
   low_share, population_share_pct, low_mentioning, denom_low, denom_site, strength_mean,
   strength_low_rating_ratio, persist_months, persist_months_total, persist_products,
   persist_products_total, aspect_scope)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id, scope, need_key, month, product_ref) DO UPDATE
SET neg = EXCLUDED.neg, pos = EXCLUDED.pos, yt_neg = EXCLUDED.yt_neg, yt_pos = EXCLUDED.yt_pos,
    unresolved = EXCLUDED.unresolved, unresolved_new = EXCLUDED.unresolved_new,
    low_share = EXCLUDED.low_share, population_share_pct = EXCLUDED.population_share_pct,
    low_mentioning = EXCLUDED.low_mentioning, denom_low = EXCLUDED.denom_low,
    denom_site = EXCLUDED.denom_site, strength_mean = EXCLUDED.strength_mean,
    strength_low_rating_ratio = EXCLUDED.strength_low_rating_ratio,
    persist_months = EXCLUDED.persist_months, persist_months_total = EXCLUDED.persist_months_total,
    persist_products = EXCLUDED.persist_products,
    persist_products_total = EXCLUDED.persist_products_total, aspect_scope = EXCLUDED.aspect_scope
"""
WISH_SQL: LiteralString = """
INSERT INTO metrics_wish
  (run_id, scope, format, attribute, brand, mentions, channels, videos, months_present, first_month,
   last_month, like_sum, like_cap_sum, max_like, example)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id, scope, format, attribute, brand) DO UPDATE
SET mentions = EXCLUDED.mentions, channels = EXCLUDED.channels, videos = EXCLUDED.videos,
    months_present = EXCLUDED.months_present, first_month = EXCLUDED.first_month,
    last_month = EXCLUDED.last_month, like_sum = EXCLUDED.like_sum,
    like_cap_sum = EXCLUDED.like_cap_sum, max_like = EXCLUDED.max_like, example = EXCLUDED.example
"""


def _float(value: Any) -> float | None:
    """numeric 컬럼은 Decimal 로 돌아오지만 계약의 행 타입은 float 다 (interfaces.md)."""
    return None if value is None else float(value)


def load_needs(cur: psycopg.Cursor[Any], extractor: str | None = None) -> list[NeedMentionRow]:
    where = "WHERE extractor_version = %s" if extractor else ""
    cur.execute(
        f"SELECT {NEED_COLUMNS} FROM need_mention {where} ORDER BY mention_id",  # noqa: S608 - 컬럼은 상수다
        (extractor,) if extractor else (),
    )
    return [
        NeedMentionRow(
            src=r[0], site=r[1], ref=r[2], product_ref=r[3], source_product_key=r[4], category=r[5],
            lexicon_category=r[6], need_key=r[7], aspect_scope=r[8], polarity=r[9],
            strength=_float(r[10]), rating=_float(r[11]), observed_at=r[12],
            observed_at_resolution=r[13], month=r[14], sentence=r[15], kind=r[16], marker=r[17],
            polarity_reason=r[18], extractor_version=r[19], polarity_version=r[20],
        )
        for r in cur.fetchall()
    ]  # fmt: skip


def load_wishes(cur: psycopg.Cursor[Any]) -> list[WishMentionRow]:
    cur.execute(f"SELECT {WISH_COLUMNS} FROM wish_mention ORDER BY mention_id")  # noqa: S608
    return [
        WishMentionRow(
            src=r[0], ref=r[1], video_id=r[2], channel_id=r[3], channel_is_brand_owner=r[4],
            product_ref=r[5], observed_at=r[6], observed_at_resolution=r[7], month=r[8],
            wish_class=r[9], brand=r[10], format=r[11], attribute=r[12], marker=r[13],
            sentence=r[14], like_count=r[15], extractor_version=r[16],
        )
        for r in cur.fetchall()
    ]  # fmt: skip


def load_denominators(cur: psycopg.Cursor[Any]) -> list[DenominatorRow]:
    cur.execute(f"SELECT {DENOMINATOR_COLUMNS} FROM product_denominator")  # noqa: S608
    return [
        DenominatorRow(
            source=r[0], product_key=r[1], captured_at=r[2], category=r[3], site_review_count=r[4],
            low_collected=r[5], low_complete=r[6], site_low_est=_float(r[7]),
        )
        for r in cur.fetchall()
    ]  # fmt: skip


def load_canonical(cur: psycopg.Cursor[Any]) -> dict[str, str]:
    cur.execute("SELECT need_key, canonical FROM need_key")
    return dict(cur.fetchall())


def _versions(aggregator: RuleAggregator, cur: psycopg.Cursor[Any]) -> dict[str, Any]:
    """versions 는 이 run 이 실제로 읽은 산출물의 버전이다 — 분석 재현의 유일한 단서 (versioning.md)."""
    cur.execute("SELECT DISTINCT extractor_version, polarity_version FROM need_mention")
    pairs = sorted(cur.fetchall())
    cur.execute("SELECT max(version) FROM entity_lexicon")
    lexicon = cur.fetchone()
    cur.execute("SELECT DISTINCT linker_version FROM product_ref")
    return {
        "linker": ";".join(sorted(v for (v,) in cur.fetchall())) or None,
        "extractor": ";".join(sorted({e for e, _ in pairs})) or None,
        "polarity": ";".join(sorted({p for _, p in pairs})) or None,
        "aggregate": aggregator.version,
        "lexicon": lexicon[0] if lexicon else None,
    }


def _run_id(cur: psycopg.Cursor[Any], note: str, versions: dict[str, Any]) -> int:
    """note 로 찾고 없을 때만 만든다 — 재실행이 run 을 쌓으면 멱등이 관측되지 않는다."""
    cur.execute("SELECT run_id FROM analysis_run WHERE note = %s ORDER BY run_id LIMIT 1", (note,))
    found = cur.fetchone()
    payload = json.dumps(versions, ensure_ascii=False)
    if found:
        cur.execute(
            "UPDATE analysis_run SET status = 'running', finished_at = NULL, versions = %s::jsonb "
            "WHERE run_id = %s",
            (payload, found[0]),
        )
        return int(found[0])
    cur.execute(
        "INSERT INTO analysis_run (status, versions, note) VALUES ('running', %s::jsonb, %s) "
        "RETURNING run_id",
        (payload, note),
    )
    created = cur.fetchone()
    assert created is not None
    return int(created[0])


def _need_values(row: MetricsNeedRow, run_id: int) -> tuple[Any, ...]:
    return (
        run_id, row.scope, row.need_key, row.month, row.product_ref, row.neg, row.pos, row.yt_neg,
        row.yt_pos, row.unresolved, row.unresolved_new, row.low_share, row.population_share_pct,
        row.low_mentioning, row.denom_low, row.denom_site, row.strength_mean,
        row.strength_low_rating_ratio, row.persist_months, row.persist_months_total,
        row.persist_products, row.persist_products_total, row.aspect_scope,
    )  # fmt: skip


def _wish_values(row: MetricsWishRow, run_id: int) -> tuple[Any, ...]:
    return (
        run_id, row.scope, row.format, row.attribute, row.brand, row.mentions, row.channels,
        row.videos, row.months_present, row.first_month, row.last_month, row.like_sum,
        row.like_cap_sum, row.max_like, row.example,
    )  # fmt: skip


def run_aggregate(conn: psycopg.Connection[Any], scope: str | None = None) -> int:
    aggregator = RuleAggregator()
    with conn.cursor() as cur:
        mentions = load_needs(cur)
        wishes = load_wishes(cur)
        denominators = load_denominators(cur)
        aggregator = RuleAggregator(canonical=load_canonical(cur))
        note = f"aggregate:{aggregator.version}:{scope or ROLLUP_SCOPE}"
        run_id = _run_id(cur, note, _versions(aggregator, cur))

        scopes = [scope] if scope else sorted({m.category for m in mentions if m.category} | {ROLLUP_SCOPE})
        need_rows = [r for s in scopes for r in aggregator.need_metrics(mentions, denominators, s)]
        wish_rows = [r for s in WISH_SCOPES for r in aggregator.wish_metrics(wishes, s)]

        # 이 run 의 행은 전부 이 실행이 만든다 — 지우고 다시 넣어야 사라진 키가 남지 않는다.
        cur.execute("DELETE FROM metrics_need WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM metrics_wish WHERE run_id = %s", (run_id,))
        cur.executemany(NEED_SQL, [_need_values(r, run_id) for r in need_rows])
        if not scope:
            cur.executemany(WISH_SQL, [_wish_values(r, run_id) for r in wish_rows])
        cur.execute(
            "UPDATE analysis_run SET status = 'done', finished_at = now() WHERE run_id = %s", (run_id,)
        )
    conn.commit()
    return run_id
