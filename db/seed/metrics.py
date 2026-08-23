"""analysis_run and the two aggregate tables the read side exposes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, LiteralString

import psycopg

from db.seed._common import LEXICON_VERSION, RUN_NOTE, counts, dec, integer, read_csv, write

TABLES = ("analysis_run", "metrics_need", "metrics_wish")

VERSIONS = {
    "linker": "slice",
    "extractor": "slice",
    "polarity": "rule-v2.1|rule-v2.2",
    "aggregate": "slice",
    "lexicon": LEXICON_VERSION,
}
SUNCARE_SCOPE = "선블록"
# wish_aggregates mixes a cross-tab with its own margins; the PK cannot hold both under one scope.
WISH_CROSS_SCOPE = "wish:a:format×attr"
FORMAT_ATTR_SEP = " × "

NEED_SQL: LiteralString = """
INSERT INTO metrics_need
  (run_id, scope, need_key, month, product_ref, neg, pos, unresolved, low_share,
   population_share_pct, strength_low_rating_ratio, persist_months, persist_products)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id, scope, need_key, month, product_ref) DO UPDATE
SET neg = EXCLUDED.neg, pos = EXCLUDED.pos, unresolved = EXCLUDED.unresolved,
    low_share = EXCLUDED.low_share, population_share_pct = EXCLUDED.population_share_pct,
    strength_low_rating_ratio = EXCLUDED.strength_low_rating_ratio,
    persist_months = EXCLUDED.persist_months, persist_products = EXCLUDED.persist_products
"""
WISH_SQL: LiteralString = """
INSERT INTO metrics_wish
  (run_id, scope, format, attribute, brand, mentions, channels, months_present, like_sum)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id, scope, format, attribute, brand) DO UPDATE
SET mentions = EXCLUDED.mentions, channels = EXCLUDED.channels,
    months_present = EXCLUDED.months_present, like_sum = EXCLUDED.like_sum
"""


def analysis_run(cur: psycopg.Cursor[Any]) -> int:
    """Found by note, created only when absent -- re-seeding must not pile up runs."""
    cur.execute("SELECT run_id FROM analysis_run WHERE note = %s ORDER BY run_id LIMIT 1", (RUN_NOTE,))
    found = cur.fetchone()
    if found:
        return int(found[0])
    cur.execute(
        "INSERT INTO analysis_run (status, versions, note) VALUES (%s, %s::jsonb, %s) RETURNING run_id",
        ("seeded", json.dumps(VERSIONS, ensure_ascii=False), RUN_NOTE),
    )
    created = cur.fetchone()
    assert created is not None
    return int(created[0])


def _ratio(value: str) -> int | None:
    """The slices print persistence as '19/39'; the contract column is the numerator."""
    return integer(value.split("/", 1)[0]) if value else None


def _need_rows(slices: Path, run_id: int) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for r in read_csv(slices / "slice-suncare" / "metrics.csv"):
        rows.append(
            (
                run_id,
                SUNCARE_SCOPE,
                r["need_key"],
                "",
                "",
                integer(r["neg"]),
                integer(r["pos"]),
                dec(r["unresolved_ratio"]),
                None,
                None,
                dec(r["strength_low_rating_ratio"]),
                _ratio(r["persist_months"]),
                _ratio(r["persist_products"]),
            )
        )
    for r in read_csv(slices / "slice-suncare" / "metrics_population.csv"):
        rows.append(
            (
                run_id,
                SUNCARE_SCOPE,
                r["need_key"],
                "",
                r["product_ref"],
                integer(r["complaint_reviews_in_low"]),
                0,
                None,
                dec(r["ratio_in_low"]),
                dec(r["pop_share_pct"]),
                None,
                None,
                None,
            )
        )
    for r in read_csv(slices / "slice-p1-category-gap" / "metrics_by_category.csv"):
        rows.append(
            (
                run_id,
                r["category"],
                r["need_key"],
                "",
                "",
                integer(r["neg"]),
                integer(r["pos"]),
                dec(r["unresolved"]),
                dec(r["low_share"]),
                dec(r["population_share_pct"]),
                None,
                _ratio(r["months_neg"]),
                _ratio(r["products_neg"]),
            )
        )
    return rows


def _wish_rows(slices: Path, run_id: int) -> list[tuple[Any, ...]]:
    aggregates = read_csv(slices / "slice-p9-wish-mining" / "wish_aggregates.csv")
    formats = {r["key"] for r in aggregates if r["kind"] == "format"}
    rows: list[tuple[Any, ...]] = []
    for r in aggregates:
        kind, key = r["kind"], r["key"]
        scope = "wish:b" if kind.startswith("b:") else "wish:a"
        dimension = kind.removeprefix("b:")
        fmt = attribute = brand = ""
        if dimension == "format×attr":
            scope = WISH_CROSS_SCOPE
            # aggregate.py joins the two with ' × ', and drops the separator when only one side matched.
            if FORMAT_ATTR_SEP in key:
                fmt, attribute = key.split(FORMAT_ATTR_SEP, 1)
            elif key in formats:
                fmt = key
            else:
                attribute = key
        elif dimension == "format":
            fmt = key
        elif dimension == "attribute":
            attribute = key
        else:
            brand = key
        rows.append(
            (
                run_id,
                scope,
                fmt,
                attribute,
                brand,
                integer(r["mentions"]),
                integer(r["channels"]),
                integer(r["months_present"]),
                integer(r["likes"]),
            )
        )
    return rows


def load(cur: psycopg.Cursor[Any], source_dir: Path) -> dict[str, int]:
    run_id = analysis_run(cur)
    write(cur, NEED_SQL, _need_rows(source_dir, run_id))
    write(cur, WISH_SQL, _wish_rows(source_dir, run_id))
    return counts(cur, TABLES)
