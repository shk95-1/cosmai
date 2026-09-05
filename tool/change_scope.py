"""What verifying this change costs: `tool/change_scope.py <base>` (#215, #231).

Three lines on stdout, for `tool/checks/test --changed <base>` to read:

    <A|B|C|N>
    <the reason, in one line>
    <the test paths to run, space separated>

A is the whole suite, B is the computed set (the mapped tests, the readers map, the import closure
and the smoke set, unioned), C is the format/lint/lang checks plus those test paths, and N is a tree
identical to the base -- format and lint, and nothing recorded.

The questions are asked in that order of authority: the gate list first (unconditional -- no proof
talks it down), a dynamic-import root next (also unconditional -- nothing traces those imports),
a joining merge next, then the trigger list (#230: A unless tool/checks/invariants proves every
changed trigger-list file moved no code), then the no-answer paths (#231 item 3), then the computed
set, and only then the cheap class for what is left -- so a path whose blast radius is the
repository can never be talked down by a guess about what its diff happens to look like (#215
review C1). Class C is not a guess either -- it is what tool/checks/invariants proved, file by
file, with every string constant compared as code.

`tool/change_scope.py --unreachable` prints, one per line, every tracked `tests/**/test_*.py` that
no map entry, reader entry, closure root or the smoke set could ever select for any change other
than the file's own (#231 Work 6; `tool/issue audit` calls this).
"""

from __future__ import annotations

import ast
import fnmatch
import subprocess
import sys
import tomllib
from pathlib import Path

FULL = "A"
PACKAGE = "B"
DOCS = "C"
NOTHING = "N"

# analysis.registry.load_implementations() imports these by name at runtime, not by any import a
# static closure can trace, so a change to either one has to cost the whole tree (#231 Work 1).
DYNAMIC_IMPORT_ROOTS = ("analysis/registry.py", "analysis/__init__.py")

# The packages a first-party import can name. `tool/` sits here for the record (option 10's list
# names it) but nothing imports `tool.*` today -- its files are scripts run by path, not modules
# (no `tool/__init__.py`), so no import ever resolves into it.
FIRST_PARTY_PACKAGES = ("analysis", "collectors", "cosmai", "db", "portal", "tool")


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


def is_none_path(path: str, none_list: list[str]) -> bool:
    """#231 item 3: a path nothing at runtime reads -- class C, no tests, comment-only or not."""
    name = path.rsplit("/", 1)[-1]
    for entry in none_list:
        if entry.endswith("/"):
            if path.startswith(entry):
                return True
        elif "." not in entry:
            # A bare name like "README" or "LICENSE" matches the file and its variants
            # (README.md, README.ko.md, LICENSE.txt), never an unrelated file that merely
            # contains the word.
            if name == entry or name.startswith(entry + "."):
                return True
        elif name == entry:
            return True
    return False


def tests_subdir_target(path: str) -> list[str] | None:
    """#231 item 3: no path under tests/ is unanswerable. tests/snapshots/ names the one test that
    reads the snapshots; tests/fixtures/ is answered by the readers map alone (a fixture nobody
    names by basename really is unmeasured); anything else under a tests/ subdirectory runs that
    whole directory, the same as a mapped package.
    """
    if not path.startswith("tests/"):
        return None
    if path.startswith("tests/snapshots/"):
        return ["tests/test_cli_help.py"]
    if path.startswith("tests/fixtures/"):
        return None
    rest = path[len("tests/") :]
    if "/" not in rest:
        return None
    top = rest.split("/", 1)[0]
    return [f"tests/{top}"]


def scope_of(path: str, entries: dict[str, list[str]]) -> tuple[str, list[str]] | None:
    """The map entry covering one changed file and its tests, or None when nothing claims it."""
    for key in sorted(entries, key=len, reverse=True):
        if path.startswith(key):
            return key, entries[key]
    return None


def all_test_files(root: Path) -> list[str]:
    """Every `tests/**/test_*.py` tracked at HEAD -- what the readers map and the closure search."""
    out = git("ls-tree", "-r", "--name-only", "HEAD", "--", "tests").split("\n")
    return sorted(p for p in out if p.rsplit("/", 1)[-1].startswith("test_") and p.endswith(".py"))


def readers_of(
    root: Path, resource: str, test_files: list[str], glob_readers: dict[str, list[str]]
) -> set[str]:
    """Every test that names `resource` by basename or by repo-relative path, plus the hand-listed
    glob readers (#231 Work 1b, 5) -- a test that discovers the file by glob/rglob/iterdir instead
    of naming it cannot be found by a text search, so those are pinned in tests/scope.toml.
    """
    found: set[str] = set()
    basename = resource.rsplit("/", 1)[-1]
    for tf in test_files:
        try:
            text = (root / tf).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if resource in text or basename in text:
            found.add(tf)
    for pattern, tests in glob_readers.items():
        if fnmatch.fnmatch(resource, pattern):
            found.update(tests)
    return found


def module_ancestors(name: str) -> list[str]:
    """Importing `a.b.c` also imports `a` and `a.b` -- a change to either package's `__init__.py`
    reaches every importer of the submodule the same way (#231 Work 1c).
    """
    parts = name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def named_first_party_modules(text: str) -> set[str]:
    """Every dotted name a module's imports could resolve to, first-party packages only. `from a.b
    import c` is ambiguous between "the submodule a.b.c" and "the name c inside module a.b" without
    resolving it against the tree, so both readings are kept -- an import edge that turns out not to
    resolve to a real file is simply dropped later, which only ever adds a false module, never
    misses one that exists (#215 review C1's bias applies here too).
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FIRST_PARTY_PACKAGES:
                    names.update(module_ancestors(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue  # no relative imports in this tree (checked); skip rather than guess
            if node.module.split(".")[0] in FIRST_PARTY_PACKAGES:
                names.update(module_ancestors(node.module))
                for alias in node.names:
                    names.update(module_ancestors(f"{node.module}.{alias.name}"))
    return names


def module_to_file(root: Path, name: str) -> str | None:
    plain = name.replace(".", "/") + ".py"
    package = name.replace(".", "/") + "/__init__.py"
    if (root / plain).is_file():
        return plain
    if (root / package).is_file():
        return package
    return None


def tracked_python_files(root: Path) -> list[str]:
    out = git("ls-tree", "-r", "--name-only", "HEAD").split("\n")
    return [p for p in out if p.endswith(".py") and p.split("/", 1)[0] in FIRST_PARTY_PACKAGES]


def build_dependents(root: Path) -> dict[str, set[str]]:
    """file -> the first-party files that import it, one hop. #231 Work 1c's closure is the
    transitive reverse of this graph, from a changed module to every test that reaches it.
    """
    dependents: dict[str, set[str]] = {}
    for path in tracked_python_files(root) + all_test_files(root):
        try:
            text = (root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for name in named_first_party_modules(text):
            target = module_to_file(root, name)
            if target and target != path:
                dependents.setdefault(target, set()).add(path)
    return dependents


def import_closure(dependents: dict[str, set[str]], changed: str, test_files: set[str]) -> set[str]:
    """Every `tests/**/test_*.py` that transitively imports the changed module (#231 Work 1c)."""
    seen: set[str] = set()
    frontier = {changed}
    while frontier:
        nxt: set[str] = set()
        for f in frontier:
            for dep in dependents.get(f, ()):
                if dep not in seen:
                    seen.add(dep)
                    nxt.add(dep)
        frontier = nxt
    return seen & test_files


def invariant_failures(root: Path, base: str, files: list[str]) -> list[str]:
    """Which of these files `tool/checks/invariants` could NOT prove moved no code, in the order
    the check itself reports them -- naming the file that actually failed, not just the first one
    changed (#231 Work 7d).
    """
    if not files:
        return []
    done = subprocess.run(
        ["sh", str(root / "tool" / "checks" / "invariants"), base, *files],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode == 0:
        return []
    failing = [f for line in done.stdout.splitlines() for f in files if line.startswith(f"{f}: ")]
    return failing or list(files)


def merge_trigger(root: Path, base: str) -> str | None:
    """A merge at HEAD whose two sides touch different top-level areas is a wave or a fork PR
    joining several channels, and earns class A the same as a trigger-list file (#231 Work 2). A
    merge whose second parent only brings in main is not: when the second parent IS the commit
    `base` resolves to, the merge is a branch catching itself up, not two channels meeting, and
    when the second parent brings in nothing new at all there is simply nothing to compare.
    """
    parents = [p for p in git("rev-parse", "HEAD^@", check=False).split("\n") if p]
    if len(parents) != 2:
        return None
    p1, p2 = parents
    base_commit = git("rev-parse", "--verify", "--quiet", base, check=False)
    if base_commit and p2 == base_commit:
        return None
    merge_base = git("merge-base", p1, p2, check=False)
    if not merge_base:
        return None
    side1 = [f for f in git("diff", "--name-only", "--no-renames", merge_base, p1).split("\n") if f]
    side2 = [f for f in git("diff", "--name-only", "--no-renames", merge_base, p2).split("\n") if f]
    if not side1 or not side2:
        return None
    keys1 = {f.split("/", 1)[0] for f in side1}
    keys2 = {f.split("/", 1)[0] for f in side2}
    if keys1 - keys2 and keys2 - keys1:
        return "a merge joins several channels"
    return None


def unreachable_tests(root: Path) -> list[str]:
    config = load(root)
    entries: dict[str, list[str]] = config["map"]  # type: ignore[assignment]
    readers: dict[str, list[str]] = config.get("readers", {})  # type: ignore[assignment]
    smoke: list[str] = config.get("smoke", [])  # type: ignore[assignment]
    test_files = all_test_files(root)

    def mark(reachable: set[str], targets: list[str]) -> None:
        for target in targets:
            if target.endswith(".py"):
                reachable.add(target)
            else:
                # A directory entry (e.g. "tests/tool") selects everything under it, the same as
                # handing it to pytest -- not just a path equal to the string itself.
                reachable.update(f for f in test_files if f.startswith(target))

    reachable: set[str] = set()
    mark(reachable, smoke)
    for tests in entries.values():
        mark(reachable, tests)
    for tests in readers.values():
        mark(reachable, tests)
    dependents = build_dependents(root)
    for deps in dependents.values():
        reachable.update(d for d in deps if is_test_file(d))
    return sorted(t for t in test_files if t not in reachable)


def classify(base: str) -> tuple[str, str, list[str]]:
    root = toplevel()
    config = load(root)
    gate: list[str] = config["gate"]  # type: ignore[assignment]
    trigger: list[str] = config["trigger"]  # type: ignore[assignment]
    docs: list[str] = config["docs"]  # type: ignore[assignment]
    smoke: list[str] = config.get("smoke", [])  # type: ignore[assignment]
    entries: dict[str, list[str]] = config["map"]  # type: ignore[assignment]
    glob_readers: dict[str, list[str]] = config.get("readers", {})  # type: ignore[assignment]
    none_list: list[str] = config.get("none", [])  # type: ignore[assignment]

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

    for path in files:
        if path in DYNAMIC_IMPORT_ROOTS:
            return FULL, f"{path} is a dynamic-import module: nothing traces that import statically", []

    joined = merge_trigger(root, base)
    if joined:
        return FULL, joined, []

    # The trigger list is next, but #230 lets tool/checks/invariants prove a changed trigger-list
    # file moved no code before it forces A -- a comment-only edit to contracts/ddl/ is not still an
    # edit to the database (#215 review C1 is why this is proof, not a guess from the diff's shape).
    trigger_files = [p for p in files if any(p.startswith(prefix) for prefix in trigger)]
    failing = invariant_failures(root, base, trigger_files)
    if failing:
        return FULL, f"{failing[0]} is on the trigger list in tests/scope.toml", []
    rest = [p for p in files if p not in trigger_files]

    # No-answer paths (#231 item 3): a real .gitignore/README/.github edit is class C, no tests --
    # nothing at runtime reads any of these, so there is nothing a test could catch either way.
    none_files = [p for p in rest if is_none_path(p, none_list)]
    rest = [p for p in rest if p not in none_files]
    if none_files and not rest:
        return DOCS, f"{len(files)} file(s): none of them are read by any test", []

    test_files_list = all_test_files(root)
    test_files = set(test_files_list)
    dependents = build_dependents(root) if any(p.endswith(".py") for p in rest) else {}
    mapped: set[str] = set()
    readers: set[str] = set()
    closure: set[str] = set()
    keys: list[str] = []
    prose_tests: set[str] = set()
    for path in rest:
        if is_test_file(path):
            if not exists_at_head(path):
                return (
                    FULL,
                    f"{path} was a test file and is gone: nothing left in the tree measures that",
                    [],
                )
            mapped.add(path)
            continue
        found = False
        covered = scope_of(path, entries)
        if covered is not None:
            key, covers = covered
            mapped.update(covers)
            keys.append(key)
            found = True
            if path.endswith(".md"):
                prose_tests.update(covers)
        found_readers = readers_of(root, path, test_files_list, glob_readers)
        if found_readers:
            readers.update(found_readers)
            found = True
        if path.endswith(".py"):
            found_closure = import_closure(dependents, path, test_files)
            if found_closure:
                closure.update(found_closure)
                found = True
        if found:
            continue
        if path.endswith(".md"):
            continue  # prose with no test behind it costs nothing to verify
        subdir_target = tests_subdir_target(path)
        if subdir_target is not None:
            mapped.update(subdir_target)
            continue
        return FULL, f"{path} maps to no entry in tests/scope.toml", []

    # Only now the cheap class, and only for what is left: Markdown, plus code that tool/checks/
    # invariants proves moved nothing -- comments and docstrings, with every string constant compared.
    code = [f for f in rest if not f.endswith(".md")]
    if not invariant_failures(root, base, code):
        return (
            DOCS,
            f"{len(files)} file(s): prose, or code tool/checks/invariants proves is unmoved",
            sorted(set(docs) | prose_tests | readers | closure),
        )
    where = ", ".join(sorted(set(keys))) or "(no mapped package)"
    tests = sorted(mapped | readers | closure | set(smoke))
    reason = (
        f"{len(files)} file(s) under {where}: "
        f"{len(mapped)} mapped · {len(readers)} readers · {len(closure)} closure · "
        f"{len(smoke)} smoke"
    )
    return PACKAGE, reason, tests


def main(argv: list[str]) -> int:
    if argv == ["--unreachable"]:
        for path in unreachable_tests(toplevel()):
            print(path)
        return 0
    if len(argv) != 1:
        print("usage: change_scope.py <base> | change_scope.py --unreachable", file=sys.stderr)
        return 2
    verdict, reason, tests = classify(argv[0])
    print(verdict)
    print(reason)
    print(" ".join(tests))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
