"""#214: the push event stopped being the unit of verification -- a tree is.

`.githooks/pre-push` runs the suite only for a pushed commit whose tree was never green, so the
worker's own run, the coordinator's merge of the identical tree and the wave-branch push cost
nothing. The cache is written by `tool/checks/tested-tree`, and this file drives the real one from
a fake suite script, so the write and the read are checked against each other rather than
separately against a fixture.

Isolation is a throwaway repository plus PATH-free relative paths: the hook calls `tool/checks/test`
by a repository-relative path, so the fixture repo carries its own.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / ".githooks" / "pre-push"
TESTED_TREE = REPO_ROOT / "tool" / "checks" / "tested-tree"

ZERO = "0" * 40

# Records that it ran, then records the tree exactly as tool/checks/test does -- through the real
# fragment, so a change to the cache format cannot pass here and fail in the hook.
FAKE_SUITE = """#!/bin/sh
set -e
if [ -r tool/checks/tested-tree ]; then
    . tool/checks/tested-tree
    tested_tree_capture
fi
printf 'fake suite ran\\n'
printf 'ran\\n' >> "$SUITE_MARKER"
if [ "${FAKE_SUITE_FAILS:-0}" = 1 ]; then
    exit 1
fi
if [ -r tool/checks/tested-tree ]; then
    tested_tree_record
fi
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo carrying its own tool/checks, isolated from THIS checkout (#60 GIT_DIR).

    The marker the fake suite writes lives OUTSIDE the repo: the cache refuses to record a run over
    a dirty checkout, and an untracked marker would make every push here dirty.
    """
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    checks = root / "tool" / "checks"
    checks.mkdir(parents=True)
    (checks / "tested-tree").write_text(TESTED_TREE.read_text(encoding="utf-8"), encoding="utf-8")
    suite = checks / "test"
    suite.write_text(FAKE_SUITE, encoding="utf-8")
    suite.chmod(0o755)
    return root


def commit(repo: Path, text: str) -> str:
    (repo / "file.txt").write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--no-verify", "-m", f"chore: {text}"], check=True
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def tree_of(repo: Path, sha: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{sha}^{{tree}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def run_hook(repo: Path, *shas: str, fails: bool = False, force: bool = False) -> subprocess.CompletedProcess:
    stdin = "".join(f"refs/heads/main {sha} refs/heads/main {ZERO}\n" for sha in shas)
    return subprocess.run(
        ["sh", str(HOOK)],
        cwd=str(repo),
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "HOME": str(repo),
            "SUITE_MARKER": str(repo.parent / "marker"),
            "FAKE_SUITE_FAILS": "1" if fails else "0",
            "COSMAI_FORCE_SUITE": "1" if force else "0",
        },
    )


def suite_runs(repo: Path) -> int:
    marker = repo.parent / "marker"
    return len(marker.read_text(encoding="utf-8").splitlines()) if marker.exists() else 0


def test_an_untested_tree_runs_the_suite(repo: Path):
    sha = commit(repo, "one")
    done = run_hook(repo, sha)
    assert done.returncode == 0, done.stderr
    assert "untested, running the suite" in done.stdout, done.stdout
    assert suite_runs(repo) == 1


def test_a_tree_the_suite_already_proved_green_skips_the_suite(repo: Path):
    sha = commit(repo, "one")
    first = run_hook(repo, sha)
    assert suite_runs(repo) == 1, first.stdout

    second = run_hook(repo, sha)
    assert second.returncode == 0, second.stderr
    assert suite_runs(repo) == 1, "the second push re-ran a suite over an already green tree"
    assert "skipping the suite" in second.stdout, second.stdout
    assert "tested 2" in second.stdout, "the skip line must name when the tree was proved green"


def test_the_cache_entry_is_named_for_the_tree_and_holds_the_time(repo: Path):
    sha = commit(repo, "one")
    run_hook(repo, sha)
    entry = repo / ".git" / "cosmai-tested" / tree_of(repo, sha)
    assert entry.exists(), "tool/checks/tested-tree recorded nothing for a green run"
    stamp = entry.read_text(encoding="utf-8").strip()
    assert stamp.endswith("Z") and stamp.startswith("20"), stamp


def test_a_merge_commit_carrying_an_already_tested_tree_is_free(repo: Path):
    # The coordinator's merge of a worker's branch has the worker's tree and a different sha; that
    # identity is the whole point of keying the cache on the tree (#214).
    sha = commit(repo, "one")
    run_hook(repo, sha)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--no-verify", "--allow-empty", "-m", "chore: merge"],
        check=True,
    )
    other = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert other != sha
    done = run_hook(repo, other)
    assert "skipping the suite" in done.stdout, done.stdout
    assert suite_runs(repo) == 1


def test_a_commit_already_on_origin_main_skips_the_suite(repo: Path):
    # #197: what is on origin/main was verified before it got there, cache entry or not.
    old = commit(repo, "one")
    new = commit(repo, "two")
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", new], check=True)
    done = run_hook(repo, old)
    assert done.returncode == 0, done.stderr
    assert "skipping the suite" in done.stdout, done.stdout
    assert suite_runs(repo) == 0


def test_a_commit_ahead_of_origin_main_still_runs_the_suite(repo: Path):
    old = commit(repo, "one")
    new = commit(repo, "two")
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", old], check=True)
    done = run_hook(repo, new)
    assert "untested, running the suite" in done.stdout, done.stdout
    assert suite_runs(repo) == 1


def test_one_untested_ref_among_tested_ones_runs_the_suite(repo: Path):
    tested = commit(repo, "one")
    run_hook(repo, tested)
    fresh = commit(repo, "two")
    done = run_hook(repo, tested, fresh)
    assert "untested, running the suite" in done.stdout, done.stdout
    assert suite_runs(repo) == 2


def test_deletions_only_still_skip_the_suite(repo: Path):
    commit(repo, "one")
    done = run_hook(repo, ZERO)
    assert done.returncode == 0, done.stderr
    assert suite_runs(repo) == 0


def test_nothing_on_stdin_runs_the_suite(repo: Path):
    # The safe default is to test: an unexpected stdin is not evidence of a green tree.
    commit(repo, "one")
    done = run_hook(repo)
    assert done.returncode == 0, done.stderr
    assert suite_runs(repo) == 1


def test_a_failing_suite_blocks_the_push_and_records_nothing(repo: Path):
    sha = commit(repo, "one")
    done = run_hook(repo, sha, fails=True)
    assert done.returncode == 1, done.stdout
    assert "Push blocked" in done.stderr, done.stderr
    assert not (repo / ".git" / "cosmai-tested" / tree_of(repo, sha)).exists()


def test_exactly_one_decision_line_is_printed(repo: Path):
    sha = commit(repo, "one")
    done = run_hook(repo, sha)
    decisions = [line for line in done.stdout.splitlines() if line.startswith("pre-push: ")]
    assert len(decisions) == 1, done.stdout


def test_a_forced_push_runs_the_suite_over_a_cached_tree(repo: Path):
    # AGENTS.md forbids --no-verify, and the cache has already been shown to be able to hold a bad
    # entry, so there has to be one sanctioned way to make the gate re-verify a green tree.
    sha = commit(repo, "one")
    run_hook(repo, sha)
    assert suite_runs(repo) == 1

    done = run_hook(repo, sha, force=True)
    assert done.returncode == 0, done.stderr
    assert suite_runs(repo) == 2, "COSMAI_FORCE_SUITE=1 did not re-run the suite"
    assert "forced by COSMAI_FORCE_SUITE=1, running the suite" in done.stdout, done.stdout


def test_a_forced_push_runs_the_suite_over_a_commit_on_origin_main(repo: Path):
    # Forcing has to beat BOTH skips, or the ancestor arm quietly outranks the escape hatch.
    old = commit(repo, "one")
    new = commit(repo, "two")
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", new], check=True)
    done = run_hook(repo, old, force=True)
    assert suite_runs(repo) == 1, done.stdout
    assert "forced by COSMAI_FORCE_SUITE=1" in done.stdout, done.stdout


def test_the_origin_main_skip_survives_an_unreadable_cache(repo: Path):
    # R3 phrased the two skips as independent alternatives. Nested inside the cache branch, #197's
    # skip would silently disappear on any checkout whose fragment is missing.
    old = commit(repo, "one")
    new = commit(repo, "two")
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", new], check=True)
    (repo / "tool" / "checks" / "tested-tree").unlink()
    done = run_hook(repo, old)
    assert done.returncode == 0, done.stderr
    assert "skipping the suite" in done.stdout, done.stdout
    assert suite_runs(repo) == 0


def test_an_unreadable_cache_still_runs_the_suite_for_anything_else(repo: Path):
    sha = commit(repo, "one")
    (repo / "tool" / "checks" / "tested-tree").unlink()
    done = run_hook(repo, sha)
    assert "running the suite" in done.stdout, done.stdout
    assert suite_runs(repo) == 1
