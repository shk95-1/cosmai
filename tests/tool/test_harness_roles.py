"""#216: the throwaway container gets connection room the parallel workers need; production does not.

`db/bootstrap.sql` is the file production runs, and it caps `needs_migrator` at 2 connections --
enough for one deploy, and exhausted by the second pytest worker, since every worker's schema
fixture connects as that role. `tool/checks/harness-roles` widens the two roles inside the
throwaway container after the deploy has run, the same harness-only shape as the `GRANT CREATE`
next to it. This file holds the boundary: what it widens, by how much, and that the DDL production
applies still says 2 and 12.

No Docker here -- `docker` is a fake first on PATH that writes down every argument it was handed,
the shape tests/tool/test_suite_lock.py already uses for its own fragment.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRAGMENT = REPO_ROOT / "tool" / "checks" / "harness-roles"
CHECK = REPO_ROOT / "tool" / "checks" / "test"
BOOTSTRAP = REPO_ROOT / "db" / "bootstrap.sql"

FAKE_DOCKER = """#!/bin/sh
for arg in "$@"; do printf '%s\\n' "$arg" >> "$FAKE_DOCKER_LOG"; done
exit 0
"""


@pytest.fixture
def widen(tmp_path: Path):
    """Sources the fragment and calls it, with a fake `docker` that only records."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    log = tmp_path / "docker.log"

    def _widen(**overrides: str) -> str:
        log.write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(log),
            **overrides,
        }
        done = subprocess.run(
            ["sh", "-c", ". tool/checks/harness-roles; harness_roles_widen cosmai-test-postgres-56216 fleet"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return log.read_text(encoding="utf-8")

    return _widen


def test_it_widens_the_migrator_inside_the_container_only(widen):
    recorded = widen()
    assert "ALTER ROLE needs_migrator CONNECTION LIMIT 8" in recorded, recorded
    assert "cosmai-test-postgres-56216" in recorded, recorded


def test_it_widens_the_runtime_role_too(widen):
    # Four workers, each entitled to the budget one production process gets (12).
    recorded = widen()
    assert "ALTER ROLE needs_runtime CONNECTION LIMIT 48" in recorded, recorded


def test_the_limits_can_be_dialed_from_the_environment(widen):
    recorded = widen(COSMAI_HARNESS_MIGRATOR_LIMIT="3", COSMAI_HARNESS_RUNTIME_LIMIT="17")
    assert "ALTER ROLE needs_migrator CONNECTION LIMIT 3" in recorded, recorded
    assert "ALTER ROLE needs_runtime CONNECTION LIMIT 17" in recorded, recorded


def test_no_password_reaches_the_command_line(widen):
    """The #20 rule the deploy lives under holds here too: this speaks as the container's superuser
    through `docker exec`, so nothing it hands docker is a credential."""
    recorded = widen()
    assert "check-runtime" not in recorded and "PASSWORD" not in recorded, recorded


def test_production_ddl_still_carries_the_narrow_limits():
    body = BOOTSTRAP.read_text(encoding="utf-8")
    assert "CONNECTION LIMIT 2 PASSWORD" in body, (
        "db/bootstrap.sql's migrator limit moved -- this is production"
    )
    assert "CONNECTION LIMIT 12 PASSWORD" in body, (
        "db/bootstrap.sql's runtime limit moved -- this is production"
    )


def test_the_suite_runner_widens_after_the_deploy_has_run():
    """Before the second `db/migrate.sh` the roles may not exist yet -- an empty container is what
    step (0) of that script builds them in."""
    body = CHECK.read_text(encoding="utf-8")
    assert ". tool/checks/harness-roles" in body, body
    lines = body.splitlines()
    widened = [
        i for i, ln in enumerate(lines) if "harness_roles_widen" in ln and not ln.lstrip().startswith("#")
    ]
    migrated = [i for i, ln in enumerate(lines) if "db/migrate.sh --container" in ln]
    assert widened and len(migrated) == 2, (widened, migrated)
    assert widened[0] > migrated[-1], "the widening must come after both deploy runs"
