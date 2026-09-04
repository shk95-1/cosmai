"""A declared edge does not diverge from the real schemas and stages (#141).

Writing the lineage into the contract is worth something only while it matches the code. Three things are
measured here: does the referenced stage exist · is the referenced store a real table · is there a stage cut
off from the picture.

The price of keeping no node table is paid here -- the existence of a store is asked of the DB directly.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from db.seed.pipeline import EDGES, STAGES

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[1]
STAGE_KEYS = {s.stage_key for s in STAGES}


def _stores() -> set[str]:
    return {e.from_key for e in EDGES if e.from_kind == "store"} | {
        e.to_key for e in EDGES if e.to_kind == "store"
    }


DDL_FILES = (
    ("needs", REPO_ROOT / "contracts" / "ddl" / "needs"),
    (None, REPO_ROOT / "contracts" / "ddl" / "current"),  # app.<schema>.sql has its names normalized
)
CREATE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?([A-Za-z_][\w.]*)", re.IGNORECASE)


def _declared_tables() -> set[str]:
    """Every table name the DDL of this checkout declares, with the schema attached."""
    out: set[str] = set()
    for default_schema, directory in DDL_FILES:
        for path in sorted(directory.glob("*.sql")):
            for name in CREATE.findall(path.read_text(encoding="utf-8")):
                out.add(name if "." in name else f"{default_schema}.{name}")
    return out


def _stage_refs() -> set[str]:
    return {e.from_key for e in EDGES if e.from_kind == "stage"} | {
        e.to_key for e in EDGES if e.to_kind == "stage"
    }


def test_every_stage_an_edge_names_is_declared():
    assert _stage_refs() <= STAGE_KEYS, sorted(_stage_refs() - STAGE_KEYS)


def test_no_stage_is_left_out_of_the_graph():
    # A stage cut off from the picture is "a stage nobody knows what feeds it". Some stages only read and
    # never write, such as prune, so the direction is not asked -- one edge is enough.
    assert STAGE_KEYS <= _stage_refs(), sorted(STAGE_KEYS - _stage_refs())


def test_a_store_key_is_a_table_this_checkout_declares():
    """Existence is asked of the **contract**, not of the DB.

    Asking the live DB was the first thought, but three things diverged: the harness does not stand tubedepth
    up whole and stands up jobs alone (tool/checks/test writes down why), and the production DB also holds
    tables the fork made. Asked of either, it measures "is it on that server now" rather than "is it a table
    this checkout knows" -- and judged the latter way, an upstream contract referring to someone else's object
    stays green (the same place as #107 and #150).
    """
    declared = _declared_tables()
    missing = sorted(k for k in _stores() if k not in declared)
    assert not missing, missing


def test_a_store_key_is_schema_qualified():
    # A name whose meaning depends on search_path cannot be a contract.
    assert not [key for key in _stores() if "." not in key]


def test_edges_are_unique():
    pairs = [(e.from_key, e.to_key) for e in EDGES]
    assert len(pairs) == len(set(pairs)), "같은 쌍이 두 번 선언됐다"


def test_no_edge_joins_two_stages():
    # Between two stages there is always the table one of them left. Skipped, the lineage loses its "through
    # what".
    assert not [e for e in EDGES if e.from_kind == "stage" and e.to_kind == "stage"]


def test_the_seeded_rows_match_the_declaration():
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")  # needs_migrator sees needs only through SET ROLE
        rows = {(r[0], r[1]) for r in conn.execute(text("select from_key, to_key from needs.pipeline_edge"))}
    engine.dispose()
    if not rows:
        pytest.skip("시드가 아직 안 돌았다 -- tests/test_seed.py 가 그것을 따로 묻는다")
    assert rows == {(e.from_key, e.to_key) for e in EDGES}
