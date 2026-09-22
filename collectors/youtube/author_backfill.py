"""One-off conversion of pre-rule comment authors (#288).

The live collector already hashes new rows.  This module exists for the finite pre-rule population
and deliberately exposes only counts: an identifier is personal data even when an operator is
debugging a failed pass.  Each batch is its own transaction so a lock timeout or an interrupted
process leaves committed batches intact and a rerun resumes from the remaining raw rows.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

RAW_PATTERN = r"^UC[0-9A-Za-z_-]{22}$"
HASH_PATTERN = r"^[0-9a-f]{24}$"


class UnknownIdentifierShape(ValueError):
    """At least one non-null identifier is neither the old raw form nor the canonical hash."""


@dataclass(frozen=True, slots=True)
class Report:
    total: int
    raw_ids: int
    display_names: int
    invalid_ids: int
    candidates: int


_INSPECT = sa.text(
    """
    SELECT count(*)::bigint AS total,
           count(*) FILTER (WHERE author_id ~ :raw)::bigint AS raw_ids,
           count(*) FILTER (WHERE author IS NOT NULL)::bigint AS display_names,
           count(*) FILTER (
               WHERE author_id IS NOT NULL
                 AND author_id !~ :raw
                 AND author_id !~ :hashed
           )::bigint AS invalid_ids,
           count(*) FILTER (WHERE author_id ~ :raw OR author IS NOT NULL)::bigint AS candidates
      FROM comments
    """
)

_APPLY_BATCH = sa.text(
    """
    WITH batch AS (
        SELECT video_id, comment_id
          FROM comments
         WHERE author_id ~ :raw OR author IS NOT NULL
         ORDER BY video_id, comment_id
         LIMIT :batch_size
         FOR UPDATE SKIP LOCKED
    )
    UPDATE comments AS c
       SET author_id = CASE
             WHEN c.author_id ~ :raw
             THEN substr(encode(sha256(('youtube:' || c.author_id)::bytea), 'hex'), 1, 24)
             ELSE c.author_id
           END,
           author = NULL
      FROM batch AS b
     WHERE c.video_id = b.video_id
       AND c.comment_id = b.comment_id
    RETURNING 1
    """
)


def inspect(conn: Connection) -> Report:
    """Return the five counts an operator may see; never return a value from either author column."""
    row = conn.execute(_INSPECT, {"raw": RAW_PATTERN, "hashed": HASH_PATTERN}).one()
    return Report(*(int(value) for value in row))


def apply(engine: Engine, *, batch_size: int = 1000) -> int:
    """Hash raw ids and clear names, committing one bounded primary-key batch at a time."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    with engine.connect() as conn:
        invalid = inspect(conn).invalid_ids
    if invalid:
        suffix = "row" if invalid == 1 else "rows"
        raise UnknownIdentifierShape(
            f"{invalid} {suffix} have an identifier shape that is neither raw nor hashed; refusing"
        )

    changed = 0
    while True:
        with engine.begin() as conn:
            # Shorter than the role's 30-second statement timeout: another collector transaction
            # wins quickly, and this resumable pass comes back for the row in its next batch.
            conn.exec_driver_sql("SET LOCAL lock_timeout = '2s'")
            rows = conn.execute(
                _APPLY_BATCH,
                {"raw": RAW_PATTERN, "batch_size": batch_size},
            ).all()
        changed += len(rows)
        if not rows:
            return changed


__all__ = ["Report", "UnknownIdentifierShape", "apply", "inspect"]
