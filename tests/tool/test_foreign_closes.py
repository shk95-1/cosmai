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


def _short(sha: str) -> str:
    return sha[:7]


@pytest.fixture
def pr_repo(gitrepo: Path):
    """A `pr` branch ahead of HEAD exercising every closing form and every non-hit form.

    Returns (repo path, {label: short sha}) so tests can build the exact expected output line
    instead of guessing commit order.
    """
    git("checkout", "-qb", "pr", cwd=gitrepo)
    shas: dict[str, str] = {}

    def commit(label: str, content: str, message: str) -> None:
        (gitrepo / "b.txt").write_text(content, encoding="utf-8")
        git("add", "-A", cwd=gitrepo)
        git("commit", "-qm", message, cwd=gitrepo)
        shas[label] = _short(git("rev-parse", "HEAD", cwd=gitrepo).stdout.strip())

    # Adjacency, not line-anchoring: keyword immediately followed by the ref is the directive,
    # whatever comes before or after it on the line (#190 review, important 1).
    commit("bare_18", "1\n", "fix: bare close\n\nCloses #18")
    commit("period_18", "2\n", "test: second closer\n\nCloses #18.")
    commit("qualified_40", "3\n", "fix: qualified close\n\nCloses other/repo#40")
    commit("mention_56", "4\n", "chore: mentions #56 with no keyword")
    # A Conventional Commits type prefix ("fix:") starts with the same word as the closing
    # keyword; the "(" right after it breaks the required ":?[ \t]+" before a ref, so this is
    # prose, not "Fixes #40" (found live against PR #59's d8354de).
    commit("prefix_40", "5\n", "fix(analysis): #40 passed a stale value, not a closing keyword")
    commit("midline_108", "6\n", "fix(tool): closes #108")
    commit("prose_5", "7\n", "docs: notes\n\nThis closes #5 for good.")
    commit("paren_77", "8\n", "chore: parenthetical\n\nCloses #77 (wave)")

    git("checkout", "-q", "main", cwd=gitrepo)
    return gitrepo, shas


@pytest.fixture
def run_predict(tmp_path: Path, pr_repo):
    repo, shas = pr_repo
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)

    def _run(open_issues: list[dict], ref: str = "pr"):
        (tmp_path / "open-issues.json").write_text(json.dumps(open_issues), encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FIXTURES": str(tmp_path),
        }
        return subprocess.run(
            [str(SCRIPT), REPO, "--predict", ref],
            capture_output=True,
            text=True,
            cwd=str(repo),
            env=env,
            check=False,
        )

    _run.shas = shas  # type: ignore[attr-defined]
    return _run


def test_a_bare_close_on_an_open_issue_is_reported(run_predict):
    done = run_predict([{"number": 18, "title": "ydc import #18"}])
    assert done.returncode == 1, done.stderr
    # exact line, not a substring match (the title alone contains "#18") -- pins the two-commit
    # `closing commits a1b2c3d, d4e5f6a` format too (#190 review, minor 4 and 5). `period_18` was
    # committed after `bare_18`, so it is the newer commit and `git log` yields it first.
    shas = run_predict.shas
    expected = f"#18 · ydc import #18 · closing commits {shas['period_18']}, {shas['bare_18']}\n"
    assert done.stdout == expected, done.stdout


def test_a_qualified_owner_repo_ref_is_never_a_hit(run_predict):
    # "Closes other/repo#40" is correct for the repo it targets, and "fix(analysis): #40 ..." is
    # prose (no keyword adjacency); neither is a bare close on THIS repo's #40.
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
    # "fix(analysis): #40 ..." is prose that happens to start with the word "fix"; only a keyword
    # immediately followed by a ref ("Fixes #40") is a real closing directive.
    done = run_predict([{"number": 40, "title": "prefix false positive"}])
    assert done.returncode == 0, done.stdout
    assert done.stdout == ""


@pytest.mark.parametrize(
    ("label", "number", "title"),
    [
        # A keyword mid-line, not at the start of the message ("fix(tool): closes #108").
        ("midline_108", 108, "keyword mid-line"),
        # A keyword inside a sentence, with trailing prose after the ref.
        ("prose_5", 5, "keyword in a sentence"),
        # A trailing parenthetical right after the ref number.
        ("paren_77", 77, "trailing parenthetical"),
    ],
)
def test_closing_forms_github_honours_are_all_caught(run_predict, label, number, title):
    # #190 review, important 1: a whole-line anchor previously silenced every one of these.
    done = run_predict([{"number": number, "title": title}])
    assert done.returncode == 1, done.stderr
    assert f"#{number} · {title} · closing commits {run_predict.shas[label]}\n" == done.stdout


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


def test_an_unresolvable_ref_exits_2_not_0(run_predict):
    # git log fails loudly on stderr for a typo'd or unfetched ref; that must not read as "no
    # commits in range, nothing found" (#190 review, important 2) -- a pre-merge gate wired as
    # `if foreign-closes ...; then merge` would otherwise treat a bad ref as a pass.
    done = run_predict([{"number": 18, "title": "ydc import #18"}], ref="origin/no-such-branch")
    assert done.returncode == 2, done.stdout
    assert done.stdout == ""
    assert done.stderr != ""
