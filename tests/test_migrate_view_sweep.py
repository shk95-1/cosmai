"""배포가 남의 뷰를 지우지 않는다 (#150).

#138 이 뷰 사이 의존 때문에 재적용이 죽는 것을 고치면서 스키마의 뷰를 **전부** 떨어뜨렸다.
운영 `needs` 에는 포크가 만든 뷰 둘(`metrics_topic_quarter_violation`·`topic_quarter_judgement_violation`,
포크 DDL 022·024)이 살고 upstream 의 `db/views/` 는 그것을 모른다 -- 스윕이 지운 뒤 다시 만드는
루프가 도는 것은 `db/views/*.sql` 뿐이라 그 둘은 그대로 사라진다.

여기서는 그 결과를 잰다: `db/views/` 에 파일이 없는 뷰를 하나 심어 두고 배포를 두 번 돌린 뒤
살아 있는지 묻는다. 소스를 grep 하지 않는 이유는 스윕의 *모양*이 아니라 *범위*가 문제여서다.
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
    """하네스가 띄운 컨테이너 이름. tool/checks/test 가 포트에서 이름을 짓는 규칙 그대로다."""
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

    # 정리 블록이 이름을 항상 갖도록 try 밖에서 만든다.
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
        # 하네스가 쓰는 것과 같은 더미다(tool/checks/test) -- secret 이 아니다.
        fh.write("NEEDS_DB_MIGRATOR=check\nNEEDS_DB_RUNTIME=check-runtime\n")
        secret = fh.name

    try:
        env = {**os.environ, "COSMAI_SECRET_FILE": secret}
        # 두 번 돈다: 한 번은 스윕이 도는 것을 보고, 두 번째는 재적용이 여전히 멱등한지 본다.
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
    # 범위를 좁히는 수정이 제 뷰까지 빠뜨리지 않았는지 -- 반대편을 함께 붙든다.
    _harness_container()
    owned = {p.stem for p in (REPO_ROOT / "db" / "views").glob("*.sql")}
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    with engine.connect() as conn:
        present = {
            r[0] for r in conn.execute(text("SELECT viewname FROM pg_views WHERE schemaname = 'needs'"))
        }
    engine.dispose()
    assert owned <= present, sorted(owned - present)
