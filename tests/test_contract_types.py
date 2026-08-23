"""Contract test #3: interfaces.md 의 dataclass 정의와 analysis/types.py 는 같은 필드를 갖는다."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTERFACES = ROOT / "contracts" / "interfaces.md"
TYPES = ROOT / "analysis" / "types.py"
CODE_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)

Field = tuple[str, str, str | None]
Method = tuple[str, str, str]


def _dataclass_fields(source: str) -> dict[str, list[Field]]:
    out: dict[str, list[Field]] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any("dataclass" in ast.unparse(d) for d in node.decorator_list):
            continue
        out[node.name] = [
            (s.target.id, ast.unparse(s.annotation), ast.unparse(s.value) if s.value else None)
            for s in node.body
            if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
        ]
    return out


def _protocol_methods(source: str) -> dict[str, list[Method]]:
    """B2 처럼 판정이 시그니처 자체를 바꾼 항목은 Protocol 에 있다 — dataclass 만 보면 갈라져도 모른다."""
    out: dict[str, list[Method]] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(ast.unparse(b) == "Protocol" for b in node.bases):
            continue
        out[node.name] = [
            (f.name, ast.unparse(f.args), ast.unparse(f.returns) if f.returns else "")
            for f in node.body
            if isinstance(f, ast.FunctionDef)
        ]
    return out


def test_the_md_and_the_module_declare_the_same_protocol_signatures():
    md = _protocol_methods("\n".join(CODE_BLOCK.findall(INTERFACES.read_text(encoding="utf-8"))))
    assert md
    assert md == _protocol_methods(TYPES.read_text(encoding="utf-8"))


def test_the_md_and_the_module_declare_the_same_dataclass_fields():
    md = _dataclass_fields("\n".join(CODE_BLOCK.findall(INTERFACES.read_text(encoding="utf-8"))))
    # The md is the contract; an empty parse would make the comparison pass for the wrong reason.
    assert md
    assert md == _dataclass_fields(TYPES.read_text(encoding="utf-8"))
