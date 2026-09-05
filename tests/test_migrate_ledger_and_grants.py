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
WHITELIST = [
    "metrics_need",
    "metrics_wish",
    "entity_lexicon",
    "aspect_lexicon",
    "product_ref",
    "analysis_run",
]
# metrics_topic_quarter is a metrics_* table, yet it sits outside the whitelist (fork #3): it has no
# rows yet, and the call on whether to open it to the screen belongs to whatever serves it (#5). A GRANT
# added later is only ever additive.
NOT_WHITELISTED = [
    "need_mention", "labeled_set", "wish_mention", "brand_mention", "product_member",
    "panel_roster", "panel_channel", "metrics_topic_quarter",
    "mfds_snapshot", "mfds_registration",
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


# The view the screen reads. Being a view rather than a table puts it in a different spot -- migrate
# drops and recreates every view on each deploy (stage f), and that runs *after* the stage this file
# runs in (d). So a GRANT given here would be erased the moment it was made. A view's own file carries
# its grants (#158).
@pytest.mark.parametrize("view", ["pipeline_health"])
def test_postgrest_anon_can_select_the_view_the_ops_page_reads(conn: Connection, view: str):
    granted = conn.execute(
        text("select has_table_privilege('postgrest_anon', :v, 'SELECT')"), {"v": f"needs.{view}"}
    ).scalar()
    assert granted, f"needs.{view} 가 anon 에 안 열려 있다 -- 화면은 PostgREST 에 anon 으로 묻는다"


# Only the one view with the finished judgment is opened. Opening the raw logs too would be exposure
# with no need for it (#138).
@pytest.mark.parametrize("view", ["collector_health", "analysis_health"])
def test_postgrest_anon_cannot_select_the_upstream_views(conn: Connection, view: str):
    granted = conn.execute(
        text("select has_table_privilege('postgrest_anon', :v, 'SELECT')"), {"v": f"needs.{view}"}
    ).scalar()
    assert not granted


def test_postgrest_anon_has_usage_on_the_needs_schema(conn: Connection):
    allowed = conn.execute(
        text("select has_schema_privilege('postgrest_anon', 'needs', 'USAGE')")
    ).scalar_one()
    assert allowed is True
