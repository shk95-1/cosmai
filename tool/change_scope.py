"""What verifying this change costs: `tool/change_scope.py <base>` (#215).

Three lines on stdout, for `tool/checks/test --changed <base>` to read:

    <A|B|C>
    <the reason, in one line>
    <the test paths to run, space separated>

A is the whole suite, B is those test paths plus the DB-free suite, C is the format/lint/lang checks
plus those test paths. The bias runs one way: a file the map does not cover, a file on the full-suite
list, or a base this checkout does not have is class A. Class C is never a guess -- it is what
`tool/checks/invariants` proved, file by file, about a diff that moved no code.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

FULL = "A"
PACKAGE = "B"
DOCS = "C"


def git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True).stdout.strip()


def toplevel() -> Path:
    return Path(git("rev-parse", "--show-toplevel"))


def load(root: Path) -> dict[str, object]:
    return tomllib.loads((root / "tests" / "scope.toml").read_text(encoding="utf-8"))


def is_test_file(path: str) -> bool:
    return path.startswith("tests/") and path.rsplit("/", 1)[-1].startswith("test_") and path.endswith(".py")


def scope_of(path: str, entries: dict[str, list[str]], root: Path) -> tuple[str, list[str]] | None:
    """The map entry covering one changed file and its tests, or None when nothing claims it."""
    if is_test_file(path):
        # A changed test file is its own scope; one that was deleted has no scope left to run.
        return path, ([path] if (root / path).exists() else [])
    for key in sorted(entries, key=len, reverse=True):
        if path.startswith(key):
            return key, entries[key]
    return None


def invariant(root: Path, base: str, files: list[str]) -> bool:
    """True when tool/checks/invariants proves every one of these files moved no code."""
    if not files:
        return True
    done = subprocess.run(
        ["sh", str(root / "tool" / "checks" / "invariants"), base, *files],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode == 0


def classify(base: str) -> tuple[str, str, list[str]]:
    root = toplevel()
    config = load(root)
    full: list[str] = config["full"]  # type: ignore[assignment]
    docs: list[str] = config["docs"]  # type: ignore[assignment]
    entries: dict[str, list[str]] = config["map"]  # type: ignore[assignment]

    if not git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", check=False):
        return FULL, f"the base {base} is not in this checkout, so nothing about the change is known", []

    files = [f for f in git("diff", "--name-only", f"{base}...HEAD").split("\n") if f]
    if not files:
        return DOCS, f"nothing changed against {base}", docs

    # Markdown is prose by definition; every other file has to be proved. Asked first, because a
    # translation wave touches the gate's own files and would otherwise pay for the whole suite.
    if invariant(root, base, [f for f in files if not f.endswith(".md")]):
        return DOCS, f"{len(files)} file(s): prose, or code tool/checks/invariants proves did not move", docs

    for path in files:
        for prefix in full:
            if path.startswith(prefix):
                return FULL, f"{path} is on the full-suite list in tests/scope.toml", []

    tests: list[str] = []
    keys: list[str] = []
    for path in files:
        if path.endswith(".md"):
            continue  # prose riding along with code costs nothing extra to verify
        covered = scope_of(path, entries, root)
        if covered is None:
            return FULL, f"{path} maps to no entry in tests/scope.toml", []
        key, covers = covered
        tests.extend(covers)
        keys.append(key)
    where = ", ".join(sorted(set(keys)))
    return PACKAGE, f"{len(files)} file(s) under {where}", sorted(set(tests))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: change_scope.py <base>", file=sys.stderr)
        return 2
    verdict, reason, tests = classify(argv[0])
    print(verdict)
    print(reason)
    print(" ".join(tests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
