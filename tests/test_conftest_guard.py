import socket

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
