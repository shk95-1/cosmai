"""origin: service/yt-scrapper/tests/test_compose.py + tests/test_deployment_units.py (the one question worth keeping)
reuse: every `command:` in stack/docker-compose.yml and every crontab line must name a subcommand and options the CLI
actually has. Pure parse + `--help` subprocess with a bare environment, so it runs offline.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CLI = "cosmai"  # console script name; run as `python -m cosmai.cli`
MODULE = "cosmai.cli"


def help_for(*argv: str) -> str:
    out = subprocess.run([sys.executable, "-m", MODULE, *argv, "--help"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "COLUMNS": "200"}, check=False)
    assert out.returncode == 0, f"{CLI} {' '.join(argv)} --help failed:\n{out.stderr}"
    return out.stdout


def _command_lines() -> list[list[str]]:
    lines: list[list[str]] = []
    compose = yaml.safe_load((ROOT / "stack" / "docker-compose.yml").read_text())
    for name, svc in compose.get("services", {}).items():
        cmd = svc.get("command")
        if isinstance(cmd, list) and cmd and cmd[0] == CLI:
            lines.append(cmd)
    for ln in (ROOT / "stack" / "crontab").read_text().splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            parts = ln.split()[5:]
            if parts and parts[0] == CLI:
                lines.append(parts)
    return lines


LINES = _command_lines()


def test_there_are_command_lines_to_check():
    assert LINES


@pytest.mark.parametrize("argv", LINES, ids=lambda a: " ".join(a[:3]))
def test_every_wired_command_exists_with_its_options(argv: list[str]):
    sub = argv[1]
    text = help_for(sub)
    for opt in (a for a in argv[2:] if a.startswith("--")):
        assert re.search(rf"(^|\s){re.escape(opt)}(\s|$|,)", text), f"{CLI} {sub} has no option {opt}"
