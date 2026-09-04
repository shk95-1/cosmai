"""The one place that builds the needs_runtime connection URL. Only the secret's key name lives here
(contracts/secrets.md)."""

from __future__ import annotations

import os
from urllib.parse import quote

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
    password = quote(secrets.require([RUNTIME_KEY])[RUNTIME_KEY], safe="")
    host, port = host_and_port()
    return RUNTIME_DSN.format(password=password, host=host, port=port)
