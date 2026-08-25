"""골격 — 키는 있고 잠그는 동작은 아직 없다 (#16)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg

__all__ = ["ANALYZE", "LOCK_CLASS", "advisory_key", "analyze_lock"]

LOCK_CLASS = 16
ANALYZE = "analyze"


def advisory_key(name: str) -> tuple[int, int]:
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=4).digest()
    return LOCK_CLASS, int.from_bytes(digest, "big", signed=True)


@contextmanager
def analyze_lock(conn: psycopg.Connection[Any], name: str = ANALYZE) -> Iterator[bool]:
    del conn, name
    yield True
