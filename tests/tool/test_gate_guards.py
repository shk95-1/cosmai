"""#281: the static guards have to run in the gate no clone can skip, not only where it is opt-in.

`tool/checks/format`, `lint` and `lang` ran in classes N and C alone, and `paths` and `todo` ran
nowhere but `.githooks/pre-commit` -- a hook that only exists for a clone that set core.hooksPath.
CI runs `tool/checks/test` bare, which is class A, so the one gate nobody can decline was checking
none of the five. They run ahead of the class dispatch now, the shape #261 gave the unreachable
sweep.

The cases below drive the real `tool/checks/test` against a throwaway repository, with a PATH whose
`uv`, `docker` and `pg_isready` are stubs and TEST_POSTGRES_URL already set, so the script runs its
whole length -- the class dispatch included -- without a container or a pytest session. What fails a
run here is therefore a guard, and nothing else.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE = REPO_ROOT / "tool" / "checks" / "test"
KOREAN = (Path(__file__).resolve().parent / "fixtures" / "korean_line.txt").read_text(encoding="utf-8")
MACHINE_PATH = "/" + "home/someone/checkout"

# The real scripts the fixture repo carries: everything the gate sources or calls, plus the three
# guards whose own verdict a case below reads. format, lint and js are stubs instead -- they need a
# synced venv and node, and what these cases measure is whether the gate calls them at all.
CARRIED = (
    "tests/scope.toml",
    "tool/change_scope.py",
    "tool/invariants.py",
    "tool/checks/invariants",
    "tool/checks/prerequisite",
    "tool/checks/suite-lock",
    "tool/checks/harness-roles",
    "tool/checks/tested-tree",
    "tool/checks/unreachable-tests",
    "tool/checks/paths",
    "tool/checks/todo",
    "tool/checks/lang",
)
STUBS = ("js", "format", "lint")
PASSING_STUB = "#!/bin/sh\nexit 0\n"
FAILING_STUB = "#!/bin/sh\nprintf 'the stub refused this tree\\n' >&2\nexit 1\n"

# A python file the map covers, changed so tool/checks/invariants sees real movement: class B, the
# class that never ran a single one of the five (#281).
PY_BEFORE = '"""A module."""\nLIMIT = 3\n\n\ndef run(rows):\n    return rows[:LIMIT]\n'
PY_CHANGED = PY_BEFORE.replace("LIMIT = 3", "LIMIT = 4")
MAPPED = "analysis/polarity/pipeline.py"

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

# tool/checks/lang matches Hangul by codepoint, which `grep -P` can only do in a UTF-8 locale -- and
# it says so rather than passing quietly when it cannot. A real run inherits the caller's locale, so
# the fixture passes it on; C.UTF-8 is the fallback for an environment carrying none.
LOCALE = {k: v for k, v in os.environ.items() if k in ("LANG", "LC_ALL", "LC_CTYPE") and v} or {
    "LC_ALL": "C.UTF-8"
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway checkout the real gate can be run inside (#60 GIT_DIR)."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, env=CLEAN_ENV)
    for carried in CARRIED:
        target = root / carried
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / carried, target)
    gate = root / "tool" / "checks" / "test"
    shutil.copy2(GATE, gate)
    gate.chmod(0o755)
    for stub in STUBS:
        write_check(root, stub, PASSING_STUB)
    # The sweep #261 put ahead of the dispatch reads the allowlist out of this file by name, so the
    # fixture carries it the way the checkout does rather than a stand-in for it.
    write(root, "tests/tool/test_change_scope.py", "ALLOWED_UNREACHABLE = []\n")
    write(root, "README.md", "# a throwaway checkout\n")
    commit(root, "seed")
    return root


def write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def write_check(repo: Path, name: str, body: str) -> None:
    target = repo / "tool" / "checks" / name
    target.write_text(body, encoding="utf-8")
    target.chmod(0o755)


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", f"chore: {message}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def stub_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """PATH with uv, docker and pg_isready answered by stubs: the gate's `require_command` is
    satisfied, `uv sync` and `uv run pytest` do nothing, and no container is ever asked for."""
    bin_dir = tmp_path_factory.mktemp("stub-bin")
    for name in ("uv", "docker", "pg_isready"):
        stub = bin_dir / name
        stub.write_text(PASSING_STUB, encoding="utf-8")
        stub.chmod(0o755)
    return os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")])


def run_gate(repo: Path, stub_path: str, *args: str) -> subprocess.CompletedProcess:
    env = {
        **LOCALE,
        "HOME": str(repo),
        "PATH": stub_path,
        # Set, so the gate takes the branch that needs no container (its own #178 wiring).
        "TEST_POSTGRES_URL": "postgresql+psycopg://x:x@localhost:1/x",
    }
    return subprocess.run(
        ["sh", str(repo / "tool" / "checks" / "test"), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def class_b_change(repo: Path, path: str, text: str) -> str:
    """Commits a base, then a change the classifier calls B, carrying `text` at `path`."""
    write(repo, MAPPED, PY_BEFORE)
    base = commit(repo, "before")
    write(repo, MAPPED, PY_CHANGED)
    write(repo, path, text)
    commit(repo, "after")
    return base


# ---------------------------------------------------------------------------------------------
# A bare run is class A, and a bare run is what CI runs.
# ---------------------------------------------------------------------------------------------


def test_a_bare_class_a_run_is_green_when_every_guard_is(repo: Path, stub_path: str):
    done = run_gate(repo, stub_path)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_bare_class_a_run_refuses_a_tree_the_format_check_refuses(repo: Path, stub_path: str):
    write_check(repo, "format", FAILING_STUB)
    commit(repo, "formatting is off")
    done = run_gate(repo, stub_path)
    assert done.returncode != 0, done.stdout + done.stderr
    assert "→ format" in done.stdout, done.stdout


def test_a_bare_class_a_run_refuses_a_tree_the_lint_check_refuses(repo: Path, stub_path: str):
    write_check(repo, "lint", FAILING_STUB)
    commit(repo, "static analysis has something to say")
    done = run_gate(repo, stub_path)
    assert done.returncode != 0, done.stdout + done.stderr
    assert "→ lint" in done.stdout, done.stdout


def test_a_bare_class_a_run_refuses_a_machine_path_in_a_tracked_file(repo: Path, stub_path: str):
    write(repo, "db/deploy.md", f"Run {MACHINE_PATH}/db/migrate.sh\n")
    commit(repo, "a path from someone's laptop")
    done = run_gate(repo, stub_path)
    assert done.returncode != 0, done.stdout + done.stderr
    assert "db/deploy.md" in done.stderr, done.stderr


def test_a_bare_class_a_run_refuses_a_marker_that_names_no_issue(repo: Path, stub_path: str):
    write(repo, "pipeline.py", "# TO" + "DO: later\n")
    commit(repo, "a plan outside the ledger")
    done = run_gate(repo, stub_path)
    assert done.returncode != 0, done.stdout + done.stderr
    assert "pipeline.py" in done.stderr, done.stderr


# ---------------------------------------------------------------------------------------------
# Class B: the computed set, and until #281 not one static guard.
# ---------------------------------------------------------------------------------------------


def test_a_class_b_change_pays_the_static_guards_too(repo: Path, stub_path: str):
    base = class_b_change(repo, "notes.md", "nothing to see\n")
    # Left uncommitted on purpose: committed, a changed tool/checks/lint would itself be on the
    # trigger list and the run would be class A -- the one class that was never the question here.
    write_check(repo, "lint", FAILING_STUB)
    done = run_gate(repo, stub_path, "--changed", base)
    assert "class B" in done.stdout, done.stdout
    assert done.returncode != 0, done.stdout + done.stderr
    assert "→ lint" in done.stdout, done.stdout


def test_a_class_b_change_that_adds_a_korean_line_is_refused(repo: Path, stub_path: str):
    # lang is the one guard with no tree-wide form -- the allowlist keeps thousands of tracked
    # Korean lines as data -- so it reads what the change added, which needs the base only
    # `--changed` supplies (#281).
    base = class_b_change(repo, "collectors/naver/fetch.py", f"# {KOREAN}")
    done = run_gate(repo, stub_path, "--changed", base)
    assert "class B" in done.stdout, done.stdout
    assert done.returncode != 0, done.stdout + done.stderr
    assert "collectors/naver/fetch.py" in done.stderr, done.stderr


def test_a_class_b_change_in_english_is_green(repo: Path, stub_path: str):
    base = class_b_change(repo, "collectors/naver/fetch.py", "# one page at a time\n")
    done = run_gate(repo, stub_path, "--changed", base)
    assert "class B" in done.stdout, done.stdout
    assert done.returncode == 0, done.stdout + done.stderr


# ---------------------------------------------------------------------------------------------
# Where the calls sit IS the invariant, the way #261 pinned its own sweep.
# ---------------------------------------------------------------------------------------------


def gate_lines() -> list[str]:
    return GATE.read_text(encoding="utf-8").splitlines()


def dispatch_index(lines: list[str]) -> int:
    dispatch = [i for i, line in enumerate(lines) if line == 'case "$change_class" in']
    assert dispatch, "tool/checks/test no longer contains the expected anchor: the class dispatch"
    return dispatch[0]


@pytest.mark.parametrize(
    "call",
    ["tool/checks/format", "tool/checks/lint", "tool/checks/paths --tree", "tool/checks/todo --tree"],
)
def test_each_static_guard_is_called_once_and_outside_the_class_dispatch(call: str):
    lines = gate_lines()
    at = [i for i, line in enumerate(lines) if line == call]
    assert len(at) == 1, f"{call} is called {len(at)} times, not once: {at}"
    assert at[0] < dispatch_index(lines), lines[at[0] : dispatch_index(lines) + 1]


def test_lang_is_called_outside_the_dispatch_wherever_the_run_has_a_base():
    lines = gate_lines()
    at = [i for i, line in enumerate(lines) if line.strip() == 'tool/checks/lang --range "$change_base"']
    assert len(at) == 1, f"lang is called {len(at)} times, not once: {at}"
    assert at[0] < dispatch_index(lines), lines[at[0] : dispatch_index(lines) + 1]
