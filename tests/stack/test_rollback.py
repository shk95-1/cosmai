"""Emergency pause must stop only current schedulers, never revive historical collectors."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROLLBACK = ROOT / "stack/rollback.sh"
SERVICES = [
    "collector-commerce",
    "collector-naver",
    "collector-youtube-watch",
    "collector-youtube-work",
    "collector-youtube-flatten",
    "analyze",
]


@pytest.fixture
def run(tmp_path):
    directory = tmp_path / "bin"
    directory.mkdir()
    log = tmp_path / "calls"
    docker = directory / "docker"
    docker.write_text("""#!/bin/sh
printf '%s\\n' "$*" >> "$ROLLBACK_CALLS"
case "$*" in
  *"config --services") printf '%s\\n' $ROLLBACK_SERVICES ;;
  *"stop "*) ;;
  *) exit 1 ;;
esac
""")
    docker.chmod(0o755)

    def invoke(*args, services=SERVICES, env_extra=None):
        result = subprocess.run(
            [str(ROLLBACK), *args],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": str(directory) + os.pathsep + os.environ["PATH"],
                "ROLLBACK_CALLS": str(log),
                "ROLLBACK_SERVICES": " ".join(services),
                "OLD_STACK_DIR": str(tmp_path / "missing-historical-stack"),
                **(env_extra or {}),
            },
        )
        return result, log.read_text().splitlines() if log.exists() else []

    return invoke


def test_dry_run_lists_all_schedulers_without_mutation_or_old_stack(run):
    result, calls = run("--dry-run")
    assert result.returncode == 0, result.stderr
    assert all(name in result.stdout for name in SERVICES)
    assert len(calls) == 1 and calls[0].endswith("config --services")


def test_pause_stops_only_current_schedulers(run):
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2
    assert calls[1].split(" stop ", 1)[1].split() == SERVICES
    for retired in ("trend-radar-collector", "tubedepth-worker", "tubedepth-flatten"):
        assert retired not in "\n".join(calls)
    assert not any(" up " in call or " down " in call for call in calls)


def test_missing_scheduler_refuses_before_any_stop(run):
    result, calls = run(services=SERVICES[:-1])
    assert result.returncode == 1
    assert "analyze" in result.stderr
    assert not any(" stop " in call for call in calls)


def test_missing_docker_fails_even_when_require_native_is_zero(run, tmp_path):
    directory = tmp_path / "no-docker"
    directory.mkdir()
    (directory / "dirname").symlink_to(shutil.which("dirname") or "/usr/bin/dirname")
    result, calls = run("--dry-run", env_extra={"PATH": str(directory), "REQUIRE_NATIVE": "0"})
    assert result.returncode == 1
    assert not calls


def test_unknown_argument_refuses_before_any_docker_action(run):
    result, calls = run("--revive-old-collectors")
    assert result.returncode == 1
    assert not calls
