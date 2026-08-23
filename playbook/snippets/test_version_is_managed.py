"""origin: service/trend-radar/tests/test_version_is_managed.py:34-83 (AST hardcoding check only)
reuse: pyproject is the one source; nothing under src/ assigns __version__/VERSION. Parsed, not grepped — docstrings mention versions.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"__version__", "VERSION", "version"}


def _declared() -> str:
    with (ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def test_the_package_reports_the_declared_version():
    from importlib.metadata import version

    assert version("cosmai") == _declared()


def test_nothing_hardcodes_the_version_a_second_time():
    modules = sorted((ROOT / "src").rglob("*.py"))
    assert len(modules) > 10, f"only {len(modules)} modules under src/ -- is ROOT right?"
    hardcoded = []
    for path in modules:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            for t in targets:
                if isinstance(t, ast.Name) and t.id in NAMES and isinstance(getattr(node, "value", None), ast.Constant):
                    hardcoded.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not hardcoded, f"a second copy of the version: {hardcoded}"
