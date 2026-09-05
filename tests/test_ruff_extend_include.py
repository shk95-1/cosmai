"""Guards against the drift #202 names: `[tool.ruff] extend-include` in pyproject.toml is a hand-kept
list of the extension-less python executables under `tool/` -- ruff's default file discovery skips
them, so a new one goes unlinted and unformatted until someone remembers to register it by hand. PR
#219 added three such tools and registered two; the third (`tool/measure-mfds-join`) was only caught
by a human reviewer in #220. This makes that gap mechanical: every python-shebang file under `tool/`
must be in the list, and every path in the list must still exist.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL_DIR = REPO_ROOT / "tool"

_PYTHON_SHEBANG = re.compile(r"^#!.*\bpython")


def _first_line(path: Path) -> str:
    with path.open(encoding="utf-8") as f:
        return f.readline()


def _python_shebang_files() -> list[str]:
    """Every file directly or nested under `tool/` whose first line is a python shebang.

    `tool/checks/` holds only `sh` scripts consumed by `tool/checks/test`'s own dispatch and is
    excluded on that basis, not by shebang -- keeping the scan agnostic to what's inside it.
    """
    found = []
    for path in sorted(TOOL_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.parent == TOOL_DIR / "checks" or TOOL_DIR / "checks" in path.parents:
            continue
        try:
            first_line = _first_line(path)
        except (UnicodeDecodeError, OSError):
            continue
        if _PYTHON_SHEBANG.match(first_line):
            found.append(str(path.relative_to(REPO_ROOT)))
    return found


def _extend_include() -> list[str]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as f:
        config = tomllib.load(f)
    return config["tool"]["ruff"]["extend-include"]


def test_every_python_shebang_tool_is_registered_in_extend_include():
    shebang_files = set(_python_shebang_files())
    registered = set(_extend_include())
    unregistered = sorted(shebang_files - registered)
    assert not unregistered, (
        "these tool/ files have a python shebang but are missing from [tool.ruff] extend-include "
        f"in pyproject.toml, so ruff's lint/format checks silently skip them: {unregistered}"
    )


def test_extend_include_has_no_stale_or_out_of_scope_entries():
    shebang_files = set(_python_shebang_files())
    registered = set(_extend_include())
    stale = sorted(registered - shebang_files)
    assert not stale, (
        "these [tool.ruff] extend-include entries no longer exist, or no longer point at a "
        f"python-shebang file under tool/: {stale}"
    )
