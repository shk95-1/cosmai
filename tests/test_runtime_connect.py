"""#257: `runtime_url()` is SQLAlchemy-shaped and a malformed conninfo string to psycopg -- the
malformed-conninfo error prints the whole string, password included. `db.runtime.connect` is the
safe, discoverable way to open the runtime DB instead, built on the parse-then-kwargs path that
already lived at `db/seed/_common.py`.

Neither test below builds an assertion message from the conninfo or the connection: a failure here
must never be able to echo the password (compare types/booleans, not strings).
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from db import runtime, secrets
from db.runtime import connect
from db.seed._common import connect as seed_connect


@pytest.fixture
def secret_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "env"
    path.write_text(f"{runtime.RUNTIME_KEY}=p@ss/word\n", encoding="utf-8")
    monkeypatch.setenv(secrets.ENV_PATH_VAR, str(path))
    return path


def test_runtime_url_is_not_a_valid_conninfo_string(secret_file: Path):
    """The shape the leak needs: a caller who hands `runtime_url()` to something conninfo-shaped
    never gets a connection, only an exception -- proven by type, not by reading its message."""
    with pytest.raises(psycopg.ProgrammingError):
        conninfo_to_dict(runtime.runtime_url())


def test_db_seed_common_connect_is_the_same_function_db_runtime_exports():
    """One implementation: `db/seed/_common.py`'s existing callers (analysis/predictors.py,
    cosmai/cli.py) must not silently drift from the documented `db.runtime.connect`."""
    assert seed_connect is connect


def test_connect_opens_a_live_connection_on_the_harness_db(needs_runtime_url: str):
    """The other half of the shape: the safe path is not just "parses", it is a live connection --
    the weaker test (asserting only that the URL parses) walks straight past a leak that fires on
    any malformed conninfo, not just this one prefix."""
    conn = connect(needs_runtime_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            ok = cur.fetchone() == (1,)
    finally:
        conn.close()
    assert ok
