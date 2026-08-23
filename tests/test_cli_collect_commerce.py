"""`python -m cosmai.cli collect commerce --help` -- offline, subprocess (origin: playbook/snippets/
test_stack_commands_resolve.py). The module form is checked here because cron and compose may run it
before anything is installed; `uv run cosmai` (the [project.scripts] entry) is covered by the help
snapshot in tests/test_cli_help.py."""

from __future__ import annotations

import subprocess
import sys


def _help(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "cosmai.cli", *argv, "--help"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=False,
    )


def test_collect_commerce_help_lists_every_option():
    out = _help("collect")
    assert out.returncode == 0, out.stderr
    for opt in ("--dataset", "--board", "--since"):
        assert opt in out.stdout


def test_an_unknown_dataset_is_refused_with_exit_code_2():
    from cosmai.cli import main

    code = main(["collect", "commerce", "--dataset", "not-a-real-dataset"])
    assert code == 2


def test_an_unwired_collector_is_refused_with_exit_code_2():
    from cosmai.cli import main

    code = main(["collect", "youtube", "--dataset", "watch"])
    assert code == 2
