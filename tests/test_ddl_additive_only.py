"""Pre-approval 2 (issue #16): migrations after 001 may only add -- anything else needs a human."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DDL_DIR = Path(__file__).resolve().parents[1] / "contracts" / "ddl" / "needs"
FORBIDDEN = re.compile(
    r"\b(DROP\s+(TABLE|COLUMN|SCHEMA|INDEX|CONSTRAINT)|ALTER\s+COLUMN\s+\S+\s+(SET\s+DATA\s+)?TYPE"
    r"|TRUNCATE|DELETE\s+FROM|UPDATE\s+\S+\s+SET|RENAME\s+(TO|COLUMN)|SET\s+NOT\s+NULL)\b",
    re.IGNORECASE,
)


def _statements(path: Path) -> str:
    return re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(p for p in DDL_DIR.glob("*.sql") if not p.name.startswith("001_")))
def test_later_migrations_are_additive_only(path: Path):
    hits = [m.group(0) for m in FORBIDDEN.finditer(_statements(path))]
    assert not hits, f"{path.name} is not additive-only (needs human approval): {hits}"


def test_the_guard_catches_a_drop(tmp_path: Path):
    bad = tmp_path / "002_x.sql"
    bad.write_text(
        "ALTER TABLE needs.x DROP COLUMN y; -- DROP TABLE in a comment is fine\n", encoding="utf-8"
    )
    assert FORBIDDEN.search(_statements(bad))
    ok = tmp_path / "003_x.sql"
    ok.write_text("ALTER TABLE needs.x ADD COLUMN z int;\nCREATE INDEX ON needs.x (z);\n", encoding="utf-8")
    assert not FORBIDDEN.search(_statements(ok))
