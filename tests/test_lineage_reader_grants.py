"""계보 두 뷰가 읽는 원천 표의 SELECT 를 배포가 실제로 열어 두는가 (#144).

`db/migrate.sh` 는 뷰를 `SET ROLE needs_owner` 로 만들고 뷰는 **소유자 권한으로** 돈다 — 그래서
원천을 읽어야 하는 롤은 `needs_runtime` 이 아니라 `needs_owner` 다. 그 GRANT 가 없으면 배포 단계
(f) 가 뷰 생성에서 죽고, 스위트가 초록이어도 운영에는 뷰가 없다(#158 이 데인 자리의 한 칸 위).

`db/grants/needs_runtime_reader.sql` 의 needs_owner 블록이 그 줄을 갖고, migrate.sh 는 (e) 로 그것을
(f) 보다 먼저 돌린다.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres

# 이름이 아니라 oid 로 묻는다 — 스키마 USAGE 가 없는 롤이 이름을 풀려고 하면 has_table_privilege
# 가 단언이 아니라 예외로 끝난다(test_collector_health_view.py 와 같은 이유).
_OID = (
    "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :s AND c.relname = :t"
)
_MAY_SELECT = "SELECT has_table_privilege(:role, cast(:oid AS oid), 'SELECT')"

# (스키마, 표, 어느 뷰의 어느 구간이 읽는가)
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
    engine.dispose()  # needs_migrator 는 CONNECTION LIMIT 2 다 — 통과든 실패든 놓아준다.


@pytest.mark.parametrize(("schema", "table", "why"), OWNER_NEEDS)
def test_needs_owner_may_read_every_source_table_the_lineage_views_touch(
    deployed: Any, schema: str, table: str, why: str
):
    oid = deployed.execute(text(_OID), {"s": schema, "t": table}).scalar()
    assert oid is not None, f"{schema}.{table} 이 하네스에 없다 — tool/checks/test 가 그 표를 세우지 않는다"
    granted = deployed.execute(text(_MAY_SELECT), {"role": "needs_owner", "oid": oid}).scalar_one()
    assert granted, f"{schema}.{table} ({why})"
