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
    *issues?state=open*) cat "$FIXTURES/open-issues.json" || exit 1; exit 0 ;;
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

    def _run(
        *,
        issues: list[dict] | None = None,
        issues_raw: str | None = None,
        timelines: dict[int, list[dict]],
    ):
        # issues_raw lets a test hand the fake gh two concatenated JSON arrays, the way real
        # `gh api --paginate` concatenates one page's array after another on stdout.
        body = issues_raw if issues_raw is not None else json.dumps(issues)
        (tmp_path / "issues.json").write_text(body, encoding="utf-8")
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
    # R3: the one existing Korean output line is English now (D10).
    assert "closed by" in done.stdout
    assert "닫은 커밋" not in done.stdout


def test_an_issue_closed_by_our_own_commit_is_not_reported(run, gitrepo: Path):
    own_commit = git("rev-parse", "main", cwd=gitrepo).stdout.strip()
    done = run(
        issues=[{"number": 40, "title": "우리 커밋이 닫음"}],
        timelines={40: [{"event": "closed", "commit_id": own_commit}]},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == ""


def test_a_second_page_of_closed_issues_is_read(run, gitrepo: Path):
    # `gh api --paginate` concatenates each page's array back to back on stdout; a script that
    # reads only the first page would silently miss whatever a repo's second page holds
    # (#175 review, should-fix 3).
    foreign_commit = git("rev-parse", "main~1", cwd=gitrepo).stdout.strip()
    page1 = json.dumps([{"number": 38, "title": "1페이지"}])
    page2 = json.dumps([{"number": 58, "title": "2페이지"}])
    done = run(
        issues_raw=page1 + page2,
        timelines={
            38: [{"event": "closed", "commit_id": foreign_commit}],
            58: [{"event": "closed", "commit_id": foreign_commit}],
        },
    )
    assert done.returncode == 1, done.stderr
    assert "#38" in done.stdout and "#58" in done.stdout, done.stdout


def test_malformed_issues_response_exits_2(run, gitrepo: Path):
    # gh exited 0 but the body isn't the expected array shape; jq's failure must not be swallowed
    # into a silent, wrong "exit 0: nothing found" (#175 review, minor 5).
    done = run(issues_raw="not json", timelines={})
    assert done.returncode == 2, done.stdout
    assert done.stderr != ""


def test_an_unreachable_commit_warns_but_does_not_crash(run, gitrepo: Path):
    # merge-base --is-ancestor exits 128 when the commit isn't known locally at all (never
    # fetched) -- different from exit 1 ("known, just not an ancestor"); it must not be silently
    # folded into "clean" (#175 review, minor 4).
    done = run(
        issues=[{"number": 99, "title": "안 당겨온 커밋"}],
        timelines={99: [{"event": "closed", "commit_id": "0" * 40}]},
    )
    assert done.returncode == 0, done.stdout
    assert done.stdout == ""
    assert "not found locally" in done.stderr, done.stderr


def test_gh_failure_exits_2(run, gitrepo: Path, tmp_path: Path):
    # issues.json was never written, so the fake gh's `cat` fails and gh exits non-zero.
    bin_dir = tmp_path / "bin"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "FIXTURES": str(tmp_path)}
    done = subprocess.run(
        [str(SCRIPT), REPO, "remote"], capture_output=True, text=True, cwd=str(gitrepo), env=env
    )
    assert done.returncode == 2, done.stdout
    assert done.stderr != ""


# #190: --predict answers "which OPEN issues of this repo would this merge close" before the
# merge happens -- the same collision as #175, caught pre-merge instead of found as damage after.


@pytest.fixture
def pr_repo(gitrepo: Path) -> Path:
    """A `pr` branch ahead of HEAD carrying bare, qualified, and keyword-less `#n` refs."""
    git("checkout", "-qb", "pr", cwd=gitrepo)
    (gitrepo / "b.txt").write_text("1\n", encoding="utf-8")
    git("add", "-A", cwd=gitrepo)
    git("commit", "-qm", "fix: bare close\n\nCloses #18", cwd=gitrepo)
    (gitrepo / "b.txt").write_text("2\n", encoding="utf-8")
    git("add", "-A", cwd=gitrepo)
    git("commit", "-qm", "fix: qualified close\n\nCloses other/repo#40", cwd=gitrepo)
    (gitrepo / "b.txt").write_text("3\n", encoding="utf-8")
    git("add", "-A", cwd=gitrepo)
    git("commit", "-qm", "chore: mentions #56 with no keyword", cwd=gitrepo)
    # A Conventional Commits type prefix ("fix:") starts with the same word as the closing
    # keyword; a subject that merely mentions an issue after it is not a closing directive
    # (found live against PR #59's d8354de: "fix(analysis): #40 이 넘긴 ..." false-positived #40).
    (gitrepo / "b.txt").write_text("4\n", encoding="utf-8")
    git("add", "-A", cwd=gitrepo)
    git("commit", "-qm", "fix(analysis): #77 passed a stale value, not a closing keyword", cwd=gitrepo)
    git("checkout", "-q", "main", cwd=gitrepo)
    return gitrepo


@pytest.fixture
def run_predict(tmp_path: Path, pr_repo: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)

    def _run(open_issues: list[dict]):
        (tmp_path / "open-issues.json").write_text(json.dumps(open_issues), encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FIXTURES": str(tmp_path),
        }
        return subprocess.run(
            [str(SCRIPT), REPO, "--predict", "pr"],
            capture_output=True,
            text=True,
            cwd=str(pr_repo),
            env=env,
            check=False,
        )

    return _run


def test_a_bare_close_on_an_open_issue_is_reported(run_predict):
    done = run_predict([{"number": 18, "title": "ydc import #18"}])
    assert done.returncode == 1, done.stderr
    assert "#18" in done.stdout
    assert "ydc import #18" in done.stdout
    assert "closing commits" in done.stdout


def test_a_qualified_owner_repo_ref_is_never_a_hit(run_predict):
    # "Closes other/repo#40" is correct for the repo it targets; it must not be mistaken for a
    # bare close on THIS repo's #40.
    done = run_predict([{"number": 40, "title": "not ours to close"}])
    assert done.returncode == 0, done.stdout
    assert done.stdout == ""


def test_a_keyword_less_reference_is_never_a_hit(run_predict):
    # "mentions #56" carries no closing keyword, so it never closes anything.
    done = run_predict([{"number": 56, "title": "just mentioned"}])
    assert done.returncode == 0, done.stdout
    assert done.stdout == ""


def test_an_open_issue_with_no_matching_commit_stays_silent(run_predict):
    done = run_predict([{"number": 999, "title": "unrelated"}])
    assert done.returncode == 0, done.stdout
    assert done.stdout == ""


def test_a_conventional_commit_type_prefix_is_not_a_closing_keyword(run_predict):
    # "fix(analysis): #77 ..." is prose that happens to start with the word "fix"; only a line
    # that is nothing but the keyword and a ref list ("Fixes #77") is a real closing directive.
    done = run_predict([{"number": 77, "title": "prefix false positive"}])
    assert done.returncode == 0, done.stdout
    assert done.stdout == ""


def test_only_the_overlapping_number_is_printed_among_several_open_issues(run_predict):
    done = run_predict(
        [
            {"number": 18, "title": "overlap"},
            {"number": 40, "title": "qualified, not a hit"},
            {"number": 999, "title": "no matching commit"},
        ]
    )
    assert done.returncode == 1, done.stderr
    assert "#18" in done.stdout
    assert "#40" not in done.stdout
    assert "#999" not in done.stdout
