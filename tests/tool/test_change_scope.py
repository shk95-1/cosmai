"""#215: what a change costs to verify is decided here, so this is where it has to be wrong loudly.

`tool/change_scope.py <base>` reads `tests/scope.toml` and answers with three lines -- the class, the
reason, and the test paths to run. Class A is the whole suite, class B is the mapped tests plus the
DB-free suite, class C is the format/lint/lang checks plus the snapshot tests. The bias is one way
only: anything the map does not cover, and anything that touches the database, the packaging or the
gate itself, is class A. A wrong class C is a change that reached main unverified, so every rule
below is a rule about what must NOT be allowed to look small.

Fixture repositories carrying the real scope.toml and the real invariant check: the classifier reads
git history, and this checkout's history is not a fixture anybody can pin.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER = REPO_ROOT / "tool" / "change_scope.py"
CARRIED = (
    "tests/scope.toml",
    "tool/invariants.py",
    "tool/checks/invariants",
    "tool/checks/prerequisite",
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repo carrying the real map and the real invariant check (#60 GIT_DIR)."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    for path in CARRIED:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / path, target)
    return root


def write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--no-verify", "-m", f"chore: {message}"], check=True
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


class Scope:
    def __init__(self, done: subprocess.CompletedProcess[str]) -> None:
        self.raw = done.stdout
        self.stderr = done.stderr
        self.code = done.returncode
        lines = done.stdout.splitlines()
        self.klass = lines[0] if lines else ""
        self.reason = lines[1] if len(lines) > 1 else ""
        self.tests = lines[2].split() if len(lines) > 2 else []


def classify(repo: Path, base: str) -> Scope:
    done = subprocess.run(
        [sys.executable, str(CLASSIFIER), base],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )
    return Scope(done)


def change(repo: Path, path: str, before: str, after: str) -> Scope:
    """Commits `before`, replaces it with `after`, and asks what verifying that costs."""
    write(repo, path, before)
    base = commit(repo, "before")
    write(repo, path, after)
    commit(repo, "after")
    return classify(repo, base)


PY_BEFORE = '"""A module."""\nLIMIT = 3\n\n\ndef run(rows):\n    # the old wording\n    return rows[:LIMIT]\n'
PY_RETOLD = '"""Said better."""\nLIMIT = 3\n\n\ndef run(rows):\n    # a better why\n    return rows[:LIMIT]\n'
PY_CHANGED = PY_BEFORE.replace("LIMIT = 3", "LIMIT = 4")


def test_a_ddl_change_is_class_a(repo: Path):
    scope = change(
        repo, "contracts/ddl/needs/030_x.sql", "CREATE TABLE a (id int);\n", "CREATE TABLE a (id bigint);\n"
    )
    assert scope.klass == "A", scope.raw
    assert "contracts/ddl/" in scope.reason, scope.reason


def test_the_lockfile_and_the_project_file_are_class_a(repo: Path):
    assert change(repo, "uv.lock", "a = 1\n", "a = 2\n").klass == "A"
    assert change(repo, "pyproject.toml", "[project]\n", "[project]\nx = 1\n").klass == "A"


def test_the_gate_deciding_its_own_change_is_small_is_refused(repo: Path):
    # The one classification nobody else can catch: a broken classifier that calls itself class C.
    scope = change(repo, "tool/change_scope.py", "x = 1\n", "x = 2\n")
    assert scope.klass == "A", scope.raw


def test_a_markdown_only_change_is_class_c(repo: Path):
    scope = change(repo, "docs.md", "# One\n\nSee §2 and #214.\n", "# One, retold\n\nSee §2 and #214.\n")
    assert scope.klass == "C", scope.raw
    assert "tests/test_cli_help.py" in scope.tests, scope.tests


def test_a_python_file_whose_code_did_not_move_is_class_c(repo: Path):
    scope = change(repo, "analysis/polarity/pipeline.py", PY_BEFORE, PY_RETOLD)
    assert scope.klass == "C", scope.raw
    assert "invariant" in scope.reason.lower(), scope.reason


def test_the_same_file_with_one_number_changed_is_class_b(repo: Path):
    scope = change(repo, "analysis/polarity/pipeline.py", PY_BEFORE, PY_CHANGED)
    assert scope.klass == "B", scope.raw
    assert "tests/test_polarity.py" in scope.tests, scope.tests
    assert "tests/test_linker.py" not in scope.tests, "class B ran another package's tests"


def test_the_longest_matching_prefix_wins(repo: Path):
    scope = change(repo, "analysis/linker/rules.py", PY_BEFORE, PY_CHANGED)
    assert scope.klass == "B", scope.raw
    assert "tests/test_linker.py" in scope.tests, scope.tests
    assert "tests/test_polarity.py" not in scope.tests, scope.tests


def test_a_change_in_two_packages_runs_both_sets(repo: Path):
    write(repo, "analysis/linker/rules.py", PY_BEFORE)
    write(repo, "analysis/trend/rules.py", PY_BEFORE)
    base = commit(repo, "before")
    write(repo, "analysis/linker/rules.py", PY_CHANGED)
    write(repo, "analysis/trend/rules.py", PY_CHANGED)
    commit(repo, "after")
    scope = classify(repo, base)
    assert scope.klass == "B", scope.raw
    assert "tests/test_linker.py" in scope.tests and "tests/test_trend_pipeline.py" in scope.tests, (
        scope.tests
    )


def test_a_changed_test_file_is_its_own_scope(repo: Path):
    scope = change(
        repo, "tests/test_linker.py", "def test_a():\n    assert 1\n", "def test_a():\n    assert 2\n"
    )
    assert scope.klass == "B", scope.raw
    assert scope.tests == ["tests/test_linker.py"], scope.tests


def test_a_changed_conftest_is_class_a(repo: Path):
    # Every test in the suite runs through it, so its blast radius is the suite.
    scope = change(repo, "tests/conftest.py", "x = 1\n", "x = 2\n")
    assert scope.klass == "A", scope.raw


def test_a_fixture_under_tests_is_not_a_test_and_falls_to_class_a(repo: Path):
    scope = change(repo, "tests/fixtures/rows.json", '{"a": 1}\n', '{"a": 2}\n')
    assert scope.klass == "A", scope.raw


def test_an_unmapped_file_is_class_a(repo: Path):
    # An unmapped file is not a small change, it is an unmeasured one.
    scope = change(repo, "newthing/main.py", PY_BEFORE, PY_CHANGED)
    assert scope.klass == "A", scope.raw
    assert "newthing/main.py" in scope.reason, scope.reason


def test_a_prose_change_next_to_a_code_change_costs_the_code_change(repo: Path):
    write(repo, "docs.md", "# One\n")
    write(repo, "analysis/linker/rules.py", PY_BEFORE)
    base = commit(repo, "before")
    write(repo, "docs.md", "# Two\n")
    write(repo, "analysis/linker/rules.py", PY_CHANGED)
    commit(repo, "after")
    scope = classify(repo, base)
    assert scope.klass == "B", scope.raw


def test_a_branch_with_nothing_on_it_is_class_c(repo: Path):
    write(repo, "a.py", PY_BEFORE)
    base = commit(repo, "one")
    scope = classify(repo, base)
    assert scope.klass == "C", scope.raw


def test_a_base_that_does_not_exist_is_class_a(repo: Path):
    # A fresh clone has no origin/main; guessing small on no information is how a gate stops being one.
    write(repo, "a.py", PY_BEFORE)
    commit(repo, "one")
    scope = classify(repo, "origin/nowhere")
    assert scope.klass == "A", scope.raw
    assert "origin/nowhere" in scope.reason, scope.reason


def test_the_class_and_the_reason_are_the_first_two_lines(repo: Path):
    scope = change(repo, "docs.md", "# One\n", "# Two\n")
    assert scope.code == 0, scope.stderr
    assert scope.klass in {"A", "B", "C"}, scope.raw
    assert scope.reason, "the class without a reason is a verdict nobody can check"
