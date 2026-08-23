"""origin: service/trend-radar/tests/test_fixtures_are_scrubbed.py (every committed fixture, not only the review ones)
reuse: list the identifier keys each source sends (profileKey, memberNo, nickname...) in PERSON_KEYS; scrub them to
`SCRUBBED-<n>` when capturing. A ranking page once carried 61 real nicknames -- read every fixture, whatever it is called.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PERSON_KEYS = ("profileKey", "memberNo", "nickname", "userId", "author")
JSON_FIXTURES = sorted(p for p in FIXTURES.rglob("*.json") if p.is_file())


def _values(node: object, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and isinstance(v, str):
                found.append(v)
            found.extend(_values(v, key))
    elif isinstance(node, list):
        for item in node:
            found.extend(_values(item, key))
    return found


def test_there_are_fixtures_to_check():
    assert JSON_FIXTURES


@pytest.mark.parametrize("path", JSON_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURES)))
def test_no_person_identifier_survives(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    leaked = {k: [v for v in _values(data, k) if not v.startswith("SCRUBBED-")][:3] for k in PERSON_KEYS}
    leaked = {k: v for k, v in leaked.items() if v}
    assert not leaked, f"{path.name} carries real identifiers: {leaked}"
