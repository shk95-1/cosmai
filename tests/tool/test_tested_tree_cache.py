"""#214 review, Important 1: the entry must name the tree the suite actually ran against.

`tool/checks/tested-tree` captures the tree BEFORE the suite starts and refuses to record when the
checkout was dirty at that moment or when HEAD moved during the run. Both are everyday flows --
"run the suite, then commit", and a rebase onto a wave branch while the suite is running -- and
either one would otherwise stamp a tree nothing was ever tested against, which is the gate lying.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRAGMENT = REPO_ROOT / "tool" / "checks" / "tested-tree"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo with one commit, isolated from THIS checkout (#60 GIT_DIR)."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    commit(tmp_path, "one")
    return tmp_path


def commit(repo: Path, text: str) -> None:
    (repo / "file.txt").write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--no-verify", "-m", f"chore: {text}"], check=True
    )


def run(repo: Path, script: str, **env: str) -> subprocess.CompletedProcess:
    """Runs a snippet against the real fragment, in the repo, as tool/checks/test would."""
    return subprocess.run(
        ["sh", "-c", f". {FRAGMENT}\n{script}\n"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            "HOME": str(repo),
            **env,
        },
    )


def entries(repo: Path) -> list[str]:
    cache = repo / ".git" / "cosmai-tested"
    return sorted(p.name for p in cache.iterdir()) if cache.is_dir() else []


def tree_now(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_a_clean_run_records_the_tree_it_started_against(repo: Path):
    done = run(repo, "tested_tree_capture; tested_tree_record")
    assert done.returncode == 0, done.stderr
    assert entries(repo) == [tree_now(repo)]


def test_a_dirty_checkout_is_refused_and_says_so(repo: Path):
    # The everyday flow is "run the suite, then commit": if the uncommitted edits are what made it
    # green, recording HEAD's tree marks a commit green that was never tested on its own.
    (repo / "file.txt").write_text("edited but not committed", encoding="utf-8")
    done = run(repo, "tested_tree_capture; tested_tree_record")
    assert done.returncode == 0, done.stderr
    assert entries(repo) == [], "a dirty run was recorded"
    assert "uncommitted changes" in done.stdout, done.stdout


def test_an_untracked_file_also_counts_as_dirty(repo: Path):
    (repo / "scratch.txt").write_text("x", encoding="utf-8")
    done = run(repo, "tested_tree_capture; tested_tree_record")
    assert entries(repo) == [], done.stdout


def test_head_moving_during_the_run_is_refused_and_says_so(repo: Path):
    # A rebase onto wave/<channel> mid-run is exactly the case AGENTS.md says to re-test.
    before = tree_now(repo)
    script = (
        "tested_tree_capture\n"
        "echo two > file.txt\n"
        "git add -A\n"
        'git commit -q --no-verify -m "chore: two"\n'
        "tested_tree_record"
    )
    done = run(repo, script)
    assert done.returncode == 0, done.stderr
    assert entries(repo) == [], "a run whose HEAD moved was recorded"
    assert "HEAD moved" in done.stdout, done.stdout
    assert tree_now(repo) != before


def test_nothing_is_recorded_when_capture_never_ran(repo: Path):
    # cleanup calls record on every exit path; one that never reached the capture must be silent.
    done = run(repo, "tested_tree_record")
    assert done.returncode == 0, done.stderr
    assert entries(repo) == []


def test_a_forced_run_reads_no_stamp(repo: Path):
    run(repo, "tested_tree_capture; tested_tree_record")
    tree = tree_now(repo)
    plain = run(repo, f'tested_tree_stamp "{tree}"')
    assert plain.stdout.strip().startswith("20"), plain.stdout
    forced = run(repo, f'tested_tree_stamp "{tree}"', COSMAI_FORCE_SUITE="1")
    assert forced.stdout.strip() == "", forced.stdout
