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


# 이 파일은 migrate.sh 가 슈퍼유저로 실행한다 — SELECT 아닌 것이 섞이면 막을 것이 아무것도 없다.
FORBIDDEN = re.compile(
    r"\b(GRANT\s+(INSERT|UPDATE|DELETE|TRUNCATE|REFERENCES|TRIGGER|CREATE|CONNECT|TEMP\w*|EXECUTE|ALL)"
    r"|GRANT\s+\w+\s+TO\b"  # 롤 멤버십 부여
    r"|DEFAULT\s+PRIVILEGES|ALTER|CREATE|DROP|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM"
    r"|TRUNCATE|COPY|SET\s+ROLE|REASSIGN)\b",
    re.IGNORECASE,
)


def test_the_reader_role_gets_select_and_nothing_else():
    # 주석은 뺀다: 부여하지 않는 이유를 적은 줄이 부여로 읽히면 안 된다.
    body = re.sub(r"--[^\n]*", "", GRANTS.read_text(encoding="utf-8"))
    hits = [m.group(0) for m in FORBIDDEN.finditer(body)]
    assert not hits, hits


def test_the_guard_catches_a_membership_grant():
    assert FORBIDDEN.search("GRANT needs_owner TO needs_runtime;")
    assert FORBIDDEN.search("ALTER DEFAULT PRIVILEGES IN SCHEMA trend_radar GRANT SELECT ON TABLES TO x;")
    assert not FORBIDDEN.search("GRANT SELECT ON trend_radar.review TO needs_runtime;")
