"""Shared plumbing for the seed loaders: connection, CSV reading, executemany, counts."""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, LiteralString

import psycopg
from psycopg import sql as pgsql
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "eval"
DEFAULT_SLICES = REPO_ROOT.parent / "architect"

LEXICON_VERSION = 1
LABELER = "shk"
LABELED_AT = date(2026, 8, 23)
CAPTURED_AT = date(2026, 8, 23)
# formats.md: YouTube published_at is restored from relative time, so only recent ones are month-precise.
YOUTUBE_MONTH_FROM = date(2025, 9, 1)


def connect(url: str) -> psycopg.Connection[Any]:
    u = make_url(url)
    kwargs: dict[str, Any] = {
        k: v
        for k, v in (
            ("host", u.host),
            ("port", u.port),
            ("user", u.username),
            ("password", u.password),
            ("dbname", u.database),
        )
        if v is not None
    }
    kwargs.update({k: v for k, v in u.query.items() if isinstance(v, str)})
    return psycopg.connect(**kwargs)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [{k: (v or "") for k, v in row.items()} for row in csv.DictReader(f)]


def write(cur: psycopg.Cursor[Any], statement: LiteralString, rows: Sequence[Sequence[Any]]) -> None:
    if rows:
        cur.executemany(statement, rows)


def count(cur: psycopg.Cursor[Any], table: str) -> int:
    cur.execute(pgsql.SQL("SELECT count(*) FROM {}").format(pgsql.Identifier(table)))
    row = cur.fetchone()
    return int(row[0]) if row else 0


def counts(cur: psycopg.Cursor[Any], tables: Iterable[str]) -> dict[str, int]:
    return {t: count(cur, t) for t in tables}


def opt(value: str) -> str | None:
    return value or None


def dec(value: str) -> Decimal | None:
    return Decimal(value) if value else None


def integer(value: str) -> int | None:
    return int(Decimal(value)) if value else None


def boolean(value: str) -> bool | None:
    return value.lower() in {"true", "1", "t", "yes"} if value else None


def as_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def as_timestamp(value: str) -> datetime:
    """Naive input is UTC: the slices write datetime.utcfromtimestamp() and t_change is part of a PK,
    so leaving the offset to the session TimeZone would shift the instant and duplicate the row."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def month_of(day: date) -> str:
    return day.strftime("%Y-%m")


def comment_resolution(day: date) -> str:
    return "month" if day >= YOUTUBE_MONTH_FROM else "year"
