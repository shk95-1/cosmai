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


def run_check(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(CHECK), *args], cwd=str(repo), capture_output=True, text=True, check=False, env=CLEAN_ENV
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
    # Korean README, the portal's UI strings, and the section-name ledger's old Korean names.
    for path in (
        "eval/polarity/set.csv",
        "db/seed/data/slice/x.csv",
        "README.ko.md",
        "portal/public/app.js",
        "contracts/section-names.md",
    ):
        stage(repo, path, KOREAN)
    done = run_check(repo)
    assert done.returncode == 0, done.stderr


def test_a_test_fixture_directory_passes(repo: Path):
    # The migration-window tests feed the tools real Korean anchors; they have to live somewhere.
    # Both depths: git gives `**` no empty match, so `tests/**/fixtures/` alone would miss the
    # second of these and block the commit that added it.
    stage(repo, "tests/tool/fixtures/bodies.json", KOREAN)
    stage(repo, "tests/fixtures/y.json", KOREAN)
    stage(repo, "tests/golden/out.txt", KOREAN)
    done = run_check(repo)
    assert done.returncode == 0, done.stderr


def test_the_two_migrated_tools_no_longer_get_a_korean_allowance(repo: Path):
    # #204 closes the migration window: tool/issue and tool/journal carry no Hangul allowlist entry
    # any more, so a staged Korean line in either fails like it would anywhere else.
    for path in ("tool/issue", "tool/journal"):
        stage(repo, path, f"# {KOREAN}")
    done = run_check(repo)
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert "tool/issue" in done.stderr and "tool/journal" in done.stderr, done.stderr


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


def commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", f"chore: {message}")
    done = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
    )
    return done.stdout.strip()


def test_a_range_reads_the_branch_instead_of_the_index(repo: Path):
    """#215 review I3: on a push nothing is staged, so the staged form of this check passes whatever
    the branch carries. The prose class of the push gate is exactly where that matters."""
    stage(repo, "README.md", "# English\n")
    base = commit_all(repo, "base")
    stage(repo, "collectors/naver.py", f"# {KOREAN}")
    commit_all(repo, "korean")

    staged = run_check(repo)
    assert staged.returncode == 0, "nothing is staged, so the index form has nothing to say"

    ranged = run_check(repo, "--range", base)
    assert ranged.returncode == 1, ranged.stdout + ranged.stderr
    assert "collectors/naver.py" in ranged.stderr, ranged.stderr


def test_a_range_over_an_english_branch_passes(repo: Path):
    stage(repo, "README.md", "# English\n")
    base = commit_all(repo, "base")
    stage(repo, "collectors/naver.py", "# English only\n")
    commit_all(repo, "english")
    assert run_check(repo, "--range", base).returncode == 0


def test_the_range_flag_needs_a_base(repo: Path):
    # A missing base would silently become `...HEAD`, which git reads as the whole history.
    assert run_check(repo, "--range").returncode == 2
    assert run_check(repo, "--whatever").returncode == 2
