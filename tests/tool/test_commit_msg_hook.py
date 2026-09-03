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


# The bare-closes check's own message identifies it; asserting on this text (rather than just
# returncode == 1) is what stops a *different* rule (branch-mention, Conventional Commits, ...)
# from silently masking a gap in this one -- exactly the trap #175 review found in the first
# version of this file (should-fix 2).
BARE_CLOSES_MARKER = "Bare closing keyword"


def test_bare_closes_is_rejected(repo: Path):
    done = run_hook(repo, "fix(hook): reject bare closes\n\nCloses #175\n")
    assert done.returncode == 1, done.stderr
    assert BARE_CLOSES_MARKER in done.stderr, done.stderr


def test_owner_repo_closes_is_accepted(repo: Path):
    done = run_hook(repo, "fix(hook): reject bare closes\n\nCloses shk95-1/cosmai#175\n")
    assert done.returncode == 0, done.stderr


def test_bare_fixes_with_colon_is_rejected(repo: Path):
    # #175 mentioned so the unrelated branch-mention rule cannot be the one that fails this --
    # without it the earlier version of this test passed for the wrong reason (review should-fix 2).
    done = run_hook(repo, "fix(hook): reject bare closes\n\nFixes: #175\n")
    assert done.returncode == 1, done.stderr
    assert BARE_CLOSES_MARKER in done.stderr, done.stderr


def test_a_bare_ref_later_in_a_comma_list_is_still_rejected(repo: Path):
    # GitHub lets one keyword govern several comma-separated refs; a bare #n anywhere in that list
    # closes the wrong issue on merge just as much as a lone one does (review should-fix 1).
    done = run_hook(repo, "fix(hook): reject bare closes\n\nCloses shk95-1/cosmai#1, #175\n")
    assert done.returncode == 1, done.stderr
    assert BARE_CLOSES_MARKER in done.stderr, done.stderr


def test_a_fully_qualified_comma_list_is_accepted(repo: Path):
    done = run_hook(repo, "fix(hook): reject bare closes\n\nCloses shk95-1/cosmai#1, shk95-1/cosmai#175\n")
    assert done.returncode == 0, done.stderr


def test_a_word_containing_close_as_a_substring_is_not_a_keyword(repo: Path):
    # #175 mentioned so this only exercises the bare-closes boundary, not the branch-mention rule
    # (round-2 regression: the awk rewrite lost the original regex's \b and matched "close" and
    # "fix" as substrings of unrelated words -- #175 re-review).
    done = run_hook(repo, "fix(hook): x\n\ndisclose #175\n")
    assert done.returncode == 0, done.stderr


def test_a_word_containing_fix_as_a_substring_is_not_a_keyword(repo: Path):
    done = run_hook(repo, "fix(hook): x\n\nprefixes #175\n")
    assert done.returncode == 0, done.stderr


def test_merge_title_is_exempt_even_with_a_bare_closes_in_the_body(repo: Path):
    done = run_hook(repo, "Merge branch 'x'\n\nCloses #175\n")
    assert done.returncode == 0, done.stderr


def test_conventional_commits_rule_still_applies(repo: Path):
    done = run_hook(repo, "not a conventional commit\n\nissue #175\n")
    assert done.returncode == 1, done.stderr
    assert "Conventional Commit" in done.stderr
    assert BARE_CLOSES_MARKER not in done.stderr, done.stderr


def test_subject_length_rule_still_applies(repo: Path):
    long_subject = "fix(hook): " + "가" * 65
    done = run_hook(repo, f"{long_subject}\n\nissue #175\n")
    assert done.returncode == 1, done.stderr
    assert "under 72" in done.stderr
    assert BARE_CLOSES_MARKER not in done.stderr, done.stderr


def test_branch_issue_mention_rule_still_applies(repo: Path):
    done = run_hook(repo, "fix(hook): reject bare closes\n\nshk95-1/cosmai#175\n")
    assert done.returncode == 0, done.stderr


def test_missing_branch_issue_mention_is_still_rejected(repo: Path):
    # No closing keyword at all here, so a pass would have to come from the branch-mention rule
    # itself, not from bare-closes leniently accepting an unrelated message.
    done = run_hook(repo, "fix(hook): unrelated change\n\nno issue mentioned here\n")
    assert done.returncode == 1, done.stderr
    assert "belongs to issue #175" in done.stderr, done.stderr
