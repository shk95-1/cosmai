"""Whether a deploy actually leaves SELECT open on the source tables the two lineage views read (#144).

`db/migrate.sh` creates a view with `SET ROLE needs_owner` and the view runs **with owner
privilege** -- so the role that has to read the source is `needs_owner`, not `needs_runtime`. Without
that GRANT, deploy stage (f) dies creating the view, and even with a green suite production has no view
at all (one spot above where #158 got bitten).

The needs_owner block of `db/grants/needs_runtime_reader.sql` carries that line, and migrate.sh runs it
as stage (e), before (f).
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres

# Asked by oid, not name -- if a role with no schema USAGE tried to resolve the name, has_table_privilege
# would end in an exception instead of an assertion (the same reason as test_collector_health_view.py).
_OID = (
    "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :s AND c.relname = :t"
)
_MAY_SELECT = "SELECT has_table_privilege(:role, cast(:oid AS oid), 'SELECT')"

# (schema, table, which section of which view reads it)
OWNER_NEEDS = (
    ("trend_radar", "review", "mention_lineage 5a · collection_lineage 6a"),
    ("trend_radar", "run", "collection_lineage 6a"),
    ("trend_radar", "fetch_log", "collection_lineage 7"),
    ("tubedepth", "comments", "mention_lineage 5b · collection_lineage 6b"),
    ("tubedepth", "artifacts", "collection_lineage 6b"),
    ("tubedepth", "jobs", "collection_lineage 6b"),
)


@pytest.fixture
def deployed() -> Any:
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        yield conn
    engine.dispose()  # needs_migrator has CONNECTION LIMIT 2 -- always release, pass or fail.


@pytest.mark.parametrize(("schema", "table", "why"), OWNER_NEEDS)
def test_needs_owner_may_read_every_source_table_the_lineage_views_touch(
    deployed: Any, schema: str, table: str, why: str
):
    oid = deployed.execute(text(_OID), {"s": schema, "t": table}).scalar()
    assert oid is not None, f"{schema}.{table} 이 하네스에 없다 — tool/checks/test 가 그 표를 세우지 않는다"
    granted = deployed.execute(text(_MAY_SELECT), {"role": "needs_owner", "oid": oid}).scalar_one()
    assert granted, f"{schema}.{table} ({why})"
