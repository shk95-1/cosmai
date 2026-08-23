"""origin: service/trend-radar/tests/test_sources_stay_at_their_layer.py + Research_Paper tests/paper_radar/test_guards.py
reuse: set PACKAGE and the LAYERS table (layer -> allowed sibling layers); imports are read from the AST so
a lazy import inside a function is caught too. Widening a layer is an edit here, not an import on the way past.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = "cosmai"  # top-level import name
SRC = Path(__file__).resolve().parents[1] / "src" / PACKAGE

# layer (first sub-package) -> the sub-packages it may import. Everything else is a violation.
LAYERS: dict[str, frozenset[str]] = {
    "collectors": frozenset({"contracts"}),
    "analysis": frozenset({"contracts", "db"}),
    "db": frozenset({"contracts"}),
    "contracts": frozenset(),
}


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _layer_of(path: Path) -> str | None:
    rel = path.relative_to(SRC).parts
    return rel[0] if len(rel) > 1 and rel[0] in LAYERS else None


def _imported_layers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").startswith(PACKAGE + "."):
            found.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == PACKAGE and len(parts) > 1:
                    found.add(parts[1])
    return found


LAYERED = [p for p in _modules() if _layer_of(p)]


def test_there_are_modules_to_check():
    # Without this a moved package turns every check below green.
    assert len(LAYERED) >= 5, f"only {len(LAYERED)} layered modules under {SRC}"


@pytest.mark.parametrize("path", LAYERED, ids=lambda p: str(p.relative_to(SRC)))
def test_a_module_imports_only_its_allowed_layers(path: Path):
    layer = _layer_of(path)
    assert layer is not None
    reached = _imported_layers(path) - {layer}
    illegal = sorted(reached - LAYERS[layer])
    assert not illegal, (
        f"{path.relative_to(SRC)} ({layer}) imports {PACKAGE}.{illegal[0]}, which {layer} may not reach. "
        f"Allowed: {sorted(LAYERS[layer])}. Widening the layer is a decision: edit LAYERS here with the reason."
    )
