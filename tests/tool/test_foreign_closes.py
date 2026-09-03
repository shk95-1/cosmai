"""#175: after a fork merges upstream, a bare `Closes #n` in an upstream commit can close an
unrelated fork issue with the same number. `tool/checks/foreign-closes` finds issues in THIS repo
whose closing commit is an ancestor of <remote>/main -- i.e. closed by someone else's commit.

Isolation follows the fake-`gh`-on-PATH pattern from test_issue_tool.py: a subprocess is outside
conftest's in-process socket guard.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tool" / "checks" / "foreign-closes"
REPO = "example/fork"

FAKE_GH = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    *issues?state=closed*) cat "$FIXTURES/issues.json" || exit 1; exit 0 ;;
    *issues/*/timeline) num=$(printf '%s' "$arg" | sed -nE 's#.*issues/([0-9]+)/timeline#\\1#p')
                         cat "$FIXTURES/timeline-$num.json" 2>/dev/null || echo '[]'
                         exit 0 ;;
  esac
done
echo "fake gh: no fixture for: $*" >&2
exit 1
"""


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=clean,
    )


@pytest.fixture
def gitrepo(tmp_path: Path) -> Path:
    """A repo with a `remote/main` branch and both an ancestor and a non-ancestor commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", "-b", "main", ".", cwd=repo)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "chore: seed", cwd=repo)
    (repo / "a.txt").write_text("b\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "feat: upstream change", cwd=repo)
    git("branch", "remote/main", cwd=repo)  # remote/main includes the foreign commit
    (repo / "a.txt").write_text("c\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "fix: local change", cwd=repo)
    return repo


@pytest.fixture
def run(tmp_path: Path, gitrepo: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)

    def _run(*, issues: list[dict], timelines: dict[int, list[dict]]):
        (tmp_path / "issues.json").write_text(json.dumps(issues), encoding="utf-8")
        for n, events in timelines.items():
            (tmp_path / f"timeline-{n}.json").write_text(json.dumps(events), encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FIXTURES": str(tmp_path),
        }
        return subprocess.run(
            [str(SCRIPT), REPO, "remote"],
            capture_output=True,
            text=True,
            cwd=str(gitrepo),
            env=env,
            check=False,
        )

    return _run


def test_an_issue_closed_by_a_foreign_commit_is_reported(run, gitrepo: Path):
    foreign_commit = git("rev-parse", "main~1", cwd=gitrepo).stdout.strip()
    done = run(
        issues=[{"number": 38, "title": "패널 43채널 재수집"}],
        timelines={38: [{"event": "closed", "commit_id": foreign_commit}]},
    )
    assert done.returncode == 1, done.stderr
    assert "#38" in done.stdout
    assert "패널 43채널 재수집" in done.stdout
    assert foreign_commit[:7] in done.stdout


def test_an_issue_closed_by_our_own_commit_is_not_reported(run, gitrepo: Path):
    own_commit = git("rev-parse", "main", cwd=gitrepo).stdout.strip()
    done = run(
        issues=[{"number": 40, "title": "우리 커밋이 닫음"}],
        timelines={40: [{"event": "closed", "commit_id": own_commit}]},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""


def test_gh_failure_exits_2(run, gitrepo: Path, tmp_path: Path):
    # issues.json was never written, so the fake gh's `cat` fails and gh exits non-zero.
    bin_dir = tmp_path / "bin"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FIXTURES": str(tmp_path)}
    done = subprocess.run(
        [str(SCRIPT), REPO, "remote"], capture_output=True, text=True, cwd=str(gitrepo), env=env
    )
    assert done.returncode == 2, done.stdout
    assert done.stderr != ""
