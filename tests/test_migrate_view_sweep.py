"""A deploy never drops someone else's view (#150).

Fixing #138, where re-applying died because of a dependency between views, meant dropping **every**
view in the schema. Two views the fork made (`metrics_topic_quarter_violation`,
`topic_quarter_judgement_violation`, fork DDL 022/024) live on production `needs`, and upstream's
`db/views/` knows nothing about them -- the drop-then-recreate loop only runs over `db/views/*.sql`, so
those two would simply vanish.

This measures that outcome: it plants a view with no file in `db/views/`, runs the deploy twice, and
asks whether it survived. The reason this does not grep the source is that the problem is the sweep's
*scope*, not its *shape*.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = "zz_foreign_view_probe"


def test_a_view_this_checkout_does_not_own_survives_the_deploy(
    deploy: Callable[..., subprocess.CompletedProcess[str]],
):
    url = os.environ["TEST_POSTGRES_URL"]
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(f"CREATE OR REPLACE VIEW needs.{PROBE} AS SELECT 1 AS one")
    # Disposed before the deploy, not after the assertions: needs_migrator has CONNECTION LIMIT 2
    # cluster-wide and db/migrate.sh needs one of them (#178 review 4). The shared `deploy` fixture
    # waits for both slots, so an engine left open here would fail this test rather than the next
    # twenty -- but it would still fail it.
    engine.dispose()

    try:
        # Runs twice: once to watch the sweep run, and a second time to check that re-applying is still
        # idempotent.
        for _ in range(2):
            done = deploy()
            assert done.returncode == 0, done.stderr

        engine = create_engine(url)
        with engine.connect() as conn:
            present = conn.execute(
                text("SELECT count(*) FROM pg_views WHERE schemaname = 'needs' AND viewname = :v"),
                {"v": PROBE},
            ).scalar()
        engine.dispose()
        assert present == 1, "배포가 이 체크아웃이 소유하지 않은 뷰를 지웠다"
    finally:
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.exec_driver_sql("SET ROLE needs_owner")
            conn.exec_driver_sql(f"DROP VIEW IF EXISTS needs.{PROBE}")
        engine.dispose()


def test_the_views_this_checkout_owns_are_all_present_after_the_deploy(harness_container: str):
    # Whether a fix that narrows the scope did not also drop the views this checkout owns -- this holds
    # the opposite side at the same time.
    owned = {p.stem for p in (REPO_ROOT / "db" / "views").glob("*.sql")}
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    with engine.connect() as conn:
        present = {
            r[0] for r in conn.execute(text("SELECT viewname FROM pg_views WHERE schemaname = 'needs'"))
        }
    engine.dispose()
    assert owned <= present, sorted(owned - present)
