"""origin: service/yt-scrapper/tests/conftest.py:128-197 (database_url_for_tests)
reuse: one schema per test on a real Postgres; change TEST_DB_URL_ENV and PREFIX; connect as the
migrator role (it needs CREATE ON DATABASE, granted by tool/checks/test, never by bootstrap.sql).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_DB_URL_ENV = "TEST_POSTGRES_URL"
PREFIX = "t"  # per package in a monorepo: "tr", "yt", "nd" ...


def _schema_name_for(nodeid: str) -> str:
    """Readable slug + sha1 tail; Postgres identifiers top out at 63 bytes."""
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", nodeid).strip("_").lower()
    digest = hashlib.sha1(nodeid.encode()).hexdigest()[:10]
    return f"{PREFIX}_{slug[: 63 - len(digest) - len(PREFIX) - 2]}_{digest}"


@pytest.fixture
def database_url_for_tests(request: pytest.FixtureRequest) -> Iterator[str]:
    """The one place the suite names a database. Schema per test, dropped on teardown."""
    server_url = os.environ.get(TEST_DB_URL_ENV)
    if not server_url:
        pytest.skip(f"set {TEST_DB_URL_ENV}, or run tool/checks/test")

    schema = _schema_name_for(request.node.nodeid)
    admin = create_engine(server_url)
    with admin.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()

    url = make_url(server_url).update_query_dict({"options": f"-csearch_path={schema},pg_catalog"})
    try:
        # Not str(url): SQLAlchemy masks the password as *** there.
        yield url.render_as_string(hide_password=False)
    finally:
        admin = create_engine(server_url)
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
