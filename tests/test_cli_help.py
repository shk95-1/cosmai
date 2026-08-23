"""`cosmai --help` 는 stack/crontab 과 compose 가 겨누는 표면이다 — 바이트로 고정한다.

바꾼 것이 의도한 것이면: uv run pytest tests/test_cli_help.py --snapshot-update
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "cosmai_help.txt"
COMMANDS = (
    (),
    ("collect",),
    ("analyze",),
    ("eval",),
    ("lexicon",),
    ("lexicon", "load"),
    ("lexicon", "diff"),
    ("lexicon", "activate"),
)


def _help(argv: tuple[str, ...]) -> str:
    out = subprocess.run(
        [sys.executable, "-m", "cosmai.cli", *argv, "--help"],
        capture_output=True,
        text=True,
        # COLUMNS 고정: argparse 는 터미널 폭에 맞춰 줄바꿈해서 폭이 다르면 스냅샷이 흔들린다.
        env={"PATH": "/usr/bin:/bin", "COLUMNS": "100"},
        check=False,
        cwd=SNAPSHOT.parents[2],
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_the_help_of_every_subcommand_matches_the_snapshot(snapshot_update: bool):
    rendered = "".join(f"$ {' '.join(('cosmai', *argv, '--help'))}\n{_help(argv)}\n" for argv in COMMANDS)
    if snapshot_update:
        SNAPSHOT.write_text(rendered, encoding="utf-8")
    assert rendered == SNAPSHOT.read_text(encoding="utf-8")
