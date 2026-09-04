"""need_mention / wish_mention / brand_mention -- the normalized extraction output of the slices."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, LiteralString

import psycopg

from db.seed._common import (
    as_date,
    comment_resolution,
    counts,
    dec,
    integer,
    month_of,
    opt,
    read_csv,
    write,
)

TABLES = ("need_mention", "wish_mention", "brand_mention")

FIVE = Decimal(5)

# With 005, extractor_version joined the natural key, so a row slice-p1 pulls again for a review
# slice-suncare already pulled no longer gets absorbed and both stand on their own -- what DO NOTHING
# blocks now is only a re-load of the same slice.
NEED_SQL: LiteralString = """
INSERT INTO need_mention
  (src, site, ref, product_ref, source_product_key, category, lexicon_category, need_key,
   aspect_scope, polarity, strength, rating, observed_at, observed_at_resolution, month, sentence,
   extractor_version, polarity_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (src, ref, need_key, extractor_version, md5(sentence)) DO NOTHING
"""
WISH_SQL: LiteralString = """
INSERT INTO wish_mention
  (src, ref, video_id, product_ref, observed_at, observed_at_resolution, month, wish_class,
   brand, format, attribute, sentence, like_count, extractor_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (src, ref) DO UPDATE
SET video_id = EXCLUDED.video_id, observed_at = EXCLUDED.observed_at,
    observed_at_resolution = EXCLUDED.observed_at_resolution, month = EXCLUDED.month,
    wish_class = EXCLUDED.wish_class, brand = EXCLUDED.brand, format = EXCLUDED.format,
    attribute = EXCLUDED.attribute, sentence = EXCLUDED.sentence, like_count = EXCLUDED.like_count
"""
BRAND_SQL: LiteralString = """
INSERT INTO brand_mention
  (src, ref_id, video_id, brand, count, cooc_count, observed_at, observed_at_resolution, linker_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (src, ref_id, brand, linker_version) DO UPDATE
SET video_id = EXCLUDED.video_id, count = EXCLUDED.count, cooc_count = EXCLUDED.cooc_count,
    observed_at = EXCLUDED.observed_at, observed_at_resolution = EXCLUDED.observed_at_resolution
"""


def _suncare_need(slices: Path) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = []
    for r in read_csv(slices / "slice-suncare" / "need_mention.csv"):
        review = r["src"] == "review"
        observed_at = as_date(r["observed_at"])
        strength = dec(r["strength"])
        out.append(
            (
                r["src"],
                r["site"],
                r["text_ref"],
                opt(r["product_ref"]),
                r["text_ref"].split("/", 1)[0] if review else None,
                "선블록",
                # B10: this slice was judged using only the 선블록 dictionary, so the site's original
                # category and the dictionary category are the same.
                "선블록",
                r["need_key"],
                None,
                r["polarity"],
                strength,
                (1 - strength) * FIVE if review and strength is not None else None,
                observed_at,
                "day" if review else comment_resolution(observed_at),
                r["month"] or month_of(observed_at),
                r["sentence"],
                "slice-suncare",
                "rule-v2.1",
            )
        )
    return out


def _p1_need(slices: Path) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = []
    for r in read_csv(slices / "slice-p1-category-gap" / "need_mention.csv"):
        rating = dec(r["rating"])
        observed_at = as_date(f"{r['month']}-01")
        out.append(
            (
                "review",
                r["site"],
                f"{r['product_key']}/{r['review_key']}",
                None,
                r["product_key"],
                opt(r["category"]),
                opt(r["lexicon_category"]),
                r["need_key"],
                # aspect_lexicon.scope is generic|category; slice-p1 spells the second one "specific".
                "category" if r["aspect_scope"] == "specific" else opt(r["aspect_scope"]),
                r["polarity"],
                1 - rating / FIVE if rating is not None else None,
                rating,
                observed_at,
                "month",
                r["month"],
                r["sentence"],
                "slice-p1",
                "rule-v2.2",
            )
        )
    return out


def load(cur: psycopg.Cursor[Any], source_dir: Path) -> dict[str, int]:
    write(cur, NEED_SQL, _suncare_need(source_dir) + _p1_need(source_dir))
    write(
        cur,
        WISH_SQL,
        [
            (
                "yt_comment",
                # A20: written as video_id/comment_id so the same comment carries the same key as
                # need_mention.
                f"{r['video_id']}/{r['comment_id']}",
                opt(r["video_id"]),
                None,
                as_date(r["published_at"]),
                comment_resolution(as_date(r["published_at"])),
                month_of(as_date(r["published_at"])),
                r["class"],
                opt(r["brand"]),
                opt(r["format"]),
                opt(r["attribute"]),
                r["text"],
                integer(r["like_count"]),
                "slice-p9",
            )
            for r in read_csv(source_dir / "slice-p9-wish-mining" / "wish_mention.csv")
        ],
    )
    brand: list[tuple[Any, ...]] = []
    for r in read_csv(source_dir / "slice-p3-youtube-brand-link" / "brand_mentions.csv"):
        observed_at = as_date(r["published_at"])
        brand.append(
            (
                r["src"],
                r["ref_id"],
                opt(r["video_id"]),
                r["brand"],
                integer(r["count"]),
                integer(r["cooc_count"]),
                observed_at,
                # Only comment timestamps are the restored-from-relative kind; a video's is exact.
                comment_resolution(observed_at) if r["src"] == "comment" else "day",
                "slice-p3",
            )
        )
    write(cur, BRAND_SQL, brand)
    return counts(cur, TABLES)
