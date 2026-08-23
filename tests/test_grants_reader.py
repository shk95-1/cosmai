"""Contract test #4: needs_runtime_reader.sql 가 여는 것은 ddl/current 에 실재하고, SELECT 뿐이다."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRANTS = ROOT / "db" / "grants" / "needs_runtime_reader.sql"
DUMPS = ROOT / "contracts" / "ddl" / "current"
GRANTED = re.compile(r"\('([a-z_]+\.[a-z_]+)'\)")
CREATED = re.compile(r"CREATE TABLE (\w+\.\w+)")


def _dumped_tables() -> set[str]:
    return {t for path in DUMPS.glob("*.sql") for t in CREATED.findall(path.read_text(encoding="utf-8"))}


def test_every_granted_table_exists_in_the_current_dumps():
    granted = set(GRANTED.findall(GRANTS.read_text(encoding="utf-8")))
    # T1: 이슈 #2 가 적었던 `tubedepth.videos` 처럼 없는 테이블을 여는 줄을 잡는 것이 이 테스트의 목적이다.
    assert granted
    assert granted <= _dumped_tables(), sorted(granted - _dumped_tables())


def test_the_reader_role_gets_select_and_nothing_else():
    # 주석은 뺀다: 부여하지 않는 이유를 적은 줄이 부여로 읽히면 안 된다.
    body = re.sub(r"--[^\n]*", "", GRANTS.read_text(encoding="utf-8"))
    assert not re.search(r"GRANT\s+(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALL)\b", body, re.IGNORECASE)
    assert "DEFAULT PRIVILEGES" not in body
