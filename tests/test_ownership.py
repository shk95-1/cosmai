"""The single active owner and migration sequence after the temporary fork handoff."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_only_cosmai_is_an_active_owner():
    text = (ROOT / "contracts/ownership.md").read_text()
    block = re.search(r"```ownership:active-repositories\n(.*?)\n```", text, re.DOTALL)
    assert block is not None
    assert block.group(1).splitlines() == ["shk95-1/cosmai"]
    for retired in ("fork-owned", "must-not-change", "shared-surface"):
        assert f"```ownership:{retired}" not in text


def test_schema_migration_numbers_are_unambiguous():
    for schema in (ROOT / "contracts/ddl").iterdir():
        if not schema.is_dir() or schema.name == "current":
            continue
        numbers = []
        for path in schema.glob("*.sql"):
            match = re.match(r"(\d+)_", path.name)
            assert match, path.name
            numbers.append(int(match.group(1)))
        assert len(numbers) == len(set(numbers)), schema.name
        assert all(number > 0 for number in numbers)


def test_retired_ownership_gate_is_not_a_current_enforcement_site():
    assert not (ROOT / "tool/checks/ownership").exists()
    text = (ROOT / "AGENTS.md").read_text()
    assert "contracts/ownership.md" in text
    assert "tests/test_ownership.py" in text
    assert "tool/checks/ownership" not in text
    assert "fully qualified issue references" in text
