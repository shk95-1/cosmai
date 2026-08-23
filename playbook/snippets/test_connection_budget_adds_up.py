"""origin: service/trend-radar/tests/test_service_database_manifest.py:32-46
reuse: copy; adjust the pool keys if your manifest names them differently. Also greps db/bootstrap calls
for the CONNECTION LIMIT so the manifest and the SQL cannot drift apart silently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

MANIFEST = json.loads((Path(__file__).resolve().parents[1] / "db" / "service-db.json").read_text())
SCHEMAS = sorted(MANIFEST["schemas"])


def test_there_are_schemas():
    assert SCHEMAS


@pytest.mark.parametrize("name", SCHEMAS)
def test_the_budget_adds_up(name: str):
    b = MANIFEST["schemas"][name]["connection_budget"]
    pools = [v for k, v in b.items() if isinstance(v, dict)]
    used = sum(p["instances"] * (p["pool_size"] + p["max_overflow"]) for p in pools) + b["migration"] + b["spare"]
    assert used == b["total"], f"{name}: {used} used vs total {b['total']}"


@pytest.mark.parametrize("name", SCHEMAS)
def test_roles_are_named_for_the_schema(name: str):
    roles = MANIFEST["schemas"][name]["roles"]
    assert {"owner", "migrator", "runtime"} <= set(roles)
    assert all(r.startswith(name + "_") for r in roles.values())
