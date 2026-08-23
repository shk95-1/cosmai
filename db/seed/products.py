"""Product identity and the per-product commerce facts: ref, member, denominator, rank, price."""

from __future__ import annotations

from pathlib import Path
from typing import Any, LiteralString

import psycopg

from db.seed._common import (
    CAPTURED_AT,
    as_date,
    as_timestamp,
    boolean,
    counts,
    dec,
    integer,
    opt,
    read_csv,
    write,
)

TABLES = (
    "product_ref",
    "product_member",
    "product_ref_candidate",
    "product_denominator",
    "rank_daily",
    "price_event",
)

SOURCE = "oliveyoung"
# 슬라이스 이름 = 그 행을 만든 실행. run 을 거치지 않는 테이블은 이 값만이 출처다 (A19).
P1_VERSION = "slice-p1"
P2_VERSION = "slice-p2"

REF_SQL: LiteralString = """
INSERT INTO product_ref (product_ref, brand, name_norm, name, n_sites, first_seen, linker_version)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (product_ref) DO UPDATE
SET brand = EXCLUDED.brand, name_norm = EXCLUDED.name_norm, name = EXCLUDED.name,
    n_sites = EXCLUDED.n_sites, linker_version = EXCLUDED.linker_version,
    first_seen = COALESCE(product_ref.first_seen, EXCLUDED.first_seen)
"""
MEMBER_SQL: LiteralString = """
INSERT INTO product_member (source, product_key, product_ref, role, match_score)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (source, product_key) DO UPDATE
SET product_ref = EXCLUDED.product_ref, role = EXCLUDED.role, match_score = EXCLUDED.match_score
"""
CANDIDATE_SQL: LiteralString = """
INSERT INTO product_ref_candidate
  (src_a, key_a, src_b, key_b, brand, shared_tok, shared_sig, dice, mutual, linker_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (src_a, key_a, src_b, key_b, linker_version) DO UPDATE
SET brand = EXCLUDED.brand, shared_tok = EXCLUDED.shared_tok, shared_sig = EXCLUDED.shared_sig,
    dice = EXCLUDED.dice, mutual = EXCLUDED.mutual
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


def normalize_ref(product_ref: str) -> str:
    """slice-p2 anchors olive young refs as `ol:`; the contract's example spells it `oy:`."""
    return "oy:" + product_ref[3:] if product_ref.startswith("ol:") else product_ref


def _role(product_ref: str, product_key: str) -> str:
    return "primary" if product_key == product_ref.split(":", 1)[1] else "member"


def _suncare(slices: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    refs: list[tuple[Any, ...]] = []
    members: list[tuple[Any, ...]] = []
    for r in read_csv(slices / "slice-suncare" / "product_ref.csv"):
        pairs = [m.split(":", 1) for m in r["members"].split(";") if m]
        refs.append(
            (
                r["product_ref"],
                opt(r["brand"]),
                r["name"],
                r["name"],
                len({source for source, _ in pairs}) or 1,
                as_date(r["first_seen"]) if r["first_seen"] else None,
                "slice-suncare",
            )
        )
        members += [
            (source, key, r["product_ref"], _role(r["product_ref"], key), None) for source, key in pairs
        ]
    return refs, members


def _p2(slices: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    d = slices / "slice-p2-ranking-dynamics"
    refs = [
        (
            normalize_ref(r["product_ref"]),
            opt(r["brand"]),
            r["name_norm"],
            r["name"],
            integer(r["n_sites"]) or 1,
            None,
            "slice-p2",
        )
        for r in read_csv(d / "product_ref.csv")
    ]
    members: list[tuple[Any, ...]] = []
    for r in read_csv(d / "product_ref_member.csv"):
        ref = normalize_ref(r["product_ref"])
        members.append((r["source"], r["product_key"], ref, _role(ref, r["product_key"]), None))
    return refs, members


def load(cur: psycopg.Cursor[Any], source_dir: Path) -> dict[str, int]:
    sun_refs, sun_members = _suncare(source_dir)
    p2_refs, p2_members = _p2(source_dir)
    # slice-p2 is the later, wider linker run: it wins where the two disagree.
    write(cur, REF_SQL, sun_refs + p2_refs)
    write(cur, MEMBER_SQL, sun_members + p2_members)
    write(
        cur,
        CANDIDATE_SQL,
        [
            (
                r["src_a"],
                r["key_a"],
                r["src_b"],
                r["key_b"],
                opt(r["brand"]),
                integer(r["shared_tok"]),
                integer(r["shared_sig"]),
                dec(r["dice"]),
                boolean(r["mutual"]),
                P2_VERSION,
            )
            for r in read_csv(source_dir / "slice-p2-ranking-dynamics" / "product_ref_candidates.csv")
        ],
    )
    write(
        cur,
        DENOMINATOR_SQL,
        [
            (
                SOURCE,
                r["product_key"],
                CAPTURED_AT,
                opt(r["category"]),
                integer(r["site_review_count"]),
                integer(r["low_collected"]),
                boolean(r["low_complete"]),
                dec(r["site_low_est"]),
                P1_VERSION,
            )
            for r in read_csv(source_dir / "slice-p1-category-gap" / "product_denominator.csv")
        ],
    )
    write(
        cur,
        RANK_SQL,
        [
            (
                r["source"],
                r["board"],
                r["category_key"],
                r["product_key"],
                as_date(r["day_kst"]),
                integer(r["n_snapshots"]),
                integer(r["n"]),
                dec(r["present_share"]),
                dec(r["rank_mean"]),
                integer(r["rank_min"]),
                integer(r["rank_max"]),
                integer(r["price_mode"]),
                P2_VERSION,
            )
            for r in read_csv(source_dir / "slice-p2-ranking-dynamics" / "rank_daily.csv")
        ],
    )
    write(
        cur,
        PRICE_SQL,
        [
            (
                SOURCE,
                r["product_key"],
                r["board"],
                as_timestamp(r["t_change"]),
                integer(r["price_before"]),
                integer(r["price_after"]),
                dec(r["pct"]),
                opt(r["direction"]),
                dec(r["rank_pre6"]),
                dec(r["rank_post6"]),
                dec(r["rank_post12"]),
                dec(r["rank_post24"]),
                integer(r["n_pre"]),
                integer(r["n_post24"]),
                P2_VERSION,
            )
            for r in read_csv(source_dir / "slice-p2-ranking-dynamics" / "price_rank_events.csv")
        ],
    )
    return counts(cur, TABLES)
