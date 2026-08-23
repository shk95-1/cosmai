"""origin: service/cosmai/apps/tests/conftest.py:430-520 (cosmai-old start_worker / wait_for_worker / run_worker)
reuse: for tests that must cross a process boundary (lease expiry, SIGINT, two workers on one queue). Both streams are
captured and shown on timeout, so a hanging process reports what it was saying rather than nothing.
"""

from __future__ import annotations

import subprocess
import sys

PROCESS_TIMEOUT_SECONDS = 30.0


def command(module: str, *arguments: str) -> list[str]:
    return [sys.executable, "-m", module, *arguments]


def start(module: str, env: dict[str, str], *arguments: str) -> subprocess.Popen[str]:
    """Start the process and return it still running. `env` replaces the environment entirely."""
    return subprocess.Popen(command(module, *arguments), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def wait(process: subprocess.Popen[str], timeout: float = PROCESS_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
        raise AssertionError(f"the process did not exit within {timeout}s\nstdout:\n{out}\nstderr:\n{err}") from None
    return subprocess.CompletedProcess(process.args, process.returncode, out, err)


def run(module: str, env: dict[str, str], *arguments: str, timeout: float = PROCESS_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    """Run to completion. The common case in a scenario."""
    return wait(start(module, env, *arguments), timeout=timeout)
