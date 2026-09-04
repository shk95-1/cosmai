"""#214: three suites fit in this host's memory and a fourth kills one of them (#212, #204).

`tool/checks/suite-lock` is the fragment `tool/checks/test` sources before it starts a container.
It counts the running `cosmai-test-postgres-*` containers AND the lock directories -- a suite that
is still migrating has no container yet -- and waits while the larger number is at the limit.

No Docker here: `docker` is a fake first on PATH that answers each call from a plan file, and the
poll interval comes from COSMAI_SUITE_WAIT_SECONDS so the wait is exercised without sleeping 15 s.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PORT = "55123"

# One line of the plan per `docker ps` call: how many suite containers are running right now.
# An exhausted plan means none, so a test cannot hang on a plan that ran out.
FAKE_DOCKER = """#!/bin/sh
plan="$FAKE_DOCKER_PLAN"
running=$(head -n 1 "$plan" 2>/dev/null)
[ -n "$running" ] || running=0
tail -n +2 "$plan" > "$plan.rest" 2>/dev/null || true
mv "$plan.rest" "$plan" 2>/dev/null || true
i=1
while [ "$i" -le "$running" ]; do
    printf 'cosmai-test-postgres-%s\\n' "$i"
    i=$((i + 1))
done
exit 0
"""


@pytest.fixture
def acquire(tmp_path: Path):
    """Sources the fragment and takes a slot, with a fake `docker` and a throwaway lock root."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    lock_root = tmp_path / "locks"

    def _acquire(running: list[int], interval: str = "0", timeout: float | None = 20.0):
        plan = tmp_path / "plan"
        plan.write_text("".join(f"{n}\n" for n in running), encoding="utf-8")
        return subprocess.run(
            ["sh", "-c", f'. tool/checks/suite-lock; suite_lock_acquire {PORT}; printf "acquired\\n"'],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env={
                **os.environ,
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "FAKE_DOCKER_PLAN": str(plan),
                "COSMAI_SUITE_LOCK_DIR": str(lock_root),
                "COSMAI_SUITE_WAIT_SECONDS": interval,
            },
        )

    _acquire.lock_root = lock_root  # type: ignore[attr-defined]
    return _acquire


def waiting_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("waiting: ")]


def test_a_free_host_starts_at_once(acquire):
    done = acquire([0])
    assert done.returncode == 0, done.stderr
    assert "acquired" in done.stdout
    assert waiting_lines(done.stdout) == []


def test_the_slot_is_taken_as_a_directory_named_for_the_port(acquire):
    done = acquire([0])
    assert done.returncode == 0, done.stderr
    assert (acquire.lock_root / PORT).is_dir(), "no lock directory, so a starting suite is invisible"


def test_two_running_suites_are_under_the_limit(acquire):
    done = acquire([2])
    assert waiting_lines(done.stdout) == [], done.stdout
    assert "acquired" in done.stdout


def test_a_fourth_suite_waits_until_a_slot_frees(acquire):
    done = acquire([3, 3, 0])
    assert done.returncode == 0, done.stderr
    assert waiting_lines(done.stdout) == ["waiting: 3 suites running (limit 3)"], done.stdout
    assert "acquired" in done.stdout


def test_the_waiting_line_is_not_repeated_on_every_poll(acquire):
    # A twenty-minute wait must not scroll the terminal: one line a minute, not one a poll.
    done = acquire([3, 3, 3, 3, 3, 3, 0])
    assert len(waiting_lines(done.stdout)) == 1, done.stdout


def test_lock_directories_count_as_suites_that_have_no_container_yet(acquire):
    # A suite between `docker run` and its first test has a lock directory and, for a moment, no
    # container `docker ps` will name; counting only containers would let a fourth one start.
    for port in ("55001", "55002", "55003"):
        (acquire.lock_root / port).mkdir(parents=True)
    with pytest.raises(subprocess.TimeoutExpired) as waited:
        acquire([0, 0, 0, 0], interval="1", timeout=2.5)
    stdout = (waited.value.stdout or b"").decode()
    assert "waiting: 3 suites running (limit 3)" in stdout, stdout
    assert "acquired" not in stdout, stdout
