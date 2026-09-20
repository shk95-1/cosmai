"""`cosmai analyze launch`: reads the source tables and re-states the launch-evidence ledger (#283).

The claims themselves are decided in `analysis/launch/axes.py`, which knows no database; this file is
the reading, the upsert and the **withdrawal**. Only `needs.product_launch_evidence` is written, and
the two commerce tables are opened with SELECT alone (`db/grants/needs_runtime_reader.sql`).

Two properties this stage owes, both held by `tests/test_launch_writer.py`:

* **Idempotent.** Every claim is an upsert on the natural key `(product_ref, axis, source_ref)`, so a
  second run over unchanged sources restates the same rows and adds none.
* **A membership move withdraws the claim.** A `product_ref` is minted from its cluster's anchor and
  wobbles when the catalogue is re-clustered (`analysis/linker` `_ref_id`), leaving the old ref row
  standing with nobody pointing at it. The claim written under it is then live on a ref that no
  longer names the product, and the metric reads the new one -- so this stage DELETEs what it holds
  for a ref no `product_member` row points at any more (`contracts/interfaces.md` §Launch evidence).
  The DELETE is scoped by `axis` to the axes this stage writes: another axis's claims are not its to
  withdraw, and a wrong scope here would silently empty the ledger of somebody else's evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql

from analysis.launch.axes import (
    AXES,
    BrandIndex,
    CatalogueRef,
    Listing,
    Registration,
    board_claim,
    load_tags,
    mfds_claims,
    registrations_by_brand,
    review_claim,
    title_tag,
    title_tag_claim,
)
from analysis.types import LaunchClaimRow

TABLES = ("product_launch_evidence",)
# The same cut as the linker's: needs_runtime is sized per statement (statement 30s, transaction
# 60s), so one run is written in pieces of this size rather than in one transaction.
BATCH = 2000

REFS: LiteralString = "SELECT product_ref, brand, name_norm FROM product_ref"
MEMBERS: LiteralString = "SELECT source, product_key, product_ref, role FROM product_member"
BRAND_SURFACES: LiteralString = (
    "SELECT canonical, surface FROM entity_lexicon WHERE kind = 'brand' AND active"
)
REGISTRATIONS: LiteralString = "SELECT report_seq, item_name, entp_key, report_date FROM mfds_registration"

# `listed_at` is NULL on all 487 rows (#282 facts), so the only date this table carries is when we
# captured the board. Filling `listed_at` is collector work and is filed, not done here (#283 work 2).
BOARD = pgsql.SQL("""
SELECT source, product_key, min(captured_at)::date
FROM {table} GROUP BY source, product_key
""")
OLDEST_REVIEW = pgsql.SQL("""
SELECT source, product_key, min(written_at)::date
FROM {table} WHERE written_at IS NOT NULL GROUP BY source, product_key
""")
# The title as it stands now, beside the day we first saw the listing: the tag is decoration on the
# current title and `first_seen_at` is the earliest day we can attest the listing existed.
TITLES = pgsql.SQL("""
SELECT DISTINCT ON (source, product_key) source, product_key, name, first_seen_at::date
FROM {table} ORDER BY source, product_key, captured_at DESC
""")

UPSERT: LiteralString = """
INSERT INTO product_launch_evidence
  (product_ref, axis, direction, claimed_on, claimed_precision, source_ref, match_strength,
   axis_version, observed_at, note)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (product_ref, axis, source_ref) DO UPDATE
SET direction = EXCLUDED.direction, claimed_on = EXCLUDED.claimed_on,
    claimed_precision = EXCLUDED.claimed_precision, match_strength = EXCLUDED.match_strength,
    axis_version = EXCLUDED.axis_version, observed_at = EXCLUDED.observed_at, note = EXCLUDED.note
"""
HELD: LiteralString = "SELECT product_ref, axis, source_ref FROM product_launch_evidence WHERE axis = ANY(%s)"
WITHDRAW: LiteralString = (
    "DELETE FROM product_launch_evidence WHERE product_ref = %s AND axis = %s AND source_ref = %s"
)


def _from(schema: str, table: str) -> pgsql.Identifier:
    """Production reads the `trend_radar` schema; a test puts both schemas in one of its own
    (`tests/conftest.py`), and passes `commerce_schema=''` to say so."""
    return pgsql.Identifier(schema, table) if schema else pgsql.Identifier(table)


def claims(
    refs: Sequence[CatalogueRef],
    members: Sequence[Listing],
    surfaces: Sequence[tuple[str, str]],
    registrations: Sequence[Registration],
    boards: Sequence[tuple[str, str, date]],
    reviews: Sequence[tuple[str, str, date]],
    titles: Sequence[tuple[str, str, str, date]],
    observed_at: datetime,
    tags: Sequence[str] = (),
) -> list[LaunchClaimRow]:
    """Every claim of this run, over rows already read -- the whole decision, with no DB in it.

    A claim is written **only for a ref some `product_member` row still points at**. That is what
    makes the withdrawal below correct rather than approximate: a re-clustering re-points the member
    rows and leaves the old `product_ref` row standing, so "the refs a re-link vacated" is exactly
    "the refs with no member", and a stale ref keeps its brand and name and would otherwise go on
    matching registrations forever.
    """
    listings = {(listing.source, listing.product_key): listing for listing in members}
    live = {listing.product_ref for listing in listings.values()}
    index = BrandIndex.of(surfaces)
    keys_of = {ref.brand: index.keys_for(ref.brand) for ref in refs if ref.product_ref in live and ref.brand}
    by_brand = registrations_by_brand(registrations, keys_of)

    found: list[LaunchClaimRow] = []
    for ref in refs:
        if ref.product_ref not in live:
            continue
        keys = keys_of.get(ref.brand or "", frozenset())
        found += mfds_claims(ref, by_brand.get(ref.brand or "", []), keys, observed_at)
    for source, product_key, captured in boards:
        listing = listings.get((source, product_key))
        if listing is not None:
            found.append(board_claim(listing, captured, observed_at))
    for source, product_key, written in reviews:
        listing = listings.get((source, product_key))
        if listing is not None:
            found.append(review_claim(listing, written, observed_at))
    for source, product_key, name, first_seen in titles:
        listing = listings.get((source, product_key))
        tag = title_tag(name, tags) if listing is not None else None
        if listing is not None and tag is not None:
            found.append(title_tag_claim(listing, first_seen, tag, observed_at))
    return found


def _read(conn: psycopg.Connection[Any], commerce_schema: str) -> tuple[list[Any], ...]:
    """One read pass, each statement its own -- needs_runtime is cut off at 15s idle in transaction,
    so the connection is rolled back as soon as the rows are in hand (the linker's `_pages` rule)."""
    with conn.cursor() as cur:
        cur.execute(REFS)
        refs = [CatalogueRef(*row) for row in cur.fetchall()]
        cur.execute(MEMBERS)
        members = [Listing(*row) for row in cur.fetchall()]
        cur.execute(BRAND_SURFACES)
        surfaces = [(canonical, surface) for canonical, surface in cur.fetchall()]
        cur.execute(REGISTRATIONS)
        registrations = [Registration(str(seq), name, key, day) for seq, name, key, day in cur.fetchall()]
        cur.execute(BOARD.format(table=_from(commerce_schema, "new_product")))
        boards = cur.fetchall()
        cur.execute(OLDEST_REVIEW.format(table=_from(commerce_schema, "review")))
        reviews = cur.fetchall()
        cur.execute(TITLES.format(table=_from(commerce_schema, "product")))
        titles = cur.fetchall()
    conn.rollback()
    return refs, members, surfaces, registrations, boards, reviews, titles


def _write(conn: psycopg.Connection[Any], found: Sequence[LaunchClaimRow], batch: int) -> int:
    rows = [
        (
            claim.product_ref,
            claim.axis,
            claim.direction,
            claim.claimed_on,
            claim.claimed_precision,
            claim.source_ref,
            claim.match_strength,
            claim.axis_version,
            claim.observed_at,
            claim.note,
        )
        for claim in found
    ]
    for start in range(0, len(rows), batch):
        with conn.cursor() as cur:
            cur.executemany(UPSERT, rows[start : start + batch])
        conn.commit()
    return len(rows)


def _withdraw(conn: psycopg.Connection[Any], found: Sequence[LaunchClaimRow], batch: int) -> int:
    """The DELETE the contract puts on the axis that wrote the claim, scoped by `axis` to this
    stage's own four: what this run no longer claims goes, and an axis nobody here writes is never
    read back and never touched."""
    live = {(claim.product_ref, claim.axis, claim.source_ref) for claim in found}
    with conn.cursor() as cur:
        cur.execute(HELD, (list(AXES),))
        stale = [key for key in cur.fetchall() if tuple(key) not in live]
    conn.rollback()
    for start in range(0, len(stale), batch):
        with conn.cursor() as cur:
            cur.executemany(WITHDRAW, stale[start : start + batch])
        conn.commit()
    return len(stale)


def run(
    conn: psycopg.Connection[Any],
    commerce_schema: str = "trend_radar",
    observed_at: datetime | None = None,
    batch: int = BATCH,
) -> dict[str, int]:
    """Read, re-state, withdraw. Committed per batch, like every other stage: dying half-way is fine
    because each write is a natural-key upsert and the next run converges on the same ledger."""
    at = observed_at or datetime.now(UTC)
    refs, members, surfaces, registrations, boards, reviews, titles = _read(conn, commerce_schema)
    found = claims(refs, members, surfaces, registrations, boards, reviews, titles, at, load_tags())
    written = _write(conn, found, batch)
    withdrawn = _withdraw(conn, found, batch)
    return {"launch_claims": written, "launch_withdrawn": withdrawn}
