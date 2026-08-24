"""What the image has to contain, asserted against the real `uv build --wheel` output.

`tool/checks/test` installs this project editable, so the working tree is always the source of
truth there and no test can see what a *wheel* leaves out. That blindness has already cost this
repo twice (#1: eval/ CSVs missing from the wheel -> FileNotFoundError; #8: three test files pytest
never collected). The wheel really does drop `contracts/ddl/needs/*.sql`, which `db/migrate.sh`
reads by repo-relative path -- so the check here is not "is it in the wheel" but "is every file
db/migrate.sh reads reachable in the image", which the image answers by carrying the checkout.

Deliberately unmarked so `tool/checks/test` runs it by default: a build-shape check that only runs
under an opt-in marker is a check that never runs.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATE_SH = REPO_ROOT / "db" / "migrate.sh"
DOCKERFILE = REPO_ROOT / "stack" / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
# pyproject's [tool.uv.build-backend] module-name: the only trees the wheel carries at all.
PACKAGED_MODULES = ("cosmai", "analysis", "collectors", "db")

_SQL_PATH = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.*-]+\.sql)")
_COPY = re.compile(r"^\s*COPY\s+(?P<rest>.+)$", re.IGNORECASE | re.MULTILINE)
_WORKDIR = re.compile(r"^\s*WORKDIR\s+(?P<dir>\S+)\s*$", re.IGNORECASE | re.MULTILINE)


# --- what db/migrate.sh needs -------------------------------------------------------------------


def migrate_inputs() -> list[PurePosixPath]:
    """Read out of the script itself, so a migration step that starts reading a new file is covered
    without anyone remembering to update a list here."""
    text = MIGRATE_SH.read_text(encoding="utf-8")
    found = {PurePosixPath(MIGRATE_SH.relative_to(REPO_ROOT).as_posix())}
    for token in sorted(set(_SQL_PATH.findall(text))):
        matches = sorted(REPO_ROOT.glob(token))
        assert matches, f"db/migrate.sh names {token} but nothing in the repo matches it"
        found.update(PurePosixPath(m.relative_to(REPO_ROOT).as_posix()) for m in matches)
    return sorted(found)


# --- what the wheel carries ---------------------------------------------------------------------


@pytest.fixture(scope="session")
def wheel_contents(tmp_path_factory: pytest.TempPathFactory) -> frozenset[str]:
    uv = shutil.which("uv")
    # Skipping would restore exactly the blindness this module exists to remove.
    assert uv, "uv is required to build the wheel this module asserts about"
    out_dir = tmp_path_factory.mktemp("wheel")
    built = subprocess.run(
        [uv, "build", "--wheel", "--offline", "--out-dir", str(out_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    wheels = sorted(out_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    with zipfile.ZipFile(wheels[0]) as zf:
        return frozenset(name for name in zf.namelist() if not name.endswith("/"))


# --- what the image carries ---------------------------------------------------------------------


def _match_segments(parts: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_match_segments(parts[i:], rest) for i in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], head) and _match_segments(parts[1:], rest)


def _pattern_hits(path: PurePosixPath, pattern: str) -> bool:
    """A .dockerignore pattern excludes a directory and everything under it, so every ancestor of
    the path counts as a hit too."""
    segments = tuple(s for s in pattern.split("/") if s not in ("", "."))
    parts = path.parts
    return any(_match_segments(parts[:i], segments) for i in range(1, len(parts) + 1))


def dockerignored(path: PurePosixPath) -> bool:
    if not DOCKERIGNORE.is_file():
        return False
    excluded = False
    for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        negated = raw.startswith("!")
        # Last matching pattern wins, which is how docker resolves a `!` re-include.
        if _pattern_hits(path, raw[1:] if negated else raw):
            excluded = not negated
    return excluded


def copied_roots() -> list[PurePosixPath]:
    """Sources of every `COPY` that reads the build context (a `--from=` COPY reads an image)."""
    roots: list[PurePosixPath] = []
    for match in _COPY.finditer(DOCKERFILE.read_text(encoding="utf-8")):
        words = match.group("rest").split()
        if any(w.startswith("--from=") for w in words):
            continue
        for src in [w for w in words if not w.startswith("--")][:-1]:
            roots.append(PurePosixPath(src.strip('"').lstrip("./") or "."))
    return roots


def in_image_checkout(path: PurePosixPath) -> bool:
    return not dockerignored(path) and any(
        root == PurePosixPath(".") or path == root or root in path.parents for root in copied_roots()
    )


# --- the assertions -----------------------------------------------------------------------------


def test_every_file_db_migrate_sh_reads_is_reachable_in_the_image(wheel_contents: frozenset[str]):
    """Either route is fine; having neither is the #10 §A-3 defect. contracts/ddl/ has exactly one
    copy in this repo on purpose, so the wheel dropping it is expected and the image answers for it
    by carrying the checkout -- that is the whole content of the (a) choice."""
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing; nothing defines the image"
    missing = [
        str(path)
        for path in migrate_inputs()
        if str(path) not in wheel_contents and not in_image_checkout(path)
    ]
    assert not missing, (
        "db/migrate.sh reads these and the image has no copy of them -- it is in neither the wheel "
        f"nor a COPY that survives .dockerignore: {missing}"
    )


def test_the_wheel_carries_every_data_file_inside_the_packaged_modules(wheel_contents: frozenset[str]):
    """The #1 shape: code under the four module roots opens its neighbours by path, and a data file
    the backend leaves out only fails once something runs from site-packages instead of the tree."""
    missing = sorted(
        str(p.relative_to(REPO_ROOT))
        for module in PACKAGED_MODULES
        for p in (REPO_ROOT / module).rglob("*")
        if p.is_file()
        and p.suffix not in (".py", ".pyc")
        and "__pycache__" not in p.parts
        and p.relative_to(REPO_ROOT).as_posix() not in wheel_contents
    )
    assert not missing, f"these sit inside a packaged module but the wheel drops them: {missing}"


def test_the_image_runs_from_the_repo_root_db_migrate_sh_assumes():
    """migrate.sh resolves contracts/ddl/needs/*.sql relative to the working directory, so the
    checkout's destination and WORKDIR have to be the same path or the deploy step cannot find it."""
    assert DOCKERFILE.is_file(), f"{DOCKERFILE} is missing; nothing defines the image"
    text = DOCKERFILE.read_text(encoding="utf-8")
    workdirs = _WORKDIR.findall(text)
    assert workdirs, "stack/Dockerfile sets no WORKDIR; db/migrate.sh only works from the repo root"
    destinations = [
        PurePosixPath(m.group("rest").split()[-1])
        for m in _COPY.finditer(text)
        if not any(w.startswith("--from=") for w in m.group("rest").split())
    ]
    assert PurePosixPath(workdirs[-1]) in destinations, (
        f"WORKDIR {workdirs[-1]} is not where the checkout is COPYed ({destinations})"
    )
