import os
import socket
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest


def test_an_unmarked_test_cannot_open_a_socket():
    with pytest.raises(RuntimeError, match="offline by construction"):
        socket.create_connection(("example.com", 80))


def test_the_guard_names_the_test_that_tripped_it():
    with pytest.raises(RuntimeError, match="test_the_guard_names_the_test_that_tripped_it"):
        socket.socket().connect(("example.com", 80))


def test_psycopg_connect_to_a_non_test_port_is_refused():
    """#8 수정 라운드 1 F-1: libpq opens its socket in C, under `socket.socket.connect` entirely, so
    the guard above never saw a psycopg connection at all -- an unguarded call reached real
    PostgreSQL and failed only at password auth. Port 1 rather than the production port (5434):
    nothing needs to be reachable for this to prove the refusal, and this way the test cannot be
    read as touching production even by coincidence."""
    with pytest.raises(RuntimeError, match="offline by construction"):
        psycopg.connect(host="127.0.0.1", port=1, dbname="whatever")


def test_psycopg_connect_names_the_host_and_port_it_tried():
    with pytest.raises(RuntimeError, match=r"127\.0\.0\.1.*1\b"):
        psycopg.connect(host="127.0.0.1", port=1, dbname="whatever")


def test_psycopg_connect_via_a_conninfo_string_is_also_refused():
    """The kwargs form is what every caller in this repo actually uses (db/seed/_common.py,
    storage/db.py's runtime_url), but the guard parses a bare conninfo string too -- covering a
    caller that ever passes one directly instead of kwargs."""
    with pytest.raises(RuntimeError, match="offline by construction"):
        psycopg.connect("host=127.0.0.1 port=1 dbname=whatever")


def test_psycopg_connection_connect_classmethod_is_also_refused():
    """SQLAlchemy's psycopg dialect calls the module-level `psycopg.connect`, but code could call the
    classmethod directly -- both names must be guarded, not just the one this repo happens to use."""
    with pytest.raises(RuntimeError, match="offline by construction"):
        psycopg.Connection.connect(host="127.0.0.1", port=1, dbname="whatever")


def test_sqlalchemy_engine_connect_to_a_non_test_port_is_also_refused():
    """#8 마무리 라운드 G-3: the round-0 incident happened through this exact path -- SQLAlchemy's
    psycopg dialect resolves `psycopg.connect` from the module at connect time, not the plain
    `psycopg.connect(...)` call the tests above exercise directly. Port 1, not 5434: the guard must
    refuse before anything is dialed, so this proves the refusal without needing production reachable
    or even real."""
    import sqlalchemy as sa

    engine = sa.create_engine("postgresql+psycopg://user:pw@127.0.0.1:1/whatever")
    try:
        with pytest.raises(RuntimeError, match="offline by construction"):
            engine.connect()
    finally:
        engine.dispose()


def _focused_run(env_value: str | None) -> subprocess.CompletedProcess[str]:
    """One `pytest <file>` in its own process, the way a worker runs a targeted test."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COSMAI_FULL_SUITE"))}
    if env_value is not None:
        env["COSMAI_FULL_SUITE"] = env_value
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_agents_md.py", "-p", "no:cacheprovider"],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_a_focused_run_ends_without_a_teardown_error():
    """#215: the session guard used to fail every targeted run, because a focused run registers no
    default implementations in the first place -- an ERROR about a run that did nothing wrong is how
    a gate teaches people to stop reading it."""
    done = _focused_run(None)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "error" not in done.stdout.lower(), done.stdout


def test_the_guard_is_still_armed_for_the_whole_suite():
    # Opt-in has to mean opt-in: with the flag tool/checks/test sets, the guard that caught #30 twice
    # must still fire on a session that never registered the defaults.
    done = _focused_run("1")
    assert done.returncode != 0, done.stdout
    assert "default registrations not restored" in done.stdout, done.stdout
