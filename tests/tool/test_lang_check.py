"""tool/checks/lang: a staged Hangul line outside the allowlist must not reach a commit.

The project operates in English (#192 D12) and the hook is the only place that rule is enforced
before the text is in history. The Korean the tests need is read from tests/tool/fixtures, which is
on the check's allowlist -- a `.py` file carrying a Hangul literal is exactly what this check exists
to stop.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK = REPO_ROOT / "tool" / "checks" / "lang"
KOREAN = (Path(__file__).resolve().parent / "fixtures" / "korean_line.txt").read_text(encoding="utf-8")

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


def test_a_staged_korean_line_fails_the_commit(repo: Path):
    stage(repo, "collectors/naver.py", f"# {KOREAN}")
    done = run_check(repo)
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert "collectors/naver.py" in done.stderr, done.stderr


def test_an_english_line_passes(repo: Path):
    stage(repo, "collectors/naver.py", "# the collector reads one page at a time\n")
    done = run_check(repo)
    assert done.returncode == 0, done.stderr


def test_an_allowlisted_path_passes(repo: Path):
    # Korean that is content rather than operating surface stays: seed rows, evaluation sets, the
    # Korean README, the portal's UI strings.
    for path in ("eval/polarity/set.csv", "db/seed/data/slice/x.csv", "README.ko.md", "portal/public/app.js"):
        stage(repo, path, KOREAN)
    done = run_check(repo)
    assert done.returncode == 0, done.stderr


def test_a_test_fixture_directory_passes(repo: Path):
    # The migration-window tests feed the tools real Korean anchors; they have to live somewhere.
    stage(repo, "tests/tool/fixtures/bodies.json", KOREAN)
    done = run_check(repo)
    assert done.returncode == 0, done.stderr


def test_the_two_migrating_tools_still_carry_their_korean_anchors(repo: Path):
    # tool/issue and tool/journal accept both anchor sets until #192 step 4 removes the Korean ones.
    for path in ("tool/issue", "tool/journal"):
        stage(repo, path, f"# {KOREAN}")
    done = run_check(repo)
    assert done.returncode == 0, done.stderr


def test_only_added_lines_count(repo: Path):
    # History is not this check's business: a commit that deletes Korean must not be blocked by the
    # Korean it deletes, or the migration itself becomes impossible to commit.
    stage(repo, "collectors/naver.py", f"# {KOREAN}")
    git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "chore: seed")
    stage(repo, "collectors/naver.py", "# the collector reads one page at a time\n")
    done = run_check(repo)
    assert done.returncode == 0, done.stderr


def test_the_check_exempts_itself(repo: Path):
    # It names the Hangul it looks for; a check that fails on its own source cannot be edited.
    stage(repo, "tool/checks/lang", f"# {KOREAN}")
    done = run_check(repo)
    assert done.returncode == 0, done.stderr
