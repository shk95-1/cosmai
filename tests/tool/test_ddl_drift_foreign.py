"""contracts/ddl/needs/foreign.txt: 운영 needs 에 들어온 남의 DDL 을 선언하는 목록 (#75).

tool/checks/ddl-drift 만 이 파일을 읽지만, 그 검사는 운영 DB 와 docker 를 요구해서 스위트에서
돌 수 없다. 여기서 도는 것은 파일이 지키는 약속 쪽이다 — 형식, 그리고 "여기 적힌 것은 정말 우리
DDL 이 아니다"(우리 것을 적으면 그 객체의 드리프트가 영원히 안 보인다).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_DIR = REPO_ROOT / "contracts" / "ddl" / "needs"
FOREIGN = DDL_DIR / "foreign.txt"
DRIFT = REPO_ROOT / "tool" / "checks" / "ddl-drift"
ENTRY = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")


def entries() -> list[str]:
    """ddl-drift 의 `sed 's/#.*//' | awk 'NF {print $1}'` 과 같은 읽기."""
    out = []
    for line in FOREIGN.read_text(encoding="utf-8").splitlines():
        fields = line.split("#", 1)[0].split()
        if fields:
            out.append(fields[0])
    return out


def declared_tables() -> set[str]:
    tables: set[str] = set()
    for path in sorted(DDL_DIR.glob("*.sql")):
        tables |= set(re.findall(r"CREATE TABLE needs\.(\w+)", path.read_text(encoding="utf-8")))
    return tables


def declared_columns() -> dict[str, set[str]]:
    """CREATE TABLE 본문의 컬럼 + 나중 마이그레이션의 ADD COLUMN. PRIMARY KEY·UNIQUE 같은 제약 줄은
    대문자로 시작해서, 소문자 식별자만 받는 아래 정규식에 애초에 안 걸린다."""
    columns: dict[str, set[str]] = {}
    for path in sorted(DDL_DIR.glob("*.sql")):
        body = path.read_text(encoding="utf-8")
        for table, column in re.findall(r"ALTER TABLE needs\.(\w+)\s+ADD COLUMN (\w+)", body):
            columns.setdefault(table, set()).add(column)
        for table, inner in re.findall(r"CREATE TABLE needs\.(\w+)\s*\((.*?)\n\);", body, re.S):
            for line in inner.splitlines():
                name = re.match(r"([a-z_][a-z0-9_]*)\s", re.sub(r"--.*", "", line).strip())
                if name:
                    columns.setdefault(table, set()).add(name.group(1))
    return columns


def test_the_file_states_the_convention_it_exists_for():
    assert "남의 DDL 이 운영에 들어오면 선언한다" in FOREIGN.read_text(encoding="utf-8")


def test_every_entry_is_a_table_or_a_table_column():
    assert entries(), "빈 목록은 이 파일이 필요 없다는 뜻이다 — 지워라"
    assert [e for e in entries() if not ENTRY.match(e)] == []


def test_the_first_entry_is_the_forks_retrieval_chunk():
    assert entries()[0] == "retrieval_chunk"


@pytest.mark.parametrize("entry", [e for e in entries() if "." not in e])
def test_a_table_entry_names_a_table_this_repo_does_not_declare(entry: str):
    # 우리가 만드는 테이블을 여기 적으면 ddl-drift 가 그 테이블을 영원히 안 본다.
    assert entry not in declared_tables()


@pytest.mark.parametrize("entry", [e for e in entries() if "." in e])
def test_a_column_entry_names_a_column_this_repo_does_not_declare(entry: str):
    # 컬럼 항목은 우리 테이블에 붙어도 된다(포크의 021 이 그렇게 왔다) — 그 컬럼만 남의 것이면 된다.
    table, column = entry.split(".")
    assert column not in declared_columns().get(table, set())


def test_the_column_parser_sees_the_columns_this_repo_does_declare():
    """위 두 검사는 '없음'만 주장한다 — 파서가 아무것도 못 읽어도 통과한다. 여기가 그 구멍을 막는다."""
    assert {"aspect", "pattern", "ruleset", "priority"} <= declared_columns()["aspect_lexicon"]
    assert "extra" in declared_columns()["labeled_set"]


def test_the_two_ddl_guards_do_not_see_it():
    """`.txt` 라서 `*.sql` glob 밖이다 — 확장자를 바꾸면 추가-전용 검사가 이 파일을 SQL 로 읽는다."""
    assert FOREIGN not in set(DDL_DIR.glob("*.sql"))


def test_ddl_drift_reads_the_file():
    assert "contracts/ddl/needs/foreign.txt" in DRIFT.read_text(encoding="utf-8")
