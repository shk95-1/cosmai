"""Contract test #1: the needs DDL applied (by tool/checks/test) and has every table it declares."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres
DDL_DIR = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "needs"


def declared_tables() -> set[str]:
    # sorted(): same filename order db/migrate.sh's `for file in .../*.sql` glob applies them in.
    tables: set[str] = set()
    for path in sorted(DDL_DIR.glob("*.sql")):
        tables |= set(re.findall(r"CREATE TABLE needs\.(\w+)", path.read_text(encoding="utf-8")))
    return tables


def test_the_ddl_declares_the_seventeen_contract_tables():
    assert len(declared_tables()) == 17


def test_every_declared_table_exists_in_the_database():
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(text("select tablename from pg_tables where schemaname = 'needs'"))
        present = {r[0] for r in rows}
    engine.dispose()
    assert declared_tables() <= present, sorted(declared_tables() - present)
