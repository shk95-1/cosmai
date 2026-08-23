"""Versioned dictionaries: entity_lexicon, aspect_lexicon, site_axis_map, need_key, category_map (eval/)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, LiteralString

import psycopg

from db.lexicon import insert_aspects, insert_entities
from db.seed._common import LEXICON_VERSION, boolean, counts, opt, read_csv, write

TABLES = ("entity_lexicon", "aspect_lexicon", "site_axis_map", "need_key", "category_map")

# stoplist.csv tier -> entity_lexicon.tier (the DDL only knows normal | cooc_required | stop).
TIERS = {"stop": "stop", "retailer": "stop", "cooc": "cooc_required"}

NEED_KEY_SQL: LiteralString = """
INSERT INTO need_key (need_key, canonical, note)
VALUES (%s, %s, %s)
ON CONFLICT (need_key) DO UPDATE SET canonical = EXCLUDED.canonical, note = EXCLUDED.note
"""
CATEGORY_MAP_SQL: LiteralString = """
INSERT INTO category_map (site, source_category, lexicon_category, method, priority)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (site, source_category) DO UPDATE
SET lexicon_category = EXCLUDED.lexicon_category, method = EXCLUDED.method,
    priority = EXCLUDED.priority
"""
# site_axis_map carries no version, so DO NOTHING would leave it frozen at whatever loaded first.
AXIS_SQL: LiteralString = """
INSERT INTO site_axis_map (site, category, site_axis, need_key, note)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (site, category, site_axis) DO UPDATE
SET need_key = EXCLUDED.need_key, note = EXCLUDED.note
"""


def _brand_rows(eval_dir: Path) -> list[tuple[Any, ...]]:
    stop = {r["brand"]: r for r in read_csv(eval_dir / "brand_link" / "stoplist.csv")}
    dropped = {
        (r["brand"], r["alias"])
        for r in read_csv(eval_dir / "brand_link" / "alias_verification.csv")
        if r["decision"] == "drop"
    }
    surfaces: list[tuple[Any, ...]] = []
    aliases: list[tuple[Any, ...]] = []
    for r in read_csv(eval_dir / "lexicon" / "brand_lexicon_v1.csv"):
        listed = stop.get(r["canonical"])
        tier = TIERS[listed["tier"]] if listed else "normal"
        note = listed["reason"] if listed else None
        surfaces.append(("brand", r["canonical"], r["surface"], tier, opt(r["sources"]), note))
        for alias in filter(None, r["aliases"].split("|")):
            if (r["canonical"], alias) not in dropped:
                aliases.append(("brand", r["canonical"], alias, tier, opt(r["sources"]), note))
    # Surfaces first: where an alias repeats another brand's surface, the surface row is the one kept.
    return surfaces + aliases


def _ingredient_rows(eval_dir: Path) -> list[tuple[Any, ...]]:
    return [
        ("ingredient", r["lexicon_key"], surface, None, "paper_lexicon", opt(r["canonical_en"]))
        for r in read_csv(eval_dir / "lexicon" / "ingredient_kr_colloquial_v1.csv")
        for surface in filter(None, r["kr_colloquial"].split("|"))
    ]


def load(cur: psycopg.Cursor[Any], source_dir: Path) -> dict[str, int]:
    insert_entities(cur, _brand_rows(source_dir) + _ingredient_rows(source_dir), LEXICON_VERSION)
    insert_aspects(
        cur,
        [
            (
                r["aspect"],
                r["scope"],
                r["category"],
                r["pattern"],
                boolean(r["is_neutral_noun"]),
                r["ruleset"],
                int(r["priority"]),
            )
            for r in read_csv(source_dir / "lexicon" / "aspect_lexicon_v1.csv")
        ],
        LEXICON_VERSION,
    )
    write(
        cur,
        AXIS_SQL,
        [
            (r["site"], r["category"], r["site_axis"], opt(r["need_key"]), opt(r["note"]))
            for r in read_csv(source_dir / "lexicon" / "site_axis_map_v1.csv")
        ],
    )
    write(
        cur,
        NEED_KEY_SQL,
        [
            (r["need_key"], r["canonical"], opt(r["note"]))
            for r in read_csv(source_dir / "lexicon" / "need_key_v1.csv")
        ],
    )
    write(
        cur,
        CATEGORY_MAP_SQL,
        [
            (r["site"], r["source_category"], r["lexicon_category"], r["method"], int(r["priority"]))
            for r in read_csv(source_dir / "lexicon" / "category_map_v1.csv")
        ],
    )
    return counts(cur, TABLES)
