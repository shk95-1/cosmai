"""선언된 엣지가 실제 스키마·단계와 어긋나지 않는다 (#141).

계보를 계약으로 적는 값은 그것이 코드와 같을 때만 있다. 여기서 재는 것은 셋이다: 참조된 단계가
실재하는가 · 참조된 저장소가 실재하는 표인가 · 그림에서 떨어져 나온 단계가 없는가.

노드 표를 따로 두지 않은 대가를 여기서 치른다 -- 저장소의 실재를 DB 에 직접 묻는다.
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
    (None, REPO_ROOT / "contracts" / "ddl" / "current"),  # app.<schema>.sql 은 이름이 정규화돼 있다
)
CREATE = re.compile(r"CREATE TABLE (?:IF NOT EXISTS )?([A-Za-z_][\w.]*)", re.IGNORECASE)


def _declared_tables() -> set[str]:
    """이 체크아웃의 DDL 이 선언하는 표 이름 전부, 스키마까지 붙여서."""
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
    # 그림에서 떨어져 나온 단계는 "무엇을 먹이는지 아무도 모르는 단계" 다. prune 처럼 쓰기만
    # 없고 읽기만 있는 단계가 있으므로 방향은 묻지 않는다 -- 엣지가 하나라도 있으면 된다.
    assert STAGE_KEYS <= _stage_refs(), sorted(STAGE_KEYS - _stage_refs())


def test_a_store_key_is_a_table_this_checkout_declares():
    """실재는 DB 가 아니라 **계약** 에 묻는다.

    라이브 DB 에 묻는 것이 처음 생각이었지만 셋이 어긋났다: 하네스는 tubedepth 를 통째로 세우지
    않고 jobs 하나만 세우고(tool/checks/test 가 그 이유를 적는다), 운영 DB 에는 포크가 만든 표도
    산다. 어느 쪽에 묻든 "이 체크아웃이 아는 표인가" 가 아니라 "지금 그 서버에 있나" 를 재게 된다 --
    그리고 후자로 판정하면 upstream 계약이 남의 객체를 참조해도 초록이다(#107·#150 과 같은 자리).
    """
    declared = _declared_tables()
    missing = sorted(k for k in _stores() if k not in declared)
    assert not missing, missing


def test_a_store_key_is_schema_qualified():
    # search_path 에 따라 뜻이 달라지는 이름은 계약이 될 수 없다.
    assert not [key for key in _stores() if "." not in key]


def test_edges_are_unique():
    pairs = [(e.from_key, e.to_key) for e in EDGES]
    assert len(pairs) == len(set(pairs)), "같은 쌍이 두 번 선언됐다"


def test_no_edge_joins_two_stages():
    # 단계 사이에는 언제나 그것이 남긴 표가 있다. 건너뛰면 계보가 "무엇을 통해" 를 잃는다.
    assert not [e for e in EDGES if e.from_kind == "stage" and e.to_kind == "stage"]


def test_the_seeded_rows_match_the_declaration():
    url = os.environ.get("TEST_POSTGRES_URL") or pytest.skip("set TEST_POSTGRES_URL, or run tool/checks/test")
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.exec_driver_sql("SET ROLE needs_owner")  # needs_migrator 는 SET ROLE 로만 needs 를 본다
        rows = {(r[0], r[1]) for r in conn.execute(text("select from_key, to_key from needs.pipeline_edge"))}
    engine.dispose()
    if not rows:
        pytest.skip("시드가 아직 안 돌았다 -- tests/test_seed.py 가 그것을 따로 묻는다")
    assert rows == {(e.from_key, e.to_key) for e in EDGES}
