"""labeled_set: the four hand-labeled eval tasks under eval/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg

from db.seed._common import LABELED_AT, LABELER, counts, opt, read_csv, write

TABLES = ("labeled_set",)

SQL = """
INSERT INTO labeled_set (task, ref, split, gold, text, labeler, labeled_at, extra)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (task, ref) DO UPDATE
SET split = EXCLUDED.split, gold = EXCLUDED.gold, text = EXCLUDED.text,
    labeler = EXCLUDED.labeler, labeled_at = EXCLUDED.labeled_at, extra = EXCLUDED.extra
"""


def _row(task: str, ref: str, split: str, gold: str, text: str, extra: dict[str, Any]) -> tuple[Any, ...]:
    return (task, ref, split, gold, opt(text), LABELER, LABELED_AT, json.dumps(extra, ensure_ascii=False))


def _polarity(eval_dir: Path) -> list[tuple[Any, ...]]:
    out: list[tuple[Any, ...]] = []
    for name, split in (("suncare_tune200.csv", "tune"), ("suncare_holdout100.csv", "holdout")):
        for r in read_csv(eval_dir / "polarity" / name):
            # A review yields several labeled sentences; its ref alone is not unique, split and i are.
            ref = f"sun:{split}:{r['i']}:{r['ref']}"
            extra = {
                "rating": r["rating"],
                "rule_aspect": r["rule_aspect"],
                "rule_polarity": r["rule_polarity"],
            }
            out.append(_row("polarity", ref, split, r["gold"], r["sentence"], extra))
    for name, split in (("crosscat_60.csv", "tune"), ("crosscat_blind40.csv", "holdout")):
        for r in read_csv(eval_dir / "polarity" / name):
            extra = {"category": r["category"], "rating": r["rating"], "gold_aspect": r["gold_aspect"]}
            out.append(_row("polarity", f"p1:{split}:{r['i']}", split, r["gold"], r["sentence"], extra))
    return out


def _wish(eval_dir: Path) -> list[tuple[Any, ...]]:
    # blind60_v2 is the never-tuned-on holdout the wish_class baseline is judged against (interfaces.md).
    return [
        _row("wish_class", r["comment_id"], split, r["gold"], r["text"], {"like_count": r["like_count"]})
        for name, split in (
            ("tune100.csv", "tune"),
            ("holdout60.csv", "holdout"),
            ("blind60_v2.csv", "holdout"),
        )
        for r in read_csv(eval_dir / "wish" / name)
    ]


def _brand_link(eval_dir: Path) -> list[tuple[Any, ...]]:
    return [
        _row(
            "brand_link",
            f"{sample}:{r['src']}/{r['ref_id']}/{r['brand']}",
            "holdout",
            r["label"],
            r["context"],
            {"sample": sample},
        )
        for name, sample in (
            ("precision_sample60.csv", "uniform"),
            ("precision_sample60_weighted.csv", "weighted"),
        )
        for r in read_csv(eval_dir / "brand_link" / name)
    ]


def _product_match(eval_dir: Path) -> list[tuple[Any, ...]]:
    used = {"src_a", "name_a", "src_b", "name_b", "verdict", "i"}
    out: list[tuple[Any, ...]] = []
    for name, tag, split in (
        ("match_check40.csv", "v1", "tune"),
        ("match_check40_v2_blind.csv", "v2", "holdout"),
    ):
        for r in read_csv(eval_dir / "product_match" / name):
            text = f"{r['src_a']}:{r['name_a']} | {r['src_b']}:{r['name_b']}"
            extra = {k: v for k, v in r.items() if k not in used}
            out.append(_row("product_match", f"{tag}:{r['i']}", split, r["verdict"], text, extra))
    return out


def load(cur: psycopg.Cursor[Any], source_dir: Path) -> dict[str, int]:
    write(
        cur,
        SQL,
        _polarity(source_dir) + _wish(source_dir) + _brand_link(source_dir) + _product_match(source_dir),
    )
    return counts(cur, TABLES)
