"""What verifying this change costs: `tool/change_scope.py <base>` (#215).

Three lines on stdout, for `tool/checks/test --changed <base>` to read:

    <A|B|C|N>
    <the reason, in one line>
    <the test paths to run, space separated>

A is the whole suite, B is those test paths plus the DB-free suite, C is the format/lint/lang checks
plus those test paths, and N is a tree identical to the base -- format and lint, and nothing recorded.
The questions are asked in that order of authority: the gate list first (unconditional -- no proof
talks it down), then the full-suite list (#230: A unless tool/checks/invariants proves every changed
full-list file moved no code), then the map, and only then the cheap class for what is left, so a
path whose blast radius is the repository can never be talked down by a guess about what its diff
happens to look like (#215 review C1). Class C is not a guess either -- it is what tool/checks/
invariants proved, file by file, with every string constant compared as code.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

FULL = "A"
PACKAGE = "B"
DOCS = "C"
NOTHING = "N"


def git(*args: str, check: bool = True) -> str:
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True).stdout.strip()


def toplevel() -> Path:
    return Path(git("rev-parse", "--show-toplevel"))


def load(root: Path) -> dict[str, object]:
    return tomllib.loads((root / "tests" / "scope.toml").read_text(encoding="utf-8"))


def is_test_file(path: str) -> bool:
    return path.startswith("tests/") and path.rsplit("/", 1)[-1].startswith("test_") and path.endswith(".py")


def exists_at_head(path: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"HEAD:{path}"], capture_output=True).returncode == 0


def scope_of(path: str, entries: dict[str, list[str]]) -> tuple[str, list[str]] | None:
    """The map entry covering one changed file and its tests, or None when nothing claims it."""
    if is_test_file(path):
        # A changed test file is its own scope. One that was deleted or renamed away is not a small
        # change at all: it takes coverage with it, and nothing left in the tree measures that.
        return (path, [path]) if exists_at_head(path) else None
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
    gate: list[str] = config["gate"]  # type: ignore[assignment]
    full: list[str] = config["full"]  # type: ignore[assignment]
    docs: list[str] = config["docs"]  # type: ignore[assignment]
    entries: dict[str, list[str]] = config["map"]  # type: ignore[assignment]

    if not git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", check=False):
        return FULL, f"the base {base} is not in this checkout, so nothing about the change is known", []

    # --no-renames so a rename arrives as a delete and an add, each judged on its own.
    files = [f for f in git("diff", "--name-only", "--no-renames", f"{base}...HEAD").split("\n") if f]
    if not files:
        return NOTHING, f"this tree is {base}'s own: there is no change to verify", []

    # The gate's own machinery decides before anything else is asked, and no proof talks it down: a
    # broken classifier that calls itself class C is the one failure nothing downstream can catch.
    for path in files:
        for prefix in gate:
            if path.startswith(prefix):
                return FULL, f"{path} is on the gate list in tests/scope.toml", []

    # The full-suite list is next, but #230 lets tool/checks/invariants prove a changed full-list file
    # moved no code before it forces A -- a comment-only edit to contracts/ddl/ or db/ is not still an
    # edit to the database (#215 review C1 is why this is proof, not a guess from the diff's shape).
    full_files = [p for p in files if any(p.startswith(prefix) for prefix in full)]
    if full_files and not invariant(root, base, full_files):
        return FULL, f"{full_files[0]} is on the full-suite list in tests/scope.toml", []
    rest = [p for p in files if p not in full_files]

    # Then the map, over every remaining file including Markdown: `contracts/ownership.md` is parsed
    # by tests/test_ownership.py, and prose a test reads is not prose to the gate (#215 review I4). A
    # full-list file already proven unmoved above needs no map entry of its own.
    tests: list[str] = []
    prose_tests: list[str] = []
    keys: list[str] = []
    for path in rest:
        covered = scope_of(path, entries)
        if covered is None:
            if path.endswith(".md"):
                continue  # prose with no test behind it costs nothing to verify
            return FULL, f"{path} maps to no entry in tests/scope.toml", []
        key, covers = covered
        tests.extend(covers)
        keys.append(key)
        if path.endswith(".md"):
            prose_tests.extend(covers)

    # Only now the cheap class, and only for what is left: Markdown, plus code that tool/checks/
    # invariants proves moved nothing -- comments and docstrings, with every string constant compared.
    code = [f for f in rest if not f.endswith(".md")]
    if invariant(root, base, code):
        return (
            DOCS,
            f"{len(files)} file(s): prose, or code tool/checks/invariants proves is unmoved",
            sorted(set(docs + prose_tests)),
        )
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
