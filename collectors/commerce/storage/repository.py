"""Records into rows, idempotently.

origin: service/trend-radar/src/trend_radar/storage/repository.py -- ported for #7, de-async'd to match
this repo's sync SQLAlchemy + psycopg stack (no live collection in this issue, so nothing here needs an
event loop yet).

`captured_at` being the run's hour bucket is only half of why re-running an hour is harmless; every
write here is an upsert on the natural key, so a run that died two thirds through is recovered by
running it again.

  rank_snapshot, price_point  DO NOTHING. The reading taken on time is the real one.
  product                     MERGE (COALESCE), except first_seen_at: a ranking response carries no
                               volume/url/ingredients, and a plain overwrite would blank whatever a
                               detail pass wrote.
  review, new_product         DO NOTHING. Both describe something that happened once.
  review_stats                MERGE: one product-hour is assembled from two endpoints, so DO NOTHING
                               gave the row to whichever arrived first and dropped the other half.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import islice
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert

from collectors.commerce.models import ProductRecord, Record, ReviewStatsRecord
from collectors.commerce.storage.tables import TABLE_FOR

# psycopg binds every value in a statement and Postgres caps a statement at 65535 parameters, so
# chunking a 16-column table around 4000 rows is a correctness requirement, not a tuning knob.
CHUNK_ROWS = 500

_MERGE_TYPES: frozenset[type] = frozenset({ReviewStatsRecord})


class Repository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def write(self, records: Sequence[Record]) -> None:
        for record_type, rows in _group(records).items():
            table = TABLE_FOR[record_type]
            for chunk in _chunks(rows, CHUNK_ROWS):
                self._connection.execute(_statement(record_type, table, chunk), chunk)


def _statement(record_type: type[Record], table: sa.Table, rows: Sequence[dict[str, Any]]):
    statement = insert(table)
    key = [c.name for c in table.primary_key.columns]

    if record_type in _MERGE_TYPES:
        return statement.on_conflict_do_update(
            index_elements=key,
            set_={
                name: sa.func.coalesce(statement.excluded[name], table.c[name])
                for name in rows[0]
                if name not in key
            },
        )

    if record_type is ProductRecord:
        return statement.on_conflict_do_update(
            index_elements=key,
            set_={
                name: sa.func.coalesce(statement.excluded[name], table.c[name])
                for name in rows[0]
                # first_seen_at is deliberately absent: refreshing it would erase the only record of
                # when this product turned up.
                if name not in key and name != "first_seen_at"
            },
        )
    return statement.on_conflict_do_nothing(index_elements=key)


def _group(records: Iterable[Record]) -> dict[type[Record], list[dict[str, Any]]]:
    """Fold a batch to one row per natural key, per record type -- Postgres refuses to let one
    statement affect a row twice, so a duplicate left in the batch takes the whole write down."""
    grouped: dict[type[Record], list[dict[str, Any]]] = {}
    index: dict[type[Record], dict[tuple[object, ...], dict[str, Any]]] = {}

    for record in records:
        record_type = type(record)
        key = record.natural_key()
        rows = index.setdefault(record_type, {})
        row = _row(record)

        existing = rows.get(key)
        if existing is None:
            rows[key] = row
            grouped.setdefault(record_type, []).append(row)
        elif record_type in _MERGE_TYPES:
            for name, value in row.items():
                if value is not None:
                    existing[name] = value
    return grouped


def _row(record: Record) -> dict[str, Any]:
    row = record.model_dump()
    if isinstance(record, ProductRecord):
        row["first_seen_at"] = record.captured_at
        row["last_seen_at"] = record.captured_at
    columns = {c.name for c in TABLE_FOR[type(record)].columns}
    return {name: value for name, value in row.items() if name in columns}


def _chunks(rows: Sequence[dict[str, Any]], size: int) -> Iterable[Sequence[dict[str, Any]]]:
    iterator = iter(rows)
    while chunk := list(islice(iterator, size)):
        yield chunk
