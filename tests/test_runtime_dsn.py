"""needs_runtime's host and port, which differ between the host and a container.

`uv run cosmai ...` on the operator's machine reaches shared-postgres through the published port on
127.0.0.1; inside the compose network the same database is the service name on 5432. Only the host
and the port move -- the role, the database and the secret key name are contracts/secrets.md's and
are not env-tunable here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from db import runtime, secrets


@pytest.fixture
def secret_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "env"
    path.write_text(f"{runtime.RUNTIME_KEY}=p@ss/word\n", encoding="utf-8")
    monkeypatch.setenv(secrets.ENV_PATH_VAR, str(path))
    for var in (runtime.HOST_VAR, runtime.PORT_VAR):
        monkeypatch.delenv(var, raising=False)
    return path


def test_the_default_is_the_published_port_on_localhost(secret_file: Path):
    """The operator runs `uv run cosmai ...` from the host; that has to keep working untouched."""
    assert runtime.runtime_url() == "postgresql+psycopg://needs_runtime:p%40ss%2Fword@127.0.0.1:5434/app"


def test_the_host_and_port_come_from_the_environment_when_it_sets_them(
    secret_file: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(runtime.HOST_VAR, "shared-postgres")
    monkeypatch.setenv(runtime.PORT_VAR, "5432")
    assert runtime.runtime_url() == (
        "postgresql+psycopg://needs_runtime:p%40ss%2Fword@shared-postgres:5432/app"
    )


def test_an_empty_override_is_the_default_not_an_empty_host(
    secret_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """compose passes `${VAR}` through as an empty string when the .env has no such key."""
    monkeypatch.setenv(runtime.HOST_VAR, "")
    monkeypatch.setenv(runtime.PORT_VAR, "")
    assert runtime.runtime_url() == "postgresql+psycopg://needs_runtime:p%40ss%2Fword@127.0.0.1:5434/app"


def test_the_secret_key_name_is_still_the_one_the_contract_names(secret_file: Path):
    assert runtime.RUNTIME_KEY == "NEEDS_DB_RUNTIME"
    assert runtime.HOST_VAR.startswith("COSMAI_") and runtime.PORT_VAR.startswith("COSMAI_")
