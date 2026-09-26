"""Restored viewers must not revive old writers or merge the independent Run database."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def parsed(tmp_path):
    (tmp_path / "tubedepth.env").write_text("")

    def render(filename):
        result = subprocess.run(
            ["docker", "compose", "-f", str(ROOT / "stack" / filename), "config", "--format", "json"],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "POSTGREST_AUTH_PASSWORD": "fixture",
                "TREND_RADAR_READER_PASSWORD": "fixture",
                "LEGACY_SOURCE_ROOT": str(tmp_path),
                "LEGACY_RUNTIME_DIR": str(tmp_path),
                "COSMAI_RUN_DIR": str(tmp_path),
                "COSMAI_RUN_PG_DATA_DIR": str(tmp_path / "run-postgres"),
                "COSMAI_HOST_UID": "2000",
                "COSMAI_HOST_GID": "100",
            },
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    return render


def test_retained_viewers_start_without_old_writers_or_migrations(parsed):
    model = parsed("docker-compose.legacy.yml")
    services = model["services"]
    assert set(services) == {"postgrest", "data-portal", "trend-radar-dashboard", "tubedepth-api"}
    assert not services["trend-radar-dashboard"].get("depends_on")
    assert not services["tubedepth-api"].get("depends_on")
    assert model["networks"]["db-net"]["external"] is True
    assert services["tubedepth-api"]["user"] == "2000:100"
    payload = services["tubedepth-api"]["volumes"][0]
    assert payload["read_only"] is True
    assert model["volumes"][payload["source"]]["name"] == "cosmai_youtube-payloads"


def test_run_uses_its_own_data_and_network_without_a_host_database_port(parsed):
    model = parsed("docker-compose.run.yml")
    services = model["services"]
    assert set(services) == {"postgres", "postgrest", "web"}
    assert not services["postgres"].get("ports")
    assert "db-net" not in model["networks"]
    data = services["postgres"]["volumes"][0]
    assert data["source"].endswith("/run-postgres")
    assert data["target"] == "/var/lib/postgresql"
    assert services["postgrest"]["environment"]["PGRST_DB_URI"].endswith("@postgres:5432/app")
