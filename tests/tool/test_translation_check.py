"""tool/checks/translation <base>: the translation reviewer's mechanical checks, one exit status (#234).

Every 2026-09-04 translation wave's first review round re-derived the same three checks by hand --
did any code move, does every `§` anchor resolve, did a comment get dropped instead of carried across.
This script is those checks, kept, so a reviewer's first round starts from its report instead of
re-running them.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK = REPO_ROOT / "tool" / "checks" / "translation"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "translation_check"

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repo, isolated from THIS checkout (#60 GIT_DIR)."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, env=CLEAN_ENV)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "t@example.com"], check=True, env=CLEAN_ENV
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True, env=CLEAN_ENV)
    return root


def write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=CLEAN_ENV)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--no-verify", "-m", f"chore: {message}"],
        check=True,
        env=CLEAN_ENV,
    )
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
    ).stdout.strip()


def translation(repo: Path, base: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(CHECK), base],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
    )


def base_tree(repo: Path) -> str:
    """A minimal contracts/ tree the fixtures build a translation wave against."""
    write(repo, "contracts/section-names.md", fixture("section_names.md"))
    write(
        repo,
        "contracts/interfaces.md",
        "# Interfaces\n\n## Ingredients (fork #6)\n\nSee §Ingredients for the rule.\n",
    )
    write(repo, "tool/greet.py", fixture("greet_before.py"))
    write(repo, "db/note.sql", fixture("note_before.sql"))
    return commit(repo, "base tree")


def test_exit_0_on_comment_only_translation_with_resolvable_anchor(repo: Path) -> None:
    base_tree(repo)
    write(
        repo,
        "tool/greet.py",
        '"""Returns the greeting."""\n\n\ndef greet() -> str:\n'
        '    return "hello"  # the contract\'s §Ingredients\n',
    )
    write(repo, "db/note.sql", "-- the rule\nSELECT 1;\n")
    commit(repo, "translate comments")

    result = translation(repo, "HEAD~1")
    assert result.returncode == 0, result.stdout + result.stderr


def test_exit_1_on_invented_anchor(repo: Path) -> None:
    base_tree(repo)
    write(
        repo,
        "contracts/interfaces.md",
        "# Interfaces\n\n## Ingredients (fork #6)\n\nSee §NoSuchSection for the rule.\n",
    )
    commit(repo, "invent an anchor")

    result = translation(repo, "HEAD~1")
    assert result.returncode == 1
    assert "interfaces.md" in result.stdout


def test_exit_1_on_moved_string_literal(repo: Path) -> None:
    base_tree(repo)
    write(repo, "tool/greet.py", fixture("greet_reworded.py"))
    commit(repo, "quietly reword the return value")

    result = translation(repo, "HEAD~1")
    assert result.returncode == 1
    assert "greet.py" in result.stdout


def test_exit_1_on_dropped_comment_block(repo: Path) -> None:
    """Dropped = a comment line removed with no comment line added in the same hunk."""
    base_tree(repo)
    write(repo, "db/note.sql", "SELECT 1;\n")
    commit(repo, "drop the comment instead of translating it")

    result = translation(repo, "HEAD~1")
    assert result.returncode == 1
    assert "note.sql" in result.stdout


def test_report_names_the_failing_file_and_line(repo: Path) -> None:
    base_tree(repo)
    write(
        repo,
        "contracts/interfaces.md",
        "# Interfaces\n\n## Ingredients (fork #6)\n\nSee §NoSuchSection for the rule.\n",
    )
    commit(repo, "invent an anchor")

    result = translation(repo, "HEAD~1")
    assert "contracts/interfaces.md:5" in result.stdout
