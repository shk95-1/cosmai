"""origin: service/yt-scrapper/tests/test_postgres_privileges.py (negative-space proof, condensed)
reuse: set RUNTIME_URL_ENV; run under tool/checks/test which exports it. Marked `postgres`: needs the real roles.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

RUNTIME_URL_ENV = "TEST_POSTGRES_RUNTIME_URL"
SCHEMA = "app_schema"

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("ddl", [f"CREATE TABLE {SCHEMA}.x (id int)", f"ALTER TABLE {SCHEMA}.run ADD COLUMN x int", f"DROP TABLE {SCHEMA}.run"])
def test_the_runtime_role_is_refused_ddl(ddl: str):
    url = os.environ.get(RUNTIME_URL_ENV) or pytest.skip(f"set {RUNTIME_URL_ENV}")
    engine = create_engine(url)
    with pytest.raises(ProgrammingError, match="permission denied|must be owner"):
        with engine.begin() as conn:
            conn.execute(text(ddl))
    engine.dispose()
