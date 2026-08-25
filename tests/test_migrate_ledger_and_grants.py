"""Contract test #2: db/migrate.sh's ledger is idempotent and postgrest_anon sees only the whitelist."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, text

pytestmark = pytest.mark.postgres

MIGRATIONS = sorted(
    p.stem for p in (Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "needs").glob("*.sql")
)
WHITELIST = ["metrics_need", "metrics_wish", "entity_lexicon", "aspect_lexicon", "product_ref"]
# metrics_topic_quarter 는 metrics_* 인데도 화이트리스트 밖이다 (포크 #3): 아직 행이 없고, 이 표를
# 화면에 여는 판단은 그것을 서빙하는 쪽(#5)의 것이다. GRANT 는 나중에 더해도 추가만이다.
NOT_WHITELISTED = [
    "need_mention", "labeled_set", "wish_mention", "brand_mention", "product_member",
    "panel_channel", "metrics_topic_quarter",
]  # fmt: skip


@pytest.fixture
def conn() -> Iterator[Connection]:
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as c:
        # needs_migrator has no USAGE on schema needs outside SET ROLE (bootstrap.sql grants that
        # to needs_owner/needs_runtime only) -- the same access path migrate.sh itself uses.
        c.execute(text("SET ROLE needs_owner"))
        yield c
    engine.dispose()  # needs_migrator has CONNECTION LIMIT 2 -- always release, pass or fail.


@pytest.mark.parametrize("version", MIGRATIONS)
def test_the_ledger_has_exactly_one_row_per_migration(conn: Connection, version: str):
    n = conn.execute(
        text("select count(*) from needs.schema_migration where version = :v"), {"v": version}
    ).scalar_one()
    assert n == 1


@pytest.mark.parametrize("table", WHITELIST)
def test_postgrest_anon_can_select_the_whitelisted_tables(conn: Connection, table: str):
    allowed = conn.execute(
        text("select has_table_privilege('postgrest_anon', :t, 'SELECT')"), {"t": f"needs.{table}"}
    ).scalar_one()
    assert allowed is True


@pytest.mark.parametrize("table", NOT_WHITELISTED)
def test_postgrest_anon_cannot_select_the_remaining_tables(conn: Connection, table: str):
    allowed = conn.execute(
        text("select has_table_privilege('postgrest_anon', :t, 'SELECT')"), {"t": f"needs.{table}"}
    ).scalar_one()
    assert allowed is False


def test_postgrest_anon_has_usage_on_the_needs_schema(conn: Connection):
    allowed = conn.execute(
        text("select has_schema_privilege('postgrest_anon', 'needs', 'USAGE')")
    ).scalar_one()
    assert allowed is True
