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

ENTRYPOINTS_MD = Path(__file__).resolve().parents[1] / "contracts" / "entrypoints.md"


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


# --- the two collectors that do not use db/runtime.py's URL --------------------------------------
#
# commerce and youtube each build their own DSN (their own role, search_path and secret key), so
# A-3's env knobs have to reach them too or `cosmai collect commerce|youtube` in a container still
# dials 127.0.0.1:5434. Same two variables, same defaults, same empty-string rule.
#
# #29: the two roles' production passwords differ from each other and from COSMA_DB_RUNTIME, so each
# collector reads its own key now. These fixtures give the two collectors different passwords on
# purpose -- a regression back to one shared key produces a DSN with the wrong password (a test
# failure) instead of silently passing because both happened to read the same value.

COLLECTOR_DSNS = {
    "commerce": "postgresql+psycopg://trend_radar_runtime:{p}@{host}:{port}/app"
    "?options=-csearch_path%3Dtrend_radar%2Cpg_catalog",
    "youtube": "postgresql+psycopg://tubedepth_runtime:{p}@{host}:{port}/app"
    "?options=-csearch_path%3Dtubedepth%2Cpg_catalog",
}

COLLECTOR_SECRET_KEYS = {
    "commerce": "TREND_RADAR_DB_RUNTIME",
    "youtube": "TUBEDEPTH_DB_RUNTIME",
}

COLLECTOR_PASSWORDS = {
    "commerce": "commerce-only-pass",
    "youtube": "youtube-only-pass",
}


def collector_module(collector: str):
    from collectors.commerce.storage import db as commerce_db
    from collectors.youtube.storage import db as youtube_db

    return {"commerce": commerce_db, "youtube": youtube_db}[collector]


def collector_runtime_url(collector: str):
    return collector_module(collector).runtime_url


@pytest.fixture
def collector_secret_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "collector-env"
    lines = [f"{COLLECTOR_SECRET_KEYS[c]}={COLLECTOR_PASSWORDS[c]}" for c in sorted(COLLECTOR_SECRET_KEYS)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setenv(secrets.ENV_PATH_VAR, str(path))
    for var in (runtime.HOST_VAR, runtime.PORT_VAR):
        monkeypatch.delenv(var, raising=False)
    return path


@pytest.mark.parametrize("collector", sorted(COLLECTOR_DSNS))
def test_a_collector_defaults_to_the_published_port_on_localhost(collector: str, collector_secret_file: Path):
    assert collector_runtime_url(collector)() == COLLECTOR_DSNS[collector].format(
        p=COLLECTOR_PASSWORDS[collector], host="127.0.0.1", port=5434
    )


@pytest.mark.parametrize("collector", sorted(COLLECTOR_DSNS))
def test_a_collector_takes_the_host_and_port_from_the_environment(
    collector: str, collector_secret_file: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(runtime.HOST_VAR, "shared-postgres")
    monkeypatch.setenv(runtime.PORT_VAR, "5432")
    assert collector_runtime_url(collector)() == COLLECTOR_DSNS[collector].format(
        p=COLLECTOR_PASSWORDS[collector], host="shared-postgres", port=5432
    )


@pytest.mark.parametrize("collector", sorted(COLLECTOR_DSNS))
def test_a_collectors_empty_override_is_the_default_not_an_empty_host(
    collector: str, collector_secret_file: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(runtime.HOST_VAR, "")
    monkeypatch.setenv(runtime.PORT_VAR, "")
    assert collector_runtime_url(collector)() == COLLECTOR_DSNS[collector].format(
        p=COLLECTOR_PASSWORDS[collector], host="127.0.0.1", port=5434
    )


@pytest.mark.parametrize("collector", sorted(COLLECTOR_DSNS))
def test_an_explicit_collector_argument_beats_the_environment(
    collector: str, collector_secret_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """A caller that names a host is pointing at a specific database on purpose; the environment is
    only the deployment-wide default for callers that name none."""
    monkeypatch.setenv(runtime.HOST_VAR, "shared-postgres")
    monkeypatch.setenv(runtime.PORT_VAR, "5432")
    assert collector_runtime_url(collector)(host="db.example", port=6000) == COLLECTOR_DSNS[collector].format(
        p=COLLECTOR_PASSWORDS[collector], host="db.example", port=6000
    )


@pytest.mark.parametrize("collector", sorted(COLLECTOR_SECRET_KEYS))
def test_a_collector_exits_naming_its_own_missing_key(
    collector: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Only the *other* collector's key is present -- a fallback to a shared key would find one and
    connect with the wrong password instead of failing loudly and by name."""
    other = next(c for c in COLLECTOR_SECRET_KEYS if c != collector)
    path = tmp_path / "collector-env"
    path.write_text(f"{COLLECTOR_SECRET_KEYS[other]}={COLLECTOR_PASSWORDS[other]}\n", encoding="utf-8")
    monkeypatch.setenv(secrets.ENV_PATH_VAR, str(path))
    with pytest.raises(SystemExit) as exc_info:
        collector_runtime_url(collector)()
    assert COLLECTOR_SECRET_KEYS[collector] in str(exc_info.value)
    assert COLLECTOR_PASSWORDS[other] not in str(exc_info.value)


def test_commerce_and_youtube_read_different_secret_keys():
    """#29: the two roles' production passwords differ, so the two modules must read different keys
    -- a regression back to sharing one key (e.g. COSMA_DB_RUNTIME again) fails this rather than
    silently reviving 'FATAL: password authentication failed for user \"trend_radar_runtime\"'."""
    commerce_key = collector_module("commerce").RUNTIME_SECRET_KEY
    youtube_key = collector_module("youtube").RUNTIME_SECRET_KEY
    assert commerce_key != youtube_key
    assert commerce_key == "TREND_RADAR_DB_RUNTIME"
    assert youtube_key == "TUBEDEPTH_DB_RUNTIME"
    assert "COSMA_DB_RUNTIME" not in (commerce_key, youtube_key)


def test_the_contract_names_both_knobs_and_their_defaults():
    """#13 (compose) has to read the names and defaults somewhere other than this file."""
    contract = ENTRYPOINTS_MD.read_text(encoding="utf-8")
    for token in (runtime.HOST_VAR, runtime.PORT_VAR, runtime.DEFAULT_HOST, runtime.DEFAULT_PORT):
        assert token in contract, f"contracts/entrypoints.md does not name {token}"
