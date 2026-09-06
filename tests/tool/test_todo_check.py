"""tool/checks/todo: a staged TODO(#n) must name an issue, in its own repo or another's (#238).

Two repos share one history now, so a bare TODO(#n) is ambiguous once a commit crosses a fork
boundary (#175 made the same call for `Closes #n`). The shape check accepts a repo-qualified form,
`TODO(owner/repo#n)`, next to the bare one -- everything else still fails the commit.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK = REPO_ROOT / "tool" / "checks" / "todo"

# Hooks export GIT_DIR; left in place these commands would act on the enclosing checkout (#60 trap).
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)], check=True, capture_output=True, env=CLEAN_ENV
    )
    return tmp_path


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env=CLEAN_ENV,
    )


def stage(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(repo, "add", "--", path)


def run_check(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(CHECK)], cwd=str(repo), capture_output=True, text=True, check=False, env=CLEAN_ENV
    )


def test_a_bare_marker_is_accepted(repo: Path):
    stage(repo, "pipeline.py", "# TO" + "DO(#12) fix it while you are in here\n")
    done = run_check(repo)
    assert done.returncode == 0, (done.stdout, done.stderr)


def test_a_repo_qualified_marker_is_accepted(repo: Path):
    stage(repo, "pipeline.py", "# TO" + "DO(shk95/cosmai-import-ydc#88) fix it while you are in here\n")
    done = run_check(repo)
    assert done.returncode == 0, (done.stdout, done.stderr)


def test_a_marker_with_no_issue_number_is_rejected(repo: Path):
    stage(repo, "pipeline.py", "# TO" + "DO: later\n")
    done = run_check(repo)
    assert done.returncode == 1, (done.stdout, done.stderr)
    assert "issue" in done.stderr, done.stderr


def test_a_marker_with_a_bare_number_and_no_hash_is_rejected(repo: Path):
    stage(repo, "pipeline.py", "# TO" + "DO(88) fix it while you are in here\n")
    done = run_check(repo)
    assert done.returncode == 1, (done.stdout, done.stderr)
