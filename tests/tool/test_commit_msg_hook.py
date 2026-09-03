"""#175: a bare `Closes #n` merged from upstream into a fork closes the fork's own unrelated
issue #n, since GitHub re-resolves the number against whichever repo receives the merge.
`.githooks/commit-msg` must reject bare cross-repo closing keywords and require `owner/repo#n`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".githooks" / "commit-msg"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo on branch repo/175-x, isolated from THIS checkout (#60 GIT_DIR trap)."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "-b", "repo/175-x"], check=True)
    return tmp_path


def run_hook(repo: Path, message: str) -> subprocess.CompletedProcess:
    msg_file = repo / "MSG"
    msg_file.write_text(message, encoding="utf-8")
    return subprocess.run(
        ["sh", str(HOOK), str(msg_file)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )


def test_bare_closes_is_rejected(repo: Path):
    done = run_hook(repo, "fix(hook): reject bare closes\n\nCloses #175\n")
    assert done.returncode == 1, done.stderr
    assert done.stderr.startswith("\n✗"), done.stderr


def test_owner_repo_closes_is_accepted(repo: Path):
    done = run_hook(repo, "fix(hook): reject bare closes\n\nCloses shk95-1/cosmai#175\n")
    assert done.returncode == 0, done.stderr


def test_bare_fixes_with_colon_is_rejected(repo: Path):
    done = run_hook(repo, "fix(hook): reject bare closes\n\nFixes: #12\n")
    assert done.returncode == 1, done.stderr


def test_merge_title_is_exempt_even_with_a_bare_closes_in_the_body(repo: Path):
    done = run_hook(repo, "Merge branch 'x'\n\nCloses #175\n")
    assert done.returncode == 0, done.stderr


def test_conventional_commits_rule_still_applies(repo: Path):
    done = run_hook(repo, "not a conventional commit\n\nissue #175\n")
    assert done.returncode == 1, done.stderr
    assert "Conventional Commit" in done.stderr


def test_subject_length_rule_still_applies(repo: Path):
    long_subject = "fix(hook): " + "가" * 65
    done = run_hook(repo, f"{long_subject}\n\nissue #175\n")
    assert done.returncode == 1, done.stderr
    assert "under 72" in done.stderr


def test_branch_issue_mention_rule_still_applies(repo: Path):
    done = run_hook(repo, "fix(hook): reject bare closes\n\nshk95-1/cosmai#175\n")
    assert done.returncode == 0, done.stderr
