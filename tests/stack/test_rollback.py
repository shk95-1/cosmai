"""What stack/rollback.sh does before it is needed, run against a throwaway old stack.

The script is the emergency path, so the only rehearsal anyone gets is --dry-run. These drive that
rehearsal against a fixture stack rather than the real one: a compose project whose third service is
declared in `docker-compose.override.yml`, which is exactly the shape the real old stack has today
(service/stack/docker-compose.override.yml mounts the host crontab over the baked one). A script that
passes `-f` turns compose's default file discovery off and never sees that file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLLBACK = REPO_ROOT / "stack" / "rollback.sh"

BASE = """\
name: cosmai-rollback-fixture
services:
  trend-radar-collector:
    image: busybox
    command: ["true"]
  tubedepth-worker:
    image: busybox
    command: ["true"]
"""
# The service the base file does not define: only a run that merges the override can see it.
OVERRIDE = """\
services:
  tubedepth-flatten:
    image: busybox
    command: ["true"]
"""


@pytest.fixture
def old_stack(tmp_path: Path) -> Path:
    stack = tmp_path / "old-stack"
    stack.mkdir()
    (stack / "docker-compose.yml").write_text(BASE, encoding="utf-8")
    (stack / "docker-compose.override.yml").write_text(OVERRIDE, encoding="utf-8")
    return stack


def _dry_run(old_stack: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ROLLBACK), "--dry-run"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "OLD_STACK_DIR": str(old_stack), **env},
        check=False,
    )


def test_the_old_stack_is_read_with_the_override_it_ships(old_stack: Path):
    # tubedepth-flatten exists only in the override. `-f <base>` would make compose skip that file,
    # and then `up -d` recreates the containers without whatever it holds -- for the real old stack,
    # without the crontab mount that #10 §A-4 applied there without a rebuild.
    done = _dry_run(old_stack)
    assert done.returncode == 0, f"--dry-run failed:\n{done.stdout}\n{done.stderr}"


def test_the_dry_run_rehearses_every_old_service(old_stack: Path):
    # A rollback that prints success and revives nothing is the failure this rehearsal is for.
    done = _dry_run(old_stack)
    for service in ("trend-radar-collector", "tubedepth-worker", "tubedepth-flatten"):
        assert service in done.stdout, f"--dry-run says nothing about {service}"


def test_a_service_the_old_stack_lost_stops_the_rollback(old_stack: Path, tmp_path: Path):
    (old_stack / "docker-compose.override.yml").unlink()
    done = _dry_run(old_stack)
    assert done.returncode == 1, "a missing old service must stop the rollback, not be reported as fine"
    assert "tubedepth-flatten" in done.stderr


def test_missing_docker_fails_even_when_require_native_is_zero(old_stack: Path):
    # An operator's shell may carry REQUIRE_NATIVE=0 from tool/checks/*; honouring it here would end
    # the rollback at exit 69 ("unverified") having done nothing, in the middle of an incident.
    bin_dir = old_stack.parent / "bin"
    bin_dir.mkdir()
    # dirname resolves the repo root at the top of the script; everything else the script needs
    # before the docker check is a shell builtin.
    (bin_dir / "dirname").symlink_to(shutil.which("dirname") or "/usr/bin/dirname")
    done = _dry_run(old_stack, PATH=str(bin_dir), REQUIRE_NATIVE="0")
    assert done.returncode == 1, f"rc={done.returncode} (69 = the silent unverified exit)"
