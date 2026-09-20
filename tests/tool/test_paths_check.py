"""tool/checks/paths: a machine path must not reach a public repository (fork #9, #281).

The check had exactly one caller, the pre-commit hook, and a clone that never set core.hooksPath has
no hook at all -- so a machine path could reach main through any such clone. `--tree` is the form the
push gate runs in every change class: CI runs that gate bare, where there is no index to read and no
base to diff against, only the tracked files (#281).

The machine path below is assembled rather than written out: this file is tracked, and a literal one
is the very thing both forms of the check exist to reject.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK = REPO_ROOT / "tool" / "checks" / "paths"
MACHINE_PATH = "/" + "home/someone/checkout"

# Hooks export GIT_DIR; left in place these commands would act on the enclosing checkout (#60 trap).
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)], check=True, capture_output=True, env=CLEAN_ENV
    )
    return tmp_path


def stage(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", path], check=True, capture_output=True, env=CLEAN_ENV
    )


def run_check(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(CHECK), *args], cwd=str(repo), capture_output=True, text=True, check=False, env=CLEAN_ENV
    )


def test_a_staged_machine_path_fails_the_commit(repo: Path):
    stage(repo, "db/deploy.md", f"Run {MACHINE_PATH}/db/migrate.sh\n")
    done = run_check(repo)
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)


def test_a_staged_repository_relative_path_passes(repo: Path):
    stage(repo, "db/deploy.md", "Run db/migrate.sh\n")
    done = run_check(repo)
    assert done.returncode == 0, done.stderr


def test_a_tracked_machine_path_fails_the_tree_form(repo: Path):
    # The case the gate meets: nothing is staged and there is no base, and the line is in the tree
    # already because the clone that committed it ran no hook.
    stage(repo, "db/deploy.md", f"Run {MACHINE_PATH}/db/migrate.sh\n")
    done = run_check(repo, "--tree")
    assert done.returncode == 1, (done.returncode, done.stdout, done.stderr)
    assert "db/deploy.md" in done.stderr, done.stderr


def test_a_clean_tree_passes_the_tree_form(repo: Path):
    stage(repo, "db/deploy.md", "Run db/migrate.sh\n")
    done = run_check(repo, "--tree")
    assert done.returncode == 0, done.stderr


def test_captured_vendor_html_under_fixtures_is_not_a_machine_path(repo: Path):
    # Measured on this tree (#281): the only tracked lines the tree form would have flagged are CDN
    # URLs inside two captured glowpick pages, whose first path segment happens to be the one a home
    # directory starts with. The staged form keeps no such exclusion, so a real machine path added
    # to a fixture still fails at the commit.
    page = f'<img src="https://cdn.example.net{MACHINE_PATH}/x.jpg">\n'
    stage(repo, "tests/collectors/commerce/fixtures/site/page.html", page)
    done = run_check(repo, "--tree")
    assert done.returncode == 0, done.stderr


def test_an_unknown_argument_is_refused(repo: Path):
    done = run_check(repo, "--everything")
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
