"""`analyze link`: reads the source schemas and rebuilds the product identification tables and
brand_mention.

Only needs is written to, and the two source schemas are opened with SELECT alone
(db/grants/needs_runtime_reader.sql).
"""

from __future__ import annotations

import collections
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql

from analysis.lexicon import load_lexicon
from analysis.linker import RuleLinker
from analysis.types import Lexicon, ProductRow, TextUnit

TABLES = ("product_ref", "product_member", "product_ref_candidate", "brand_mention")
# formats.md: only the YouTube comment timestamps are restored from relative time -- only 2025-09 onwards is
# trusted at month resolution.
COMMENT_MONTH_FROM = date(2025, 9, 1)
# The timeouts of needs_runtime are "sized per statement not per job" (db/bootstrap.sql) -- one run is cut
# into read and write pieces of this size so each piece finishes inside statement 30s and transaction 60s.
BATCH = 2000
# The starting key of the first page. An empty string is smaller than any key.
FIRST = ""

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

# All the paging is keyset -- OFFSET rereads the front on every page and makes the whole thing O(n^2).
PRODUCTS = pgsql.SQL("""
SELECT source, product_key, name, brand, volume, first_seen_at::date
FROM {schema}.product WHERE (source, product_key) > (%s, %s)
ORDER BY source, product_key LIMIT %s
""")
TITLES = pgsql.SQL("""
SELECT DISTINCT ON (video_id) video_id, video_id, title, published_at::date
FROM {schema}.video_snapshots
WHERE video_id > %s AND (%s::date IS NULL OR published_at::date >= %s::date)
ORDER BY video_id, fetched_at DESC LIMIT %s
""")
TRANSCRIPTS = pgsql.SQL("""
SELECT DISTINCT ON (t.video_id) t.video_id, t.video_id, t.full_text, v.published_at
FROM {schema}.transcripts t
LEFT JOIN LATERAL (
  SELECT max(s.published_at)::date AS published_at FROM {schema}.video_snapshots s
  WHERE s.video_id = t.video_id
) v ON true
WHERE t.video_id > %s AND (%s::date IS NULL OR v.published_at >= %s::date)
ORDER BY t.video_id, t.fetched_at DESC LIMIT %s
""")
COMMENTS = pgsql.SQL("""
SELECT comment_id, video_id, text, published_at::date FROM {schema}.comments
WHERE (video_id, comment_id) > (%s, %s)
  AND (%s::date IS NULL OR published_at::date >= %s::date)
ORDER BY video_id, comment_id LIMIT %s
""")


@dataclass(frozen=True)
class _Documents:
    """The source of one src of brand_mention. A row is (ref_id, video_id, text, observed_at)."""

    src: str
    sql: pgsql.SQL
    key_columns: tuple[int, ...]  # where the next start key is taken from in the last row of a page


DOCUMENTS = (
    _Documents("title", TITLES, (1,)),  # B12: a title is a link target too
    _Documents("transcript", TRANSCRIPTS, (1,)),
    _Documents("comment", COMMENTS, (1, 0)),  # the PK of comments is (video_id, comment_id)
)


def _resolution(src: str, observed_at: date | None) -> str:
    """The unit used for linking and the unit stored must not diverge -- that the time is unknown is said by
    observed_at NULL."""
    if src != "comment" or observed_at is None:
        return "day"
    return "month" if observed_at >= COMMENT_MONTH_FROM else "year"


def _pages(
    conn: psycopg.Connection[Any],
    query: pgsql.Composed,
    key_columns: tuple[int, ...],
    params: tuple[Any, ...],
    batch: int,
) -> Iterator[list[tuple[Any, ...]]]:
    key: tuple[Any, ...] = (FIRST,) * len(key_columns)
    while True:
        with conn.cursor() as cur:
            cur.execute(query, (*key, *params, batch))
            page = cur.fetchall()
        # Closed as soon as it is read: with the transaction open during linking, idle_in_transaction 15s
        # cuts the session.
        conn.rollback()
        if not page:
            return
        yield page
        if len(page) < batch:
            return
        key = tuple(page[-1][i] for i in key_columns)


def _write(
    conn: psycopg.Connection[Any], statement: LiteralString, rows: Sequence[Sequence[Any]], batch: int
) -> None:
    for start in range(0, len(rows), batch):
        with conn.cursor() as cur:
            cur.executemany(statement, rows[start : start + batch])
        conn.commit()


def _counts(conn: psycopg.Connection[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in TABLES:
            cur.execute(pgsql.SQL("SELECT count(*) FROM {}").format(pgsql.Identifier(table)))
            row = cur.fetchone()
            out[table] = int(row[0]) if row else 0
    conn.rollback()
    return out


def link_products(
    conn: psycopg.Connection[Any], linker: RuleLinker, commerce_schema: str, batch: int
) -> None:
    """It takes no --since: a catalogue cut to a window makes different clusters and the same ref wobbles."""
    query = PRODUCTS.format(schema=pgsql.Identifier(commerce_schema))
    products = [
        ProductRow(
            source=source,
            product_key=product_key,
            name=name,
            brand=brand,
            volume=volume,
            first_ranked=first_seen,
        )
        for page in _pages(conn, query, (0, 1), (), batch)
        for source, product_key, name, brand, volume, first_seen in page
    ]
    match = linker.match_products(products)
    # The order ref -> member is kept: product_member references product_ref (001).
    _write(
        conn,
        REF_SQL,
        [
            (r.product_ref, r.brand, r.name_norm, r.name, r.n_sites, r.first_seen, r.linker_version)
            for r in match.refs
        ],
        batch,
    )
    _write(
        conn,
        MEMBER_SQL,
        [(m.source, m.product_key, m.product_ref, m.role, m.match_score) for m in match.members],
        batch,
    )
    _write(
        conn,
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
        batch,
    )


def link_brands(
    conn: psycopg.Connection[Any],
    linker: RuleLinker,
    lexicon: Lexicon,
    youtube_schema: str,
    since: date | None,
    batch: int,
) -> None:
    schema = pgsql.Identifier(youtube_schema)
    for source in DOCUMENTS:
        query = source.sql.format(schema=schema)
        for page in _pages(conn, query, source.key_columns, (since, since), batch):
            rows: list[tuple[Any, ...]] = []
            for ref_id, video_id, text, observed_at in page:
                resolution = _resolution(source.src, observed_at)
                unit = TextUnit(
                    src=f"yt_{source.src}",
                    site="youtube",
                    ref=ref_id,
                    text=text,
                    observed_at=observed_at or date(1970, 1, 1),
                    observed_at_resolution=resolution,
                )
                hits = collections.Counter[str]()
                cooc = collections.Counter[str]()
                # brand_mention counts brands only -- surface_re bites ingredient surfaces too
                # (lexicon.py).
                for hit in linker.link(unit, lexicon):
                    if hit.kind != "brand":
                        continue
                    hits[hit.canonical] += 1
                    cooc[hit.canonical] += int(hit.cooc)
                rows += [
                    (
                        source.src,
                        ref_id,
                        video_id,
                        brand,
                        n,
                        cooc[brand],
                        observed_at,
                        resolution,
                        linker.version,
                    )
                    for brand, n in hits.items()
                ]
            _write(conn, BRAND_SQL, rows, batch)


def run(
    conn: psycopg.Connection[Any],
    since: date | None = None,
    commerce_schema: str = "trend_radar",
    youtube_schema: str = "tubedepth",
    linker: RuleLinker | None = None,
    batch: int = BATCH,
) -> dict[str, int]:
    """Committed per batch -- dying halfway is fine because every write is a natural-key upsert and the next
    run converges on the same result."""
    resolved = linker or RuleLinker()
    lexicon = load_lexicon(conn)
    link_products(conn, resolved, commerce_schema, batch)
    link_brands(conn, resolved, lexicon, youtube_schema, since, batch)
    return _counts(conn)
