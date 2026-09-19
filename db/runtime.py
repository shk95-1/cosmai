"""The one place that builds the needs_runtime connection URL. Only the secret's key name lives here
(contracts/secrets.md)."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import psycopg
from sqlalchemy.engine import make_url

from db import secrets

RUNTIME_KEY = "NEEDS_DB_RUNTIME"
# On the host, shared-postgres's published port; inside the compose network, service-name:5432 -- both
# reach the same DB. The role, DB name and secret key name are a contract and do not move
# (contracts/secrets.md).
HOST_VAR = "COSMAI_DB_HOST"
PORT_VAR = "COSMAI_DB_PORT"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "5434"
RUNTIME_DSN = "postgresql+psycopg://needs_runtime:{password}@{host}:{port}/app"


def host_and_port(host: str | None = None, port: int | str | None = None) -> tuple[str, str]:
    """The one place that lets the three spots in this repo building a DSN (here, commerce, youtube)
    read the same knobs under the same rule.

    An explicit argument wins over env: a caller that passed an argument is targeting a specific DB,
    while env is the deployment unit's default for when no one pointed at anything in particular.
    """
    # compose passes an unset ${VAR} through as an empty string -- that must fall through to the
    # default, not become an empty host.
    return (
        str(host or os.environ.get(HOST_VAR) or DEFAULT_HOST),
        str(port or os.environ.get(PORT_VAR) or DEFAULT_PORT),
    )


def runtime_url() -> str:
    """The SQLAlchemy `create_engine` form (`+psycopg` in the scheme). Never hand this to
    `psycopg.connect()`: it takes a conninfo string, the `+psycopg` prefix makes that conninfo
    malformed, and the resulting error message is the whole string -- password included. Use
    `connect()` below, or `create_engine(runtime_url())`.
    """
    password = quote(secrets.require([RUNTIME_KEY])[RUNTIME_KEY], safe="")
    host, port = host_and_port()
    return RUNTIME_DSN.format(password=password, host=host, port=port)


def connect(url: str) -> psycopg.Connection[Any]:
    """Open a psycopg connection from a SQLAlchemy-form URL without ever building a conninfo
    string: `make_url` parses it and each part is passed to `psycopg.connect()` as a keyword
    argument, so a malformed conninfo can never happen and can never echo the password."""
    u = make_url(url)
    kwargs: dict[str, Any] = {
        k: v
        for k, v in (
            ("host", u.host),
            ("port", u.port),
            ("user", u.username),
            ("password", u.password),
            ("dbname", u.database),
        )
        if v is not None
    }
    kwargs.update({k: v for k, v in u.query.items() if isinstance(v, str)})
    return psycopg.connect(**kwargs)
