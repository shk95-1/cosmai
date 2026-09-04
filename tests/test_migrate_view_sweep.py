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
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = "zz_foreign_view_probe"


def _harness_container() -> str:
    """The container name the harness started. Exactly the rule tool/checks/test uses to name it from
    the port."""
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    port = urlparse(url.replace("postgresql+psycopg://", "postgresql://")).port
    name = f"cosmai-test-postgres-{port}"
    probe = subprocess.run(["docker", "inspect", "-f", "{{.Name}}", name], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip(f"{name} 이 없다 -- 외부 TEST_POSTGRES_URL 로 도는 중")
    return name


def test_a_view_this_checkout_does_not_own_survives_the_deploy():
    container = _harness_container()
    url = os.environ["TEST_POSTGRES_URL"]
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")
        conn.exec_driver_sql(f"CREATE OR REPLACE VIEW needs.{PROBE} AS SELECT 1 AS one")
    engine.dispose()

    # Created outside the try so the cleanup block always has a name.
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
        # The same dummy the harness uses (tool/checks/test) -- not a secret.
        fh.write("NEEDS_DB_MIGRATOR=check\nNEEDS_DB_RUNTIME=check-runtime\n")
        secret = fh.name

    try:
        env = {**os.environ, "COSMAI_SECRET_FILE": secret}
        # Runs twice: once to watch the sweep run, and a second time to check that re-applying is still
        # idempotent.
        for _ in range(2):
            done = subprocess.run(
                ["db/migrate.sh", "--container", container, "--db", "fleet", "--superuser", "fleet"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
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
        os.unlink(secret)


def test_the_views_this_checkout_owns_are_all_present_after_the_deploy():
    # Whether a fix that narrows the scope did not also drop the views this checkout owns -- this holds
    # the opposite side at the same time.
    _harness_container()
    owned = {p.stem for p in (REPO_ROOT / "db" / "views").glob("*.sql")}
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    with engine.connect() as conn:
        present = {
            r[0] for r in conn.execute(text("SELECT viewname FROM pg_views WHERE schemaname = 'needs'"))
        }
    engine.dispose()
    assert owned <= present, sorted(owned - present)
