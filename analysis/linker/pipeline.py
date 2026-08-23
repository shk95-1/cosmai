"""`analyze link`: 원천 스키마를 읽어 제품 식별 표와 brand_mention 을 다시 만든다.

쓰는 곳은 needs 뿐이고 원천 두 스키마는 SELECT 로만 연다 (db/grants/needs_runtime_reader.sql).
"""

from __future__ import annotations

import collections
from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql

from analysis.lexicon import load_lexicon
from analysis.linker import RuleLinker
from analysis.types import Lexicon, ProductRow, TextUnit

TABLES = ("product_ref", "product_member", "product_ref_candidate", "brand_mention")
# formats.md: 유튜브 댓글 시각만 상대시간 복원본이다 — 2025-09 이후만 달 단위로 믿는다.
COMMENT_MONTH_FROM = date(2025, 9, 1)
BATCH = 5000

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
BRAND_SQL: LiteralString = """
INSERT INTO brand_mention
  (src, ref_id, video_id, brand, count, cooc_count, observed_at, observed_at_resolution, linker_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (src, ref_id, brand, linker_version) DO UPDATE
SET video_id = EXCLUDED.video_id, count = EXCLUDED.count, cooc_count = EXCLUDED.cooc_count,
    observed_at = EXCLUDED.observed_at, observed_at_resolution = EXCLUDED.observed_at_resolution
"""

PRODUCTS = pgsql.SQL("""
SELECT DISTINCT ON (source, product_key) source, product_key, name, brand, volume, first_seen_at::date
FROM {}.product ORDER BY source, product_key, captured_at DESC
""")
TITLES = pgsql.SQL("""
SELECT DISTINCT ON (video_id) video_id, video_id, title, published_at::date
FROM {}.video_snapshots WHERE (%s::date IS NULL OR published_at::date >= %s::date)
ORDER BY video_id, fetched_at DESC
""")
TRANSCRIPTS = pgsql.SQL("""
SELECT DISTINCT ON (t.video_id) t.video_id, t.video_id, t.full_text, v.published_at
FROM {schema}.transcripts t
LEFT JOIN LATERAL (
  SELECT max(s.published_at)::date AS published_at FROM {schema}.video_snapshots s
  WHERE s.video_id = t.video_id
) v ON true
WHERE (%s::date IS NULL OR v.published_at >= %s::date)
ORDER BY t.video_id, t.fetched_at DESC
""")
COMMENTS = pgsql.SQL("""
SELECT comment_id, video_id, text, published_at::date FROM {}.comments
WHERE (%s::date IS NULL OR published_at::date >= %s::date)
""")


def _resolution(src: str, observed_at: date | None) -> str | None:
    if observed_at is None:
        return None
    if src != "comment":
        return "day"
    return "month" if observed_at >= COMMENT_MONTH_FROM else "year"


def _write(cur: psycopg.Cursor[Any], statement: LiteralString, rows: Sequence[Sequence[Any]]) -> None:
    for start in range(0, len(rows), BATCH):
        cur.executemany(statement, rows[start : start + BATCH])


def _counts(cur: psycopg.Cursor[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in TABLES:
        cur.execute(pgsql.SQL("SELECT count(*) FROM {}").format(pgsql.Identifier(table)))
        row = cur.fetchone()
        out[table] = int(row[0]) if row else 0
    return out


def _documents(
    cur: psycopg.Cursor[Any], schema: str, since: date | None
) -> Iterator[tuple[str, str, str, str, date | None]]:
    """(src, ref_id, video_id, text, observed_at) — B12: 제목도 링크 대상이다."""
    for src, query in (
        ("title", TITLES.format(pgsql.Identifier(schema))),
        ("transcript", TRANSCRIPTS.format(schema=pgsql.Identifier(schema))),
        ("comment", COMMENTS.format(pgsql.Identifier(schema))),
    ):
        cur.execute(query, (since, since))
        for ref_id, video_id, text, observed_at in cur.fetchall():
            yield src, ref_id, video_id, text, observed_at


def link_products(conn: psycopg.Connection[Any], linker: RuleLinker, commerce_schema: str) -> None:
    """--since 를 받지 않는다: 창을 자른 카탈로그는 다른 군집을 만들어 같은 ref 가 흔들린다."""
    with conn.cursor() as cur:
        cur.execute(PRODUCTS.format(pgsql.Identifier(commerce_schema)))
        products = [
            ProductRow(
                source=source,
                product_key=product_key,
                name=name,
                brand=brand,
                volume=volume,
                first_ranked=first_seen,
            )
            for source, product_key, name, brand, volume, first_seen in cur.fetchall()
        ]
    match = linker.match_products(products)
    with conn.cursor() as cur:
        _write(
            cur,
            REF_SQL,
            [
                (r.product_ref, r.brand, r.name_norm, r.name, r.n_sites, r.first_seen, r.linker_version)
                for r in match.refs
            ],
        )
        _write(
            cur,
            MEMBER_SQL,
            [(m.source, m.product_key, m.product_ref, m.role, m.match_score) for m in match.members],
        )
        _write(
            cur,
            CANDIDATE_SQL,
            [
                (
                    c.src_a,
                    c.key_a,
                    c.src_b,
                    c.key_b,
                    c.brand,
                    c.shared_tok,
                    c.shared_sig,
                    c.dice,
                    c.mutual,
                    linker.version,
                )
                for c in match.candidates
            ],
        )
    conn.commit()


def link_brands(
    conn: psycopg.Connection[Any],
    linker: RuleLinker,
    lexicon: Lexicon,
    youtube_schema: str,
    since: date | None,
) -> None:
    rows: list[tuple[Any, ...]] = []
    with conn.cursor() as cur:
        for src, ref_id, video_id, text, observed_at in _documents(cur, youtube_schema, since):
            unit = TextUnit(
                src=f"yt_{src}",
                site="youtube",
                ref=ref_id,
                text=text,
                observed_at=observed_at or date(1970, 1, 1),
                observed_at_resolution=_resolution(src, observed_at) or "day",
            )
            hits = collections.Counter[str]()
            cooc = collections.Counter[str]()
            # brand_mention 은 브랜드만 센다 — surface_re 는 ingredient 표면까지 문다 (analysis/lexicon.py).
            for hit in linker.link(unit, lexicon):
                if hit.kind != "brand":
                    continue
                hits[hit.canonical] += 1
                cooc[hit.canonical] += int(hit.cooc)
            rows += [
                (
                    src,
                    ref_id,
                    video_id,
                    brand,
                    n,
                    cooc[brand],
                    observed_at,
                    _resolution(src, observed_at),
                    linker.version,
                )
                for brand, n in hits.items()
            ]
    with conn.cursor() as cur:
        _write(cur, BRAND_SQL, rows)
    conn.commit()


def run(
    conn: psycopg.Connection[Any],
    since: date | None = None,
    commerce_schema: str = "trend_radar",
    youtube_schema: str = "tubedepth",
    linker: RuleLinker | None = None,
) -> dict[str, int]:
    resolved = linker or RuleLinker()
    lexicon = load_lexicon(conn)
    link_products(conn, resolved, commerce_schema)
    link_brands(conn, resolved, lexicon, youtube_schema, since)
    with conn.cursor() as cur:
        return _counts(cur)
